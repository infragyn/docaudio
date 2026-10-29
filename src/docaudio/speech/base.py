"""Interface comum dos provedores de voz."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class SpeechError(RuntimeError):
    """Falha de sintese com mensagem pronta para o usuario."""


@dataclass(frozen=True)
class VoiceInfo:
    id: str
    label: str
    language: str
    model_path: Path | None = None
    engine: str = "Piper"
    # "feminina", "masculina", "infantil (efeito)"... exibido na lista de vozes.
    kind: str = ""

    @property
    def display_name(self) -> str:
        details = " · ".join(p for p in (self.kind, self.engine) if p)
        return f"{self.label} — {details}" if details else self.label


class SpeechProvider(Protocol):
    name: str
    offline: bool

    def list_voices(self) -> list[VoiceInfo]: ...

    def synthesize_to_wav(
        self,
        text: str,
        voice_id: str,
        speed: float,
        out_path: Path,
        sentence_pause_ms: int = 0,
        trailing_silence_ms: int = 0,
    ) -> Path: ...

    def estimated_cost(self, characters: int) -> float:
        """Custo estimado em reais; zero para provedores offline."""
        ...
