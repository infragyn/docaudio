"""Catalogo unico das vozes offline: Piper, Kokoro e Windows."""

from pathlib import Path

from docaudio.speech.base import VoiceInfo
from docaudio.speech.clone_provider import PREFIX as CLONE_PREFIX
from docaudio.speech.clone_provider import ClonedVoiceProvider
from docaudio.speech.kokoro_provider import PREFIX as KOKORO_PREFIX
from docaudio.speech.kokoro_provider import KokoroProvider
from docaudio.speech.piper_provider import PiperProvider
from docaudio.speech.windows_provider import PREFIX as WINDOWS_PREFIX
from docaudio.speech.windows_provider import WindowsProvider


# Ordem na lista: portugues primeiro, agrupado por tipo de voz.
KIND_ORDER = {"sua voz": -1, "feminina": 0, "feminina (efeito)": 1, "infantil (efeito)": 2, "masculina": 3}


class VoiceRegistry:
    """Encaminha cada voz ao seu mecanismo pelo prefixo do identificador.

    Vozes Piper nao tem prefixo (ex.: pt_BR-faber-medium); as demais usam
    "kokoro:" e "windows:".
    """

    name = "Offline"
    offline = True

    def __init__(self, piper=None, kokoro=None, windows=None, cloned=None) -> None:
        self.piper = piper or PiperProvider()
        self.kokoro = kokoro or KokoroProvider()
        self.windows = windows or WindowsProvider()
        self.cloned = cloned or ClonedVoiceProvider()

    def provider_for(self, voice_id: str):
        if voice_id.startswith(CLONE_PREFIX):
            return self.cloned
        if voice_id.startswith(KOKORO_PREFIX):
            return self.kokoro
        if voice_id.startswith(WINDOWS_PREFIX):
            return self.windows
        return self.piper

    def engine_name(self, voice_id: str) -> str:
        return self.provider_for(voice_id).name

    def list_voices(self) -> list[VoiceInfo]:
        voices = [
            *self.cloned.list_voices(),
            *self.piper.list_voices(),
            *self.kokoro.list_voices(),
            *self.windows.list_voices(),
        ]
        return sorted(
            voices,
            key=lambda v: (not v.language.startswith("pt"), KIND_ORDER.get(v.kind, 9), v.label.lower()),
        )

    def find_voice(self, voice_id: str) -> VoiceInfo | None:
        return self.provider_for(voice_id).find_voice(voice_id)

    def synthesize_to_wav(
        self,
        text: str,
        voice_id: str,
        speed: float,
        out_path: Path,
        sentence_pause_ms: int = 0,
        trailing_silence_ms: int = 0,
    ) -> Path:
        return self.provider_for(voice_id).synthesize_to_wav(
            text, voice_id, speed, out_path, sentence_pause_ms, trailing_silence_ms
        )

    def estimated_cost(self, characters: int) -> float:
        return 0.0
