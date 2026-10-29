"""Sintese offline com Piper."""

import json
import logging
import threading
import wave
from dataclasses import replace
from pathlib import Path

import numpy as np

from docaudio.core import paths
from docaudio.speech.base import SpeechError, VoiceInfo

log = logging.getLogger(__name__)

# Vozes pt-BR do catalogo oficial do Piper (todas masculinas).
MALE_SPEAKERS = {"faber", "cadu", "jeff", "edresson", "tugão"}
# Variantes por mudanca de tom: a fala e gerada mais lenta e depois reamostrada,
# o que sobe tom e formantes sem mudar o ritmo. Soa como efeito, nao como voz real.
VARIANTS = {
    "infantil": ("infantil (efeito)", 1.35),
    "feminina": ("feminina (efeito)", 1.2),
}


def speed_to_length_scale(speed: float) -> float:
    """Piper usa length_scale: valores menores falam mais rapido."""
    if speed <= 0:
        raise ValueError("velocidade deve ser positiva")
    return 1.0 / speed


def silence(sample_rate: int, frame_bytes: int, milliseconds: int) -> bytes:
    return b"\x00" * (sample_rate * milliseconds // 1000 * frame_bytes)


def read_voice_info(model_path: Path) -> VoiceInfo | None:
    """Le o .onnx.json ao lado do modelo; ignora modelos sem configuracao valida."""
    config_path = model_path.with_name(model_path.name + ".json")
    if not config_path.is_file():
        return None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("Configuracao de voz invalida: %s", config_path)
        return None
    voice_id = model_path.stem
    language = (config.get("language") or {}).get("code") or voice_id.split("-")[0]
    parts = voice_id.split("-")
    if len(parts) == 3:
        label = f"{parts[1].capitalize()} ({parts[0]}, {parts[2]})"
        kind = "masculina" if parts[1].lower() in MALE_SPEAKERS else ""
    else:
        label, kind = voice_id, ""
    return VoiceInfo(id=voice_id, label=label, language=language, model_path=model_path, kind=kind)


def variants_of(base: VoiceInfo) -> list[VoiceInfo]:
    """Vozes derivadas por mudanca de tom (so para vozes em portugues de qualidade media ou alta)."""
    if not base.language.startswith("pt") or base.id.endswith(("-low", "-x_low")):
        return []
    name = base.label.split(" (")[0]
    return [
        replace(base, id=f"{base.id}+{key}", label=f"{name} {key}", kind=kind)
        for key, (kind, _factor) in VARIANTS.items()
    ]


def pitch_shift(samples: np.ndarray, factor: float) -> np.ndarray:
    """Reamostra para subir o tom e os formantes por `factor` (encurta o audio na mesma proporcao)."""
    if factor == 1.0 or len(samples) < 2:
        return samples
    positions = np.linspace(0, len(samples) - 1, int(len(samples) / factor))
    return np.interp(positions, np.arange(len(samples)), samples.astype(np.float32)).astype(np.int16)


class PiperProvider:
    name = "Piper"
    offline = True

    def __init__(self, voice_dirs: list[Path] | None = None) -> None:
        self._voice_dirs = voice_dirs
        self._loaded: dict[Path, object] = {}
        self._lock = threading.Lock()

    def list_voices(self) -> list[VoiceInfo]:
        voices: dict[str, VoiceInfo] = {}
        for folder in self._voice_dirs or paths.voice_dirs():
            if not folder.is_dir():
                continue
            for model in sorted(folder.glob("*.onnx")):
                info = read_voice_info(model)
                # A primeira pasta tem prioridade (vozes do usuario sobre as do pacote).
                if info and info.id not in voices:
                    voices[info.id] = info
        bases = sorted(voices.values(), key=lambda v: (not v.language.startswith("pt"), v.id))
        return bases + [variant for base in bases for variant in variants_of(base)]

    def find_voice(self, voice_id: str) -> VoiceInfo | None:
        return next((v for v in self.list_voices() if v.id == voice_id), None)

    def _load(self, model_path: Path):
        with self._lock:
            if model_path not in self._loaded:
                from piper import PiperVoice

                log.info("Carregando voz %s", model_path.name)
                self._loaded[model_path] = PiperVoice.load(model_path)
            return self._loaded[model_path]

    def synthesize_to_wav(
        self,
        text: str,
        voice_id: str,
        speed: float,
        out_path: Path,
        sentence_pause_ms: int = 0,
        trailing_silence_ms: int = 0,
    ) -> Path:
        """Gera WAV com pausa entre frases e silencio final (para separar trechos)."""
        if not text.strip():
            raise SpeechError("Não há texto para narrar.")
        voice = self.find_voice(voice_id)
        if voice is None or voice.model_path is None:
            raise SpeechError(
                f"A voz '{voice_id}' não está instalada. "
                f"Instale com: docaudio baixar-voz {voice_id}"
            )
        from piper import SynthesisConfig

        variant = voice_id.partition("+")[2]
        factor = VARIANTS[variant][1] if variant in VARIANTS else 1.0
        piper_voice = self._load(voice.model_path)
        # Variantes: fala gerada `factor` vezes mais lenta e depois reamostrada.
        config = SynthesisConfig(length_scale=speed_to_length_scale(speed) * factor)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(".part")
        # Piper gera PCM 16 bits mono; o formato e definido antes para que um texto
        # sem nada pronunciavel (ex.: "}") resulte em silencio, nao em WAV invalido.
        sample_rate = piper_voice.config.sample_rate
        frame_bytes = 2
        try:
            with wave.open(str(tmp), "wb") as wav:
                wav.setframerate(sample_rate)
                wav.setsampwidth(2)
                wav.setnchannels(1)
                spoken = False
                for index, chunk in enumerate(piper_voice.synthesize(text, syn_config=config)):
                    if index and sentence_pause_ms:
                        wav.writeframes(silence(sample_rate, frame_bytes, sentence_pause_ms))
                    wav.writeframes(pitch_shift(chunk.audio_int16_array, factor).tobytes())
                    spoken = True
                if not spoken:
                    log.warning("Trecho sem fala, substituído por silêncio: %r", text[:80])
                wav.writeframes(silence(sample_rate, frame_bytes, max(trailing_silence_ms, 0 if spoken else 200)))
            tmp.replace(out_path)
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            raise SpeechError(f"Falha ao sintetizar com Piper: {exc}") from exc
        return out_path

    def estimated_cost(self, characters: int) -> float:
        return 0.0
