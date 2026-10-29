"""Vozes clonadas a partir de uma gravacao do usuario (Chatterbox, ambiente separado).

O Chatterbox exige PyTorch e NumPy < 2, incompativeis com o ambiente principal;
por isso roda em `.venv-clonagem`, num processo auxiliar (clone_worker.py) que
fica aberto enquanto o DocAudio precisar dele.
"""

import atexit
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import unicodedata
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from docaudio.core import paths
from docaudio.speech.base import SpeechError, VoiceInfo
from docaudio.speech.piper_provider import silence
from docaudio.text_processing.segmenter import split_sentences

log = logging.getLogger(__name__)

PREFIX = "clone:"
REFERENCE_FILE = "referencia.wav"
INFO_FILE = "voz.json"
MIN_REFERENCE_SECONDS = 5
MAX_REFERENCE_SECONDS = 30
MODEL_SIZE_GB = 3.5


def voices_dir() -> Path:
    return paths.data_dir() / "vozes_clonadas"


def model_dir() -> Path:
    return paths.data_dir() / "clonagem" / "modelo"


def clone_python() -> Path | None:
    """Python do ambiente de clonagem: variavel DOCAUDIO_CLONE_PYTHON ou .venv-clonagem."""
    override = os.environ.get("DOCAUDIO_CLONE_PYTHON")
    candidates = [Path(override)] if override else []
    for base in (paths.project_root(), paths.data_dir()):
        candidates.append(base / ".venv-clonagem" / "Scripts" / "python.exe")
        candidates.append(base / ".venv-clonagem" / "bin" / "python")
    return next((c for c in candidates if c.is_file()), None)


def worker_script() -> Path:
    if paths.is_frozen():
        return Path(getattr(sys, "_MEIPASS")) / "clone_worker.py"
    return Path(__file__).with_name("clone_worker.py")


def model_ready() -> bool:
    folder = model_dir()
    return all((folder / n).is_file() for n in ("ve.pt", "s3gen.pt", "t3_pt_br.safetensors"))


# ---- trava: so tenta clonar em maquinas que suportam ------------------------

MIN_VRAM_GB = 4.0
PROBE_CODE = (
    "import json, torch\n"
    "ok = torch.cuda.is_available()\n"
    "p = torch.cuda.get_device_properties(0) if ok else None\n"
    "print(json.dumps({'cuda': ok, 'gpu': p.name if p else '', 'vram_gb': round(p.total_memory / 1e9, 1) if p else 0}))"
)
# Desligada pelo usuario (Ferramentas > Permitir clonagem); definida pela interface.
_enabled = True


@dataclass(frozen=True)
class CloneSupport:
    available: bool
    reason: str
    needs_model: bool = False


def set_enabled(enabled: bool) -> None:
    global _enabled
    _enabled = enabled


def capability_file() -> Path:
    return paths.data_dir() / "clonagem" / "capacidade.json"


def _python_signature(python: Path) -> str:
    return f"{python}|{python.stat().st_mtime_ns}"


def cached_capability(python: Path) -> dict | None:
    """Resultado da verificacao anterior, se foi feita com o mesmo ambiente."""
    try:
        data = json.loads(capability_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if data.get("python") == _python_signature(python) else None


def probe_capability(python: Path) -> dict:
    """Verifica a placa de video no ambiente de clonagem (alguns segundos) e guarda o resultado."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        result = subprocess.run(
            [str(python), "-c", PROBE_CODE], capture_output=True, text=True, timeout=180, creationflags=flags
        )
        data = json.loads(result.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as exc:
        data = {"cuda": False, "gpu": "", "vram_gb": 0, "error": str(exc)}
    data["python"] = _python_signature(python)
    data["checked_at"] = datetime.now().isoformat(timespec="seconds")
    capability_file().parent.mkdir(parents=True, exist_ok=True)
    capability_file().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Clonagem: placa %s, %.1f GB, CUDA=%s", data.get("gpu") or "nenhuma", data["vram_gb"], data["cuda"])
    return data


def check_support(probe: bool = False) -> CloneSupport:
    """Se esta maquina pode clonar vozes; sem `probe`, usa apenas o resultado guardado."""
    if not _enabled:
        return CloneSupport(False, "Clonagem desativada nesta máquina (Ferramentas).")
    python = clone_python()
    if python is None:
        return CloneSupport(False, "Ambiente de clonagem não instalado nesta máquina.")
    capability = cached_capability(python) or (probe_capability(python) if probe else None)
    if capability is None:
        return CloneSupport(False, "Verificando se a placa de vídeo suporta clonagem…")
    if os.environ.get("DOCAUDIO_CLONE_ALLOW_CPU") != "1" and (
        not capability["cuda"] or capability["vram_gb"] < MIN_VRAM_GB
    ):
        return CloneSupport(
            False,
            f"Requer placa NVIDIA com {MIN_VRAM_GB:.0f} GB ou mais "
            f"(encontrada: {capability.get('gpu') or 'nenhuma'}). Em processador seria lento demais.",
        )
    if not model_ready():
        return CloneSupport(True, "Modelo de clonagem ainda não baixado.", needs_model=True)
    return CloneSupport(True, f"Pronta ({capability.get('gpu')}).")


def slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "voz"


class CloneWorker:
    """Processo auxiliar persistente; um pedido por vez."""

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._loaded = False
        atexit.register(self.stop)

    def _start(self) -> None:
        python = clone_python()
        if python is None:
            raise SpeechError(
                "Ambiente de clonagem não instalado (.venv-clonagem). Veja \"Vozes clonadas\" no README."
            )
        log_path = paths.logs_dir() / "clonagem.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self._process = subprocess.Popen(
            [str(python), "-u", str(worker_script())],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log_path.open("a", encoding="utf-8"),
            text=True,
            encoding="utf-8",
            creationflags=flags,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "HF_HUB_DISABLE_PROGRESS_BARS": "1",
                 # Sem a barra "Sampling" de cada frase enchendo o clonagem.log.
                 "TQDM_DISABLE": "1"},
        )
        self._read()  # {"event": "started"}
        self._loaded = False

    def _read(self) -> dict:
        line = self._process.stdout.readline()
        if not line:
            code = self._process.poll()
            self._process = None
            raise SpeechError(f"O processo de clonagem foi encerrado (código {code}); veja logs/clonagem.log.")
        return json.loads(line)

    def request(self, payload: dict) -> dict:
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                self._start()
            if payload["cmd"] == "synth" and not self._loaded:
                loaded = self._send({"cmd": "load", "model_dir": str(model_dir())})
                self._loaded = True
                log.info("Modelo de clonagem carregado (%s).", loaded.get("device", "?"))
            return self._send(payload)

    def _send(self, payload: dict) -> dict:
        self._process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._process.stdin.flush()
        response = self._read()
        if not response.get("ok"):
            raise SpeechError(f"Clonagem: {response.get('error', 'erro desconhecido')}")
        return response

    def stop(self) -> None:
        process, self._process = self._process, None
        if process and process.poll() is None:
            try:
                process.stdin.write('{"cmd": "quit"}\n')
                process.stdin.flush()
                process.wait(timeout=10)
            except Exception:
                process.kill()


_worker = CloneWorker()


def shutdown() -> None:
    """Encerra o processo auxiliar (ex.: ao desativar a clonagem)."""
    _worker.stop()


def prepare_model() -> None:
    """Baixa o modelo de clonagem (cerca de 3,5 GB) para a pasta de dados."""
    _worker.request({"cmd": "prepare", "model_dir": str(model_dir())})


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / handle.getframerate()


def create_voice(name: str, source: Path, consent: bool, ffmpeg: Path | None) -> VoiceInfo:
    """Cria uma voz clonada a partir de uma gravacao (WAV, MP3, M4A...).

    `consent` confirma que a voz e do proprio usuario ou de alguem que autorizou.
    """
    if not consent:
        raise ValueError("Confirme que a gravação é da sua voz ou de alguém que autorizou o uso.")
    name = name.strip()
    if not name:
        raise ValueError("Dê um nome para a voz.")
    folder = voices_dir() / slugify(name)
    if folder.exists():
        raise ValueError(f"Já existe uma voz chamada “{name}”.")
    folder.mkdir(parents=True)
    reference = folder / REFERENCE_FILE
    try:
        if ffmpeg:
            # Mono 24 kHz, ate 30 s, sem silencio no inicio.
            command = [
                str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(source),
                "-af", "silenceremove=start_periods=1:start_threshold=-45dB,loudnorm=I=-20",
                "-t", str(MAX_REFERENCE_SECONDS), "-ar", "24000", "-ac", "1", str(reference),
            ]
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            result = subprocess.run(command, capture_output=True, text=True, creationflags=flags)
            if result.returncode != 0:
                raise ValueError(f"Não foi possível ler a gravação: {result.stderr.strip()[-300:]}")
        elif source.suffix.lower() == ".wav":
            shutil.copyfile(source, reference)
        else:
            raise ValueError("Sem o FFmpeg, use uma gravação em WAV.")
        duration = _wav_duration(reference)
        if duration < MIN_REFERENCE_SECONDS:
            raise ValueError(
                f"A gravação tem {duration:.0f} s; grave pelo menos {MIN_REFERENCE_SECONDS} s (ideal: 15 a 30 s)."
            )
        info = {"nome": name, "criada_em": datetime.now().isoformat(timespec="seconds"), "duracao": round(duration, 1)}
        (folder / INFO_FILE).write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    log.info("Voz clonada criada: %s (%.0f s de referência)", name, duration)
    return ClonedVoiceProvider().find_voice(PREFIX + folder.name)


def delete_voice(voice_id: str) -> None:
    folder = voices_dir() / voice_id.removeprefix(PREFIX)
    if folder.is_dir() and folder.parent == voices_dir():
        shutil.rmtree(folder)


class ClonedVoiceProvider:
    name = "Clonada"
    offline = True

    def list_voices(self) -> list[VoiceInfo]:
        voices = []
        folder = voices_dir()
        # Trava: em maquina sem suporte as vozes clonadas nem aparecem.
        support = check_support()
        if not folder.is_dir() or not support.available or support.needs_model:
            return voices
        for voice_folder in sorted(folder.iterdir()):
            info_file, reference = voice_folder / INFO_FILE, voice_folder / REFERENCE_FILE
            if not (info_file.is_file() and reference.is_file()):
                continue
            try:
                info = json.loads(info_file.read_text(encoding="utf-8"))
            except ValueError:
                continue
            voices.append(
                VoiceInfo(
                    id=PREFIX + voice_folder.name,
                    label=info.get("nome", voice_folder.name),
                    language="pt_BR",
                    model_path=reference,
                    engine="Clonada",
                    kind="sua voz",
                )
            )
        return voices

    def find_voice(self, voice_id: str) -> VoiceInfo | None:
        return next((v for v in self.list_voices() if v.id == voice_id), None)

    def synthesize_to_wav(
        self,
        text: str,
        voice_id: str,
        speed: float,
        out_path: Path,
        sentence_pause_ms: int = 0,
        trailing_silence_ms: int = 0,
    ) -> Path:
        """Frase a frase pelo processo auxiliar; velocidade aplicada depois com o FFmpeg."""
        support = check_support()
        if not support.available or support.needs_model:
            raise SpeechError(f"Clonagem indisponível: {support.reason}")
        voice = self.find_voice(voice_id)
        if voice is None:
            raise SpeechError(f"Voz clonada não encontrada: {voice_id}")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        parts = []
        try:
            for index, sentence in enumerate(split_sentences(text) or [text]):
                part = out_path.with_name(f"{out_path.stem}.f{index}.wav")
                _worker.request({"cmd": "synth", "text": sentence, "ref": str(voice.model_path), "out": str(part)})
                parts.append(part)
            joined = out_path.with_suffix(".part")
            with wave.open(str(joined), "wb") as out:
                for index, part in enumerate(parts):
                    with wave.open(str(part), "rb") as source:
                        if index == 0:
                            out.setparams(source.getparams())
                        elif sentence_pause_ms:
                            out.writeframes(silence(source.getframerate(), 2, sentence_pause_ms))
                        out.writeframes(source.readframes(source.getnframes()))
                out.writeframes(silence(out.getframerate(), 2, trailing_silence_ms))
            _apply_speed(joined, out_path, speed)
        finally:
            for part in parts:
                part.unlink(missing_ok=True)
            out_path.with_suffix(".part").unlink(missing_ok=True)
        return out_path

    def estimated_cost(self, characters: int) -> float:
        return 0.0


def _apply_speed(source: Path, target: Path, speed: float) -> None:
    from docaudio.core.dependencies import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if abs(speed - 1.0) < 0.01 or ffmpeg is None:
        shutil.copyfile(source, target)
        return
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    result = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(source),
         "-af", f"atempo={speed:.2f}", "-c:a", "pcm_s16le", str(target)],
        capture_output=True, text=True, creationflags=flags,
    )
    if result.returncode != 0:
        raise SpeechError(f"Falha ao ajustar a velocidade: {result.stderr.strip()[-200:]}")
