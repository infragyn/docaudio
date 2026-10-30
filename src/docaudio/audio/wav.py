"""Uniao de arquivos WAV (com reamostragem quando as vozes usam taxas diferentes)."""

import wave
from pathlib import Path

import numpy as np


class AudioError(RuntimeError):
    """Falha de processamento de audio com mensagem pronta para o usuario."""


def duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / wav.getframerate()


def _resample(frames: bytes, channels: int, rate: int, target_rate: int) -> bytes:
    """Interpolacao linear de PCM 16 bits: suficiente para fala (Piper 22 kHz + Kokoro 24 kHz)."""
    samples = np.frombuffer(frames, dtype=np.int16).reshape(-1, channels).astype(np.float32)
    count = int(round(len(samples) * target_rate / rate))
    if not len(samples) or not count:
        return b""
    positions = np.linspace(0, len(samples) - 1, count)
    resampled = np.stack([np.interp(positions, np.arange(len(samples)), samples[:, c]) for c in range(channels)], 1)
    return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()


def concat_wavs(inputs: list[Path], output: Path, trailing_silence_ms: int = 0) -> Path:
    """Concatena WAVs; o silencio final separa capitulos.

    Trechos de vozes com outra taxa de amostragem (modo bilingue com dois
    mecanismos) sao convertidos para a taxa do primeiro.
    """
    if not inputs:
        raise AudioError("Nenhum trecho de áudio para unir.")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(".part")
    params = None
    try:
        with wave.open(str(tmp), "wb") as out:
            for path in inputs:
                with wave.open(str(path), "rb") as source:
                    current = (source.getnchannels(), source.getsampwidth(), source.getframerate())
                    frames = source.readframes(source.getnframes())
                    if params is None:
                        params = current
                        out.setnchannels(current[0])
                        out.setsampwidth(current[1])
                        out.setframerate(current[2])
                    elif current != params:
                        if current[:2] != params[:2] or current[1] != 2:
                            raise AudioError(f"Formato de áudio diferente em {path.name}.")
                        frames = _resample(frames, current[0], current[2], params[2])
                    out.writeframes(frames)
            if trailing_silence_ms and params:
                channels, width, rate = params
                out.writeframes(b"\x00" * (rate * trailing_silence_ms // 1000 * channels * width))
        tmp.replace(output)
    finally:
        tmp.unlink(missing_ok=True)
    return output
