"""Player por streaming: toca qualquer formato de audio, de qualquer tamanho, em qualquer velocidade.

Os decodificadores vem do PyAV (FFmpeg embutido, licenca LGPL): nada a instalar.
Uma thread decodifica aos poucos e o `sounddevice` consome, entao um audiolivro
de dez horas nao vai para a memoria. A velocidade usa o filtro atempo, que
mantem o tom da voz.
"""

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

import numpy as np

from docaudio.audio.playback import PlaybackError

log = logging.getLogger(__name__)

RATE = 44100
CHANNELS = 2
# Audio decodificado a frente da reproducao, em amostras.
MAX_BUFFER = RATE * 4
AUDIO_EXTENSIONS = (
    ".mp3", ".m4a", ".m4b", ".aac", ".wav", ".flac", ".ogg", ".oga", ".opus", ".wma", ".aiff", ".aif",
    ".alac", ".ac3", ".ape", ".wv", ".amr", ".mka", ".mp4", ".webm",
)
FILE_DIALOG_FILTER = (
    "Áudio e audiolivros (" + " ".join(f"*{e}" for e in AUDIO_EXTENSIONS) + ");;Todos os arquivos (*)"
)


@dataclass(frozen=True)
class AudioInfo:
    path: Path
    title: str
    duration: float
    # (titulo, inicio em segundos)
    chapters: list[tuple[str, float]] = field(default_factory=list)


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


def probe(path: Path) -> AudioInfo:
    """Duracao, titulo e capitulos do arquivo."""
    import av

    try:
        with av.open(str(path)) as container:
            if not container.streams.audio:
                raise PlaybackError(f"{path.name} não tem áudio.")
            stream = container.streams.audio[0]
            if container.duration:
                duration = container.duration / av.time_base
            elif stream.duration and stream.time_base:
                duration = float(stream.duration * stream.time_base)
            else:
                duration = 0.0
            chapters = [
                (c["metadata"].get("title") or f"Capítulo {i}", float(c["start"] * c["time_base"]))
                for i, c in enumerate(container.chapters(), 1)
            ]
            return AudioInfo(path, container.metadata.get("title") or path.stem, duration, chapters)
    except (av.FFmpegError, OSError) as exc:
        raise PlaybackError(f"Não foi possível abrir {path.name}: {exc}") from exc


class _Tempo:
    """Muda a velocidade sem mudar o tom."""

    def __init__(self, speed: float) -> None:
        import av

        self._empty = (av.BlockingIOError, av.EOFError)
        self._graph = av.filter.Graph()
        source = self._graph.add_abuffer(
            format="s16", sample_rate=RATE, layout="stereo", time_base=Fraction(1, RATE)
        )
        tempo = self._graph.add("atempo", f"{speed:.3f}")
        sink = self._graph.add("abuffersink")
        source.link_to(tempo)
        tempo.link_to(sink)
        self._graph.configure()
        self._pts = 0

    def _pull(self) -> list[np.ndarray]:
        chunks = []
        while True:
            try:
                chunks.append(self._graph.pull().to_ndarray().reshape(-1, CHANNELS))
            except self._empty:
                return chunks

    def process(self, samples: np.ndarray) -> list[np.ndarray]:
        import av

        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="stereo")
        frame.sample_rate = RATE
        frame.time_base = Fraction(1, RATE)
        frame.pts = self._pts
        self._pts += len(samples)
        self._graph.push(frame)
        return self._pull()

    def flush(self) -> list[np.ndarray]:
        self._graph.push(None)
        return self._pull()


class StreamPlayer:
    """Um arquivo por vez. `position` e sempre o tempo no arquivo, em qualquer velocidade."""

    def __init__(self) -> None:
        self._lock = threading.Condition()
        self._chunks: deque[np.ndarray] = deque()
        self._offset = 0
        self._buffered = 0
        self._generation = 0
        self._eof = False
        self._base = 0.0
        self._played = 0
        self._stream = None
        self.info: AudioInfo | None = None
        self.speed = 1.0
        self.finished = False
        self.error = ""

    # ---- estado ---------------------------------------------------------------------

    @property
    def duration(self) -> float:
        return self.info.duration if self.info else 0.0

    @property
    def position(self) -> float:
        position = self._base + self._played / RATE * self.speed
        return min(position, self.duration) if self.duration else position

    @property
    def playing(self) -> bool:
        return self._stream is not None and self._stream.active

    # ---- comandos -------------------------------------------------------------------

    def open(self, path: Path, start: float = 0.0) -> AudioInfo:
        info = probe(path)
        self.pause()
        self.info = info
        # Posicao lembrada alem do fim (o arquivo mudou): comeca do inicio.
        self._restart(start if not info.duration or start < info.duration else 0.0)
        return info

    def seek(self, seconds: float) -> None:
        if self.info:
            limit = self.duration if self.duration else seconds
            self._restart(min(max(seconds, 0.0), limit))

    def set_speed(self, speed: float) -> None:
        """Continua do mesmo ponto na nova velocidade."""
        position = self.position
        self.speed = speed
        if self.info:
            self._restart(position)

    def play(self) -> None:
        import sounddevice

        if self.playing or not self.info:
            return
        if self.finished:
            self._restart(0.0)
        self._close()
        self._stop_signal = sounddevice.CallbackStop
        try:
            self._stream = sounddevice.OutputStream(
                samplerate=RATE, channels=CHANNELS, dtype="int16", callback=self._callback
            )
            self._stream.start()
        except Exception as exc:  # dispositivo de audio ausente ou ocupado
            self._stream = None
            raise PlaybackError(f"Não foi possível tocar o áudio: {exc}") from exc

    def pause(self) -> None:
        self._close()

    def shutdown(self) -> None:
        self._close()
        with self._lock:
            self._generation += 1
            self._lock.notify_all()
        self.info = None

    def _close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.debug("Falha ao fechar o fluxo de áudio", exc_info=True)

    # ---- decodificacao ---------------------------------------------------------------

    def _restart(self, start: float) -> None:
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._chunks.clear()
            self._offset = self._buffered = self._played = 0
            self._base = start
            self._eof = self.finished = False
            self.error = ""
            self._lock.notify_all()
        threading.Thread(
            target=self._decode, args=(self.info.path, start, self.speed, generation), daemon=True
        ).start()

    def _push(self, samples: np.ndarray, generation: int) -> bool:
        """Entrega amostras a reproducao; False se o usuario ja mudou de ponto ou de arquivo."""
        with self._lock:
            while self._generation == generation and self._buffered >= MAX_BUFFER:
                self._lock.wait(0.1)
            if self._generation != generation:
                return False
            self._chunks.append(samples)
            self._buffered += len(samples)
            return True

    def _decode(self, path: Path, start: float, speed: float, generation: int) -> None:
        import av

        error = ""
        try:
            tempo = _Tempo(speed) if speed != 1.0 else None

            def deliver(samples: np.ndarray) -> bool:
                if not len(samples):
                    return True
                chunks = tempo.process(samples) if tempo else [samples]
                return all(self._push(chunk, generation) for chunk in chunks)

            with av.open(str(path)) as container:
                stream = container.streams.audio[0]
                if start > 0:
                    container.seek(int(start * av.time_base))
                resampler = av.AudioResampler(format="s16", layout="stereo", rate=RATE)
                for packet in container.demux(stream):
                    try:
                        frames = packet.decode()
                    except av.InvalidDataError:
                        continue  # pacote danificado: segue com o proximo
                    for frame in frames:
                        drop = 0
                        if frame.time is not None:
                            # O seek para antes do ponto pedido.
                            if frame.time + frame.samples / frame.sample_rate <= start:
                                continue
                            drop = max(0, int((start - frame.time) * RATE))
                        for out in resampler.resample(frame):
                            if not deliver(out.to_ndarray().reshape(-1, CHANNELS)[drop:]):
                                return
                for out in resampler.resample(None):
                    if not deliver(out.to_ndarray().reshape(-1, CHANNELS)):
                        return
                for chunk in tempo.flush() if tempo else []:
                    if not self._push(chunk, generation):
                        return
        except Exception as exc:
            log.exception("Falha ao decodificar %s", path)
            error = f"Não foi possível tocar {path.name}: {exc}"
        with self._lock:
            if self._generation == generation:
                self._eof = True
                self.error = error

    # ---- saida de audio ---------------------------------------------------------------

    def _callback(self, out, frames, _time, _status) -> None:
        with self._lock:
            filled = 0
            while filled < frames and self._chunks:
                chunk = self._chunks[0]
                take = min(frames - filled, len(chunk) - self._offset)
                out[filled:filled + take] = chunk[self._offset:self._offset + take]
                filled += take
                self._offset += take
                if self._offset == len(chunk):
                    self._chunks.popleft()
                    self._offset = 0
            out[filled:] = 0
            self._played += filled
            self._buffered -= filled
            ended = filled < frames and self._eof
            if ended:
                self.finished = True
            self._lock.notify_all()
        if ended:
            raise self._stop_signal
