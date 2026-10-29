"""Vozes instaladas no Windows (SAPI), como a Maria em pt-BR."""

import logging
import math
import sys
import threading
import wave
from pathlib import Path

from docaudio.speech.base import SpeechError, VoiceInfo
from docaudio.speech.piper_provider import silence

log = logging.getLogger(__name__)

PREFIX = "windows:"
SAMPLE_RATE = 22050
FORMAT_22KHZ_16BIT_MONO = 22  # SpeechAudioFormatType.SAFT22kHz16BitMono
CREATE_FOR_WRITE = 3  # SpeechStreamFileMode.SSFMCreateForWrite
# Codigos de idioma (LCID em hexadecimal) do portugues.
PORTUGUESE_LCIDS = {"416": "pt_BR", "816": "pt_PT"}
GENDERS = {"female": "feminina", "male": "masculina"}


def speed_to_rate(speed: float) -> int:
    """SAPI usa Rate de -10 a 10; +10 e cerca de 3x mais rapido."""
    return max(-10, min(10, round(10 * math.log(speed) / math.log(3))))


class WindowsProvider:
    name = "Windows"
    offline = True

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._voices: list[VoiceInfo] | None = None

    def _tokens(self):
        import comtypes
        import comtypes.client

        comtypes.CoInitialize()
        voice = comtypes.client.CreateObject("SAPI.SpVoice")
        tokens = voice.GetVoices()
        return voice, [tokens.Item(i) for i in range(tokens.Count)]

    def list_voices(self) -> list[VoiceInfo]:
        if sys.platform != "win32":
            return []
        if self._voices is None:
            voices = []
            try:
                _, tokens = self._tokens()
                for token in tokens:
                    lcid = (token.GetAttribute("Language") or "").split(";")[0].lower()
                    if lcid not in PORTUGUESE_LCIDS:
                        continue
                    name = token.GetAttribute("Name") or token.GetDescription()
                    short = name.replace("Microsoft ", "").replace(" Desktop", "")
                    voices.append(
                        VoiceInfo(
                            id=PREFIX + name,
                            label=f"{short} ({PORTUGUESE_LCIDS[lcid]})",
                            language=PORTUGUESE_LCIDS[lcid],
                            engine="Windows",
                            kind=GENDERS.get((token.GetAttribute("Gender") or "").lower(), ""),
                        )
                    )
            except Exception as exc:
                log.warning("Vozes do Windows indisponíveis: %s", exc)
            self._voices = voices
        return self._voices

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
        """Gera WAV 22 kHz mono; a pausa entre frases fica a cargo do proprio SAPI."""
        import comtypes.client

        name = voice_id.removeprefix(PREFIX)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        raw = out_path.with_suffix(".sapi.wav")
        tmp = out_path.with_suffix(".part")
        try:
            with self._lock:
                voice, tokens = self._tokens()
                token = next((t for t in tokens if (t.GetAttribute("Name") or "") == name), None)
                if token is None:
                    raise SpeechError(f"A voz do Windows '{name}' não está instalada.")
                voice.Voice = token
                voice.Rate = speed_to_rate(speed)
                audio_format = comtypes.client.CreateObject("SAPI.SpAudioFormat")
                audio_format.Type = FORMAT_22KHZ_16BIT_MONO
                stream = comtypes.client.CreateObject("SAPI.SpFileStream")
                stream.Format = audio_format
                stream.Open(str(raw), CREATE_FOR_WRITE)
                voice.AudioOutputStream = stream
                voice.Speak(text, 0)
                stream.Close()
            with wave.open(str(raw), "rb") as source, wave.open(str(tmp), "wb") as out:
                out.setparams(source.getparams())
                out.writeframes(source.readframes(source.getnframes()))
                frame_bytes = source.getsampwidth() * source.getnchannels()
                out.writeframes(silence(source.getframerate(), frame_bytes, trailing_silence_ms))
            tmp.replace(out_path)
        except SpeechError:
            raise
        except Exception as exc:
            raise SpeechError(f"Falha ao sintetizar com a voz do Windows: {exc}") from exc
        finally:
            raw.unlink(missing_ok=True)
            tmp.unlink(missing_ok=True)
        return out_path

    def estimated_cost(self, characters: int) -> float:
        return 0.0
