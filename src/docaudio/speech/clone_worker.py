"""Processo auxiliar de clonagem de voz (Chatterbox Multilingual, pt-BR).

Roda no ambiente separado `.venv-clonagem` (PyTorch, NumPy < 2), que nao e
compativel com o ambiente principal. Usa so a biblioteca padrao alem do
Chatterbox: nao importa nada de `docaudio`.

Protocolo: uma linha JSON por pedido em stdin, uma linha JSON por resposta em
stdout. Mensagens de bibliotecas vao para stderr.

    {"cmd": "prepare", "model_dir": "..."}                      baixa o modelo
    {"cmd": "load", "model_dir": "..."}                         carrega na GPU
    {"cmd": "synth", "text": "...", "ref": "x.wav", "out": "y.wav",
     "exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.8}
    {"cmd": "quit"}
"""

import json
import os
import shutil
import sys
import traceback
import wave
from pathlib import Path

BASE_REPO = "ResembleAI/chatterbox"
PT_BR_REPO = "ResembleAI/Chatterbox-Multilingual-pt-br"
BASE_FILES = ["ve.pt", "conds.pt", "Cangjie5_TC.json"]
PT_BR_FILES = ["t3_pt_br.safetensors", "s3gen_v3.pt", "grapheme_mtl_merged_expanded_v1.json"]
T3_FILE = "t3_pt_br.safetensors"

_model = None
_autocast_dtype = None
# Referencia cujas condicoes ja estao no modelo: (caminho, data de modificacao, exagero).
_conditioned: tuple | None = None
# Saida do protocolo; definida em main() antes de redirecionar stdout para stderr.
_protocol = sys.stdout


def reply(**payload) -> None:
    _protocol.write(json.dumps(payload, ensure_ascii=False) + "\n")
    _protocol.flush()


def model_ready(model_dir: Path) -> bool:
    return all((model_dir / name).is_file() for name in ["ve.pt", "s3gen.pt", T3_FILE, PT_BR_FILES[2]])


def prepare(model_dir: Path) -> None:
    """Monta a pasta do modelo: arquivos comuns do repositorio base + pacote pt-BR."""
    from huggingface_hub import hf_hub_download

    model_dir.mkdir(parents=True, exist_ok=True)
    for repo, names in ((BASE_REPO, BASE_FILES), (PT_BR_REPO, PT_BR_FILES)):
        for name in names:
            if not (model_dir / name).is_file():
                hf_hub_download(repo_id=repo, filename=name, local_dir=str(model_dir))
    # O carregador espera "s3gen.pt"; o pacote pt-BR traz o decodificador v3.
    # Link fisico evita duplicar ~1 GB; copia se o sistema de arquivos nao suportar.
    target = model_dir / "s3gen.pt"
    if not target.is_file():
        try:
            os.link(model_dir / "s3gen_v3.pt", target)
        except OSError:
            shutil.copyfile(model_dir / "s3gen_v3.pt", target)


def load(model_dir: Path) -> str:
    global _model, _conditioned
    _conditioned = None
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    import inspect

    global _autocast_dtype
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if "t3_model" in inspect.signature(ChatterboxMultilingualTTS.from_local).parameters:
        _model = ChatterboxMultilingualTTS.from_local(model_dir, device, t3_model=T3_FILE)
    else:
        _model = _load_with_legacy_library(model_dir, device)
    # bf16 deixa a geracao ~30% mais rapida na GPU sem mudar a duracao da fala (medido
    # numa RTX 4050); DOCAUDIO_CLONE_PRECISION=fp32 volta a precisao total.
    _autocast_dtype = None
    if device == "cuda" and os.environ.get("DOCAUDIO_CLONE_PRECISION", "bf16") == "bf16":
        if torch.cuda.is_bf16_supported():
            _autocast_dtype = torch.bfloat16
    return device + (" (bf16)" if _autocast_dtype else "")


# Buffers que a biblioteca recalcula ao criar o decodificador; o checkpoint v3 nao os traz.
RECOMPUTED_BUFFERS = {"tokenizer._mel_filters", "tokenizer.window"}


def _load_with_legacy_library(model_dir: Path, device: str):
    """Carrega o pacote pt-BR (V3) com a biblioteca do PyPI (0.1.x).

    Igual a ChatterboxMultilingualTTS.from_local, mas le o T3 pt-BR e aceita
    que o decodificador V3 nao traga os dois buffers recalculados; qualquer
    outra diferenca de pesos continua sendo erro.
    """
    import torch
    from chatterbox.models.s3gen import S3Gen
    from chatterbox.models.t3 import T3
    from chatterbox.models.t3.modules.t3_config import T3Config
    from chatterbox.models.tokenizers import MTLTokenizer
    from chatterbox.models.voice_encoder import VoiceEncoder
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS, Conditionals
    from safetensors.torch import load_file

    map_location = torch.device("cpu") if device == "cpu" else None
    ve = VoiceEncoder()
    ve.load_state_dict(torch.load(model_dir / "ve.pt", map_location=map_location, weights_only=True))
    ve.to(device).eval()

    t3 = T3(T3Config.multilingual())
    t3_state = load_file(model_dir / T3_FILE)
    if "model" in t3_state.keys():
        t3_state = t3_state["model"][0]
    t3.load_state_dict(t3_state)
    t3.to(device).eval()

    s3gen = S3Gen()
    result = s3gen.load_state_dict(
        torch.load(model_dir / "s3gen.pt", map_location=map_location, weights_only=True), strict=False
    )
    unexpected_missing = set(result.missing_keys) - RECOMPUTED_BUFFERS
    if unexpected_missing or result.unexpected_keys:
        raise RuntimeError(
            f"Decodificador incompatível: faltando {sorted(unexpected_missing)[:5]}, "
            f"sobrando {sorted(result.unexpected_keys)[:5]}"
        )
    s3gen.to(device).eval()

    tokenizer = MTLTokenizer(str(model_dir / "grapheme_mtl_merged_expanded_v1.json"))
    conds = None
    if (builtin := model_dir / "conds.pt").exists():
        conds = Conditionals.load(builtin, map_location=map_location).to(device)
    return ChatterboxMultilingualTTS(t3, s3gen, ve, tokenizer, device, conds=conds)


def _condition(ref: str, exaggeration: float) -> None:
    """Processa a gravacao de referencia so quando ela muda, nao a cada frase."""
    global _conditioned
    key = (ref, Path(ref).stat().st_mtime_ns, exaggeration)
    if _conditioned != key:
        _model.prepare_conditionals(ref, exaggeration=exaggeration)
        _conditioned = key


def synth(request: dict) -> int:
    import contextlib

    import numpy as np
    import torch

    exaggeration = float(request.get("exaggeration", 0.5))
    _condition(request["ref"], exaggeration)
    precision = torch.autocast("cuda", dtype=_autocast_dtype) if _autocast_dtype else contextlib.nullcontext()
    with precision:
        wav = _model.generate(
            request["text"],
            language_id="pt",
            exaggeration=exaggeration,
            cfg_weight=float(request.get("cfg_weight", 0.5)),
            temperature=float(request.get("temperature", 0.8)),
        )
    samples = wav.float().squeeze().detach().cpu().numpy()
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    out = Path(request["out"])
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(_model.sr)
        handle.writeframes(pcm.tobytes())
    return _model.sr


def main() -> int:
    global _protocol
    # Bibliotecas escrevem avisos em stdout; o protocolo usa o stdout original.
    _protocol = sys.stdout
    sys.stdout = sys.stderr
    reply(event="started")
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            command = request.get("cmd")
            if command == "quit":
                reply(ok=True)
                return 0
            if command == "prepare":
                prepare(Path(request["model_dir"]))
                reply(ok=True)
            elif command == "load":
                model_dir = Path(request["model_dir"])
                if not model_ready(model_dir):
                    reply(ok=False, error="Modelo de clonagem não baixado.")
                    continue
                reply(ok=True, device=load(model_dir))
            elif command == "synth":
                if _model is None:
                    reply(ok=False, error="Modelo de clonagem não carregado.")
                    continue
                reply(ok=True, sample_rate=synth(request))
            else:
                reply(ok=False, error=f"Comando desconhecido: {command}")
        except Exception as exc:
            traceback.print_exc()
            reply(ok=False, error=f"{type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
