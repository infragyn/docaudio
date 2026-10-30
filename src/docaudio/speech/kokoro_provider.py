"""Vozes Kokoro (modelo aberto, Apache 2.0) em portugues, ingles e espanhol.

O mesmo modelo (voices-v1.0.bin) traz vozes de varios idiomas; a primeira letra
do nome indica o idioma: p = portugues, a = ingles americano, b = ingles
britanico, e = espanhol.
"""

import logging
import threading
import wave
from pathlib import Path

import numpy as np

from docaudio.core import paths
from docaudio.speech.base import SpeechError, VoiceInfo
from docaudio.speech.piper_provider import silence
from docaudio.text_processing.segmenter import split_sentences

log = logging.getLogger(__name__)

PREFIX = "kokoro:"
MODEL_FILE = "kokoro-v1.0.int8.onnx"
VOICES_FILE = "voices-v1.0.bin"
# Prefixo da voz -> (codigo do espeak-ng usado pela Kokoro, idioma no padrao do DocAudio)
LANGUAGE_BY_PREFIX = {"p": ("pt-br", "pt_BR"), "a": ("en-us", "en_US"), "b": ("en-gb", "en_GB"), "e": ("es", "es_ES")}
LANGUAGE = LANGUAGE_BY_PREFIX["p"][0]
# Selecao das vozes mais naturais de cada idioma (o modelo tem 54).
VOICES = {
    "pf_dora": ("Dora", "feminina"),
    "pm_alex": ("Alex", "masculina"),
    "pm_santa": ("Santa", "masculina"),
    "af_heart": ("Heart", "feminina"),
    "af_bella": ("Bella", "feminina"),
    "af_nicole": ("Nicole", "feminina"),
    "af_sarah": ("Sarah", "feminina"),
    "am_michael": ("Michael", "masculina"),
    "am_adam": ("Adam", "masculina"),
    "am_eric": ("Eric", "masculina"),
    "bf_emma": ("Emma", "feminina"),
    "bf_isabella": ("Isabella", "feminina"),
    "bm_george": ("George", "masculina"),
    "bm_lewis": ("Lewis", "masculina"),
    "ef_dora": ("Dora", "feminina"),
    "em_alex": ("Alex", "masculina"),
    "em_santa": ("Santa", "masculina"),
}


def voice_language(key: str) -> tuple[str, str]:
    return LANGUAGE_BY_PREFIX.get(key[:1], LANGUAGE_BY_PREFIX["p"])


def model_dirs() -> list[Path]:
    return [folder / "kokoro" for folder in paths.voice_dirs()]


def find_model_dir() -> Path | None:
    return next(
        (d for d in model_dirs() if (d / MODEL_FILE).is_file() and (d / VOICES_FILE).is_file()), None
    )


class KokoroProvider:
    name = "Kokoro"
    offline = True

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model = None

    def list_voices(self) -> list[VoiceInfo]:
        folder = find_model_dir()
        if folder is None:
            return []
        return [
            VoiceInfo(
                id=PREFIX + key,
                label=f"{name} ({voice_language(key)[1]})",
                language=voice_language(key)[1],
                model_path=folder / MODEL_FILE,
                engine="Kokoro",
                kind=kind,
            )
            for key, (name, kind) in VOICES.items()
        ]

    def find_voice(self, voice_id: str) -> VoiceInfo | None:
        return next((v for v in self.list_voices() if v.id == voice_id), None)

    def _load(self):
        if self._model is None:
            folder = find_model_dir()
            if folder is None:
                raise SpeechError("Modelo Kokoro não instalado. Instale com: docaudio baixar-voz kokoro")
            from kokoro_onnx import Kokoro

            log.info("Carregando modelo Kokoro")
            self._model = Kokoro(str(folder / MODEL_FILE), str(folder / VOICES_FILE))
        return self._model

    def synthesize_to_wav(
        self,
        text: str,
        voice_id: str,
        speed: float,
        out_path: Path,
        sentence_pause_ms: int = 0,
        trailing_silence_ms: int = 0,
    ) -> Path:
        """Sintetiza frase a frase (para controlar a pausa) em PCM 16 bits, 24 kHz."""
        key = voice_id.removeprefix(PREFIX)
        if key not in VOICES:
            raise SpeechError(f"Voz Kokoro desconhecida: {key}")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(".part")
        try:
            with self._lock:
                model = self._load()
                parts: list[np.ndarray] = []
                rate = 24000
                for sentence in split_sentences(text) or [text]:
                    samples, rate = model.create(sentence, voice=key, speed=speed, lang=voice_language(key)[0])
                    parts.append((np.clip(samples, -1, 1) * 32767).astype(np.int16))
            with wave.open(str(tmp), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(rate)
                for index, part in enumerate(parts):
                    if index and sentence_pause_ms:
                        wav.writeframes(silence(rate, 2, sentence_pause_ms))
                    wav.writeframes(part.tobytes())
                wav.writeframes(silence(rate, 2, trailing_silence_ms))
            tmp.replace(out_path)
        except SpeechError:
            raise
        except Exception as exc:
            raise SpeechError(f"Falha ao sintetizar com Kokoro: {exc}") from exc
        finally:
            tmp.unlink(missing_ok=True)
        return out_path

    def estimated_cost(self, characters: int) -> float:
        return 0.0
