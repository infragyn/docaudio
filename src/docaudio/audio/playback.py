"""Reproducao com posicao, pausa e busca (para o player do audiolivro).

O PySide6-Essentials nao traz o QtMultimedia; o audio sai pelo sounddevice.
MP3 e M4B sao decodificados pelo FFmpeg para um WAV temporario por capitulo.
"""

import logging
import subprocess
import sys
import threading
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

PLAYBACK_RATE = 24000


class PlaybackError(RuntimeError):
    pass


def decode_to_wav(
    ffmpeg: Path | None, source: Path, target: Path, start_s: float = 0.0, duration_s: float | None = None
) -> Path:
    """Trecho de `source` (inteiro ou a partir de `start_s`) em WAV mono."""
    if source.suffix.lower() == ".wav" and not start_s and duration_s is None:
        return source
    if ffmpeg is None:
        raise PlaybackError("O FFmpeg é necessário para tocar MP3 e M4B.")
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if start_s:
        command += ["-ss", f"{start_s:.3f}"]
    command += ["-i", str(source)]
    if duration_s is not None:
        command += ["-t", f"{duration_s:.3f}"]
    command += ["-ac", "1", "-ar", str(PLAYBACK_RATE), "-c:a", "pcm_s16le", str(target)]
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    result = subprocess.run(command, capture_output=True, text=True, creationflags=flags)
    if result.returncode != 0:
        raise PlaybackError(f"Não foi possível abrir o áudio: {result.stderr.strip()[-200:]}")
    return target


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Amostras int16 (frames x canais) e taxa de amostragem."""
    with wave.open(str(path), "rb") as handle:
        if handle.getsampwidth() != 2:
            raise PlaybackError("Formato de WAV não suportado (use 16 bits).")
        channels, rate = handle.getnchannels(), handle.getframerate()
        data = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)
    return data.reshape(-1, channels), rate


class Player:
    """Toca um bloco de amostras em memoria; posicao em segundos."""

    def __init__(self) -> None:
        self._data = np.zeros((0, 1), dtype=np.int16)
        self._rate = PLAYBACK_RATE
        self._frame = 0
        self._stream = None
        self._lock = threading.Lock()
        self.finished = False

    def load(self, samples: np.ndarray, rate: int) -> None:
        self.stop()
        with self._lock:
            self._data, self._rate, self._frame = samples, rate, 0
            self.finished = False

    @property
    def duration(self) -> float:
        return len(self._data) / self._rate

    @property
    def position(self) -> float:
        return self._frame / self._rate

    @property
    def playing(self) -> bool:
        return self._stream is not None and self._stream.active

    def seek(self, seconds: float) -> None:
        with self._lock:
            self._frame = int(min(max(seconds, 0.0), self.duration) * self._rate)
            self.finished = False

    def _callback(self, out, frames, _time, _status) -> None:
        with self._lock:
            chunk = self._data[self._frame:self._frame + frames]
            out[: len(chunk)] = chunk
            out[len(chunk):] = 0
            self._frame += len(chunk)
            if len(chunk) < frames:
                self.finished = True
                import sounddevice

                raise sounddevice.CallbackStop

    def play(self) -> None:
        import sounddevice

        if self.playing or not len(self._data):
            return
        if self._frame >= len(self._data):
            self._frame = 0
        self.finished = False
        self._close()
        try:
            self._stream = sounddevice.OutputStream(
                samplerate=self._rate, channels=self._data.shape[1], dtype="int16", callback=self._callback
            )
            self._stream.start()
        except Exception as exc:  # dispositivo de audio ausente ou ocupado
            self._stream = None
            raise PlaybackError(f"Não foi possível tocar o áudio: {exc}") from exc

    def pause(self) -> None:
        self._close()

    def stop(self) -> None:
        self._close()
        with self._lock:
            self._frame = 0

    def _close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.debug("Falha ao fechar o fluxo de áudio", exc_info=True)
