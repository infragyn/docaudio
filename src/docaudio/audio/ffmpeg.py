"""Codificacao com FFmpeg: MP3 por capitulo e M4B com marcadores."""

import json
import logging
import math
import subprocess
import sys
import threading
from pathlib import Path

from docaudio.audio.wav import AudioError, duration_seconds

log = logging.getLogger(__name__)

# Volume integrado de -16 LUFS, pico real de -1,5 dB.
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"
SAMPLE_RATE = "44100"


class Cancelled(Exception):
    """Conversao interrompida pelo usuario."""


def run_ffmpeg(
    ffmpeg: Path, args: list[str], cancel: threading.Event | None = None, loglevel: str = "error"
) -> str:
    """Executa o FFmpeg, permitindo cancelar; devolve a saida de erro (stderr)."""
    command = [str(ffmpeg), "-hide_banner", "-loglevel", loglevel, "-nostdin", "-y", *args]
    log.debug("FFmpeg: %s", " ".join(command))
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=flags
    )
    stderr_lines: list[bytes] = []
    reader = threading.Thread(target=lambda: stderr_lines.extend(process.stderr), daemon=True)
    reader.start()
    while process.poll() is None:
        if cancel and cancel.wait(0.2):
            process.kill()
            process.wait()
            raise Cancelled()
        if not cancel:
            process.wait()
    reader.join(timeout=5)
    stderr = b"".join(stderr_lines).decode("utf-8", "replace").strip()
    if process.returncode != 0:
        raise AudioError(f"FFmpeg falhou (código {process.returncode}): {stderr[-500:]}")
    return stderr


def loudnorm_filter(ffmpeg: Path, input_args: list[str], cancel: threading.Event | None = None) -> str:
    """Filtro loudnorm em duas passadas: mede o audio e aplica um ganho linear.

    A passada unica (dinamica) fica longe do alvo em faixas curtas. Se a medicao
    falhar (ex.: audio so com silencio), usa a passada unica.
    """
    stderr = run_ffmpeg(
        ffmpeg, [*input_args, "-af", f"{LOUDNORM}:print_format=json", "-f", "null", "-"], cancel, loglevel="info"
    )
    try:
        measured = json.loads(stderr[stderr.rindex("{") : stderr.rindex("}") + 1])
        values = {k: float(measured[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
    except (ValueError, KeyError):
        log.warning("Medição de volume indisponível; usando normalização em passada única.")
        return LOUDNORM
    if any(math.isinf(v) or math.isnan(v) for v in values.values()):
        return LOUDNORM
    return (
        f"{LOUDNORM}:measured_I={values['input_i']}:measured_TP={values['input_tp']}"
        f":measured_LRA={values['input_lra']}:measured_thresh={values['input_thresh']}"
        f":offset={values['target_offset']}:linear=true"
    )


def _metadata_args(metadata: dict[str, str]) -> list[str]:
    args = []
    for key, value in metadata.items():
        if value:
            args += ["-metadata", f"{key}={value}"]
    return args


def _cover_args(cover: Path | None, index: int) -> tuple[list[str], list[str]]:
    """Entrada e mapeamento da capa (imagem anexada), ou nada sem capa."""
    if cover is None or not cover.is_file():
        return [], []
    return ["-i", str(cover)], [
        "-map", f"{index}:v", "-c:v", "mjpeg", "-disposition:v:0", "attached_pic",
        "-metadata:s:v", "title=Capa", "-metadata:s:v", "comment=Cover (front)",
    ]


def encode_mp3(
    ffmpeg: Path,
    wav: Path,
    output: Path,
    metadata: dict[str, str],
    cancel: threading.Event | None = None,
    cover: Path | None = None,
) -> Path:
    part = output.with_name(output.name + ".part")
    loudnorm = loudnorm_filter(ffmpeg, ["-i", str(wav)], cancel)
    cover_input, cover_map = _cover_args(cover, 1)
    args = [
        "-i", str(wav), *cover_input, "-map", "0:a", *cover_map,
        "-af", loudnorm, "-ar", SAMPLE_RATE, "-ac", "1",
        "-codec:a", "libmp3lame", "-b:a", "96k", "-id3v2_version", "3",
        *_metadata_args(metadata), "-f", "mp3", str(part),
    ]
    try:
        run_ffmpeg(ffmpeg, args, cancel)
        part.replace(output)
    finally:
        part.unlink(missing_ok=True)
    return output


def _escape_metadata(value: str) -> str:
    for char in ("\\", "=", ";", "#", "\n"):
        value = value.replace(char, "\\" + char)
    return value


def chapters_metadata(titles: list[str], wavs: list[Path], album_metadata: dict[str, str]) -> str:
    """Arquivo FFMETADATA1 com um [CHAPTER] por capitulo, em milissegundos."""
    lines = [";FFMETADATA1"]
    lines += [f"{key}={_escape_metadata(value)}" for key, value in album_metadata.items() if value]
    start = 0
    for title, wav in zip(titles, wavs):
        end = start + round(duration_seconds(wav) * 1000)
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={start}", f"END={end}", f"title={_escape_metadata(title)}"]
        start = end
    return "\n".join(lines) + "\n"


def encode_m4b(
    ffmpeg: Path,
    wavs: list[Path],
    titles: list[str],
    output: Path,
    metadata: dict[str, str],
    work_dir: Path,
    cancel: threading.Event | None = None,
    cover: Path | None = None,
) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    concat_list = work_dir / "capitulos.txt"
    # Formato do demuxer concat: aspas simples no caminho viram '\''.
    concat_list.write_text(
        "".join("file '" + wav.as_posix().replace("'", "'\\''") + "'\n" for wav in wavs), encoding="utf-8"
    )
    metadata_file = work_dir / "capitulos.ffmeta"
    metadata_file.write_text(chapters_metadata(titles, wavs, metadata), encoding="utf-8")
    part = output.with_name(output.name + ".part")
    concat_input = ["-f", "concat", "-safe", "0", "-i", str(concat_list)]
    loudnorm = loudnorm_filter(ffmpeg, concat_input, cancel)
    cover_input, cover_map = _cover_args(cover, 2)
    args = [
        *concat_input, "-i", str(metadata_file), *cover_input,
        "-map", "0:a", *cover_map, "-map_metadata", "1", "-map_chapters", "1",
        "-af", loudnorm, "-ar", SAMPLE_RATE, "-ac", "1", "-c:a", "aac", "-b:a", "64k",
        "-movflags", "+faststart", "-f", "mp4", str(part),
    ]
    try:
        run_ffmpeg(ffmpeg, args, cancel)
        part.replace(output)
    finally:
        part.unlink(missing_ok=True)
    return output
