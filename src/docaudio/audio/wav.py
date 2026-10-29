"""Uniao de arquivos WAV sem reamostragem."""

import wave
from pathlib import Path


class AudioError(RuntimeError):
    """Falha de processamento de audio com mensagem pronta para o usuario."""


def duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / wav.getframerate()


def concat_wavs(inputs: list[Path], output: Path, trailing_silence_ms: int = 0) -> Path:
    """Concatena WAVs de mesmo formato; o silencio final separa capitulos."""
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
                    if params is None:
                        params = current
                        out.setnchannels(current[0])
                        out.setsampwidth(current[1])
                        out.setframerate(current[2])
                    elif current != params:
                        raise AudioError(f"Formato de áudio diferente em {path.name}.")
                    out.writeframes(source.readframes(source.getnframes()))
            if trailing_silence_ms and params:
                channels, width, rate = params
                out.writeframes(b"\x00" * (rate * trailing_silence_ms // 1000 * channels * width))
        tmp.replace(output)
    finally:
        tmp.unlink(missing_ok=True)
    return output
