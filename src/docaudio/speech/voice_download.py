"""Download de modelos de voz Piper do repositorio oficial (rhasspy/piper-voices)."""

import logging
import re
import shutil
import urllib.request
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
VOICE_ID_PATTERN = re.compile(r"^(?P<lang>[a-z]{2,3})_(?P<region>[A-Z]{2})-(?P<name>[\w]+)-(?P<quality>x_low|low|medium|high)$")

ProgressCallback = Callable[[str, int, int], None]


def voice_urls(voice_id: str) -> dict[str, str]:
    """URLs do modelo e da configuracao de uma voz, ex.: pt_BR-faber-medium."""
    match = VOICE_ID_PATTERN.match(voice_id)
    if not match:
        raise ValueError(f"Identificador de voz invalido: {voice_id}")
    lang = match["lang"]
    lang_code = f"{lang}_{match['region']}"
    base = f"{BASE_URL}/{lang}/{lang_code}/{match['name']}/{match['quality']}/{voice_id}"
    return {
        f"{voice_id}.onnx": f"{base}.onnx?download=true",
        f"{voice_id}.onnx.json": f"{base}.onnx.json?download=true",
    }


KOKORO_ID = "kokoro"
KOKORO_BASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"


def kokoro_urls() -> dict[str, str]:
    from docaudio.speech.kokoro_provider import MODEL_FILE, VOICES_FILE

    return {name: f"{KOKORO_BASE_URL}/{name}" for name in (MODEL_FILE, VOICES_FILE)}


def download_voice(voice_id: str, dest_dir: Path, progress: ProgressCallback | None = None) -> Path:
    """Baixa uma voz Piper ou, com voice_id "kokoro", o modelo Kokoro (em dest_dir/kokoro)."""
    if voice_id == KOKORO_ID:
        folder = dest_dir / "kokoro"
        _download(kokoro_urls(), folder, progress)
        return folder
    _download(voice_urls(voice_id), dest_dir, progress)
    return dest_dir / f"{voice_id}.onnx"


def _download(files: dict[str, str], dest_dir: Path, progress: ProgressCallback | None) -> None:
    """Arquivos parciais nunca ficam com o nome final."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    for filename, url in files.items():
        target = dest_dir / filename
        if target.exists():
            log.info("%s ja existe; download ignorado.", target)
            continue
        part = target.with_name(target.name + ".part")
        log.info("Baixando %s", url)
        try:
            with urllib.request.urlopen(url, timeout=60) as response, part.open("wb") as out:
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                while chunk := response.read(256 * 1024):
                    out.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(filename, done, total)
            if total and part.stat().st_size != total:
                raise OSError(f"download incompleto de {filename}")
            shutil.move(part, target)
        finally:
            part.unlink(missing_ok=True)
