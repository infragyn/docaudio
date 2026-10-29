"""Verificacao das dependencias locais (FFmpeg e voz Piper)."""

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from docaudio.core import paths
from docaudio.speech.registry import VoiceRegistry
from docaudio.storage.settings import Settings


@dataclass(frozen=True)
class DependencyStatus:
    name: str
    ok: bool
    detail: str
    # Dependencias nao obrigatorias apenas geram aviso nesta etapa.
    required: bool = True
    hint: str = ""


def find_ffmpeg(configured: str = "") -> Path | None:
    """Ordem de busca: caminho configurado, binario do pacote, PATH."""
    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates.append(paths.bundled_bin_dir() / "ffmpeg.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    found = shutil.which("ffmpeg") or shutil.which("ffmpeg", path=_registry_path())
    return Path(found) if found else None


def _registry_path() -> str | None:
    """PATH gravado no Windows: enxerga o FFmpeg instalado depois que o app foi aberto."""
    if sys.platform != "win32":
        return None
    import winreg

    parts = []
    for root, key in (
        (winreg.HKEY_CURRENT_USER, "Environment"),
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    ):
        try:
            with winreg.OpenKey(root, key) as handle:
                parts.append(os.path.expandvars(winreg.QueryValueEx(handle, "Path")[0]))
        except OSError:
            continue
    return os.pathsep.join(parts) or None


def check_ffmpeg(settings: Settings) -> DependencyStatus:
    ffmpeg = find_ffmpeg(settings.ffmpeg_path)
    if ffmpeg:
        return DependencyStatus("FFmpeg", True, str(ffmpeg), required=False)
    return DependencyStatus(
        "FFmpeg",
        False,
        "não encontrado",
        required=False,
        hint="Necessário para MP3 e M4B; sem ele, use o formato WAV. Instale o FFmpeg ou coloque ffmpeg.exe em resources/bin.",
    )


def find_tessdata(settings: Settings) -> Path | None:
    """Pasta com <idioma>.traineddata para o OCR.

    Ordem: configurada, do pacote, do usuario, TESSDATA_PREFIX, instalacao do Tesseract.
    """
    language_file = f"{settings.ocr_language}.traineddata"
    candidates = [Path(settings.tessdata_dir)] if settings.tessdata_dir else []
    fast = [paths.resources_dir() / "tessdata", paths.data_dir() / "tessdata"]
    best = [paths.resources_dir() / "tessdata_best", paths.data_dir() / "tessdata_best"]
    # O modelo escolhido vem primeiro; o outro serve de reserva.
    candidates += best + fast if settings.ocr_quality == "preciso" else fast + best
    if os.environ.get("TESSDATA_PREFIX"):
        candidates.append(Path(os.environ["TESSDATA_PREFIX"]))
    try:
        import pymupdf

        candidates.append(Path(pymupdf.get_tessdata()))
    except Exception:
        pass
    return next((c for c in candidates if (c / language_file).is_file()), None)


def check_ocr(settings: Settings) -> DependencyStatus:
    if not settings.ocr_enabled:
        return DependencyStatus("OCR", True, "desativado", required=False)
    tessdata = find_tessdata(settings)
    if tessdata:
        return DependencyStatus("OCR", True, f"{settings.ocr_language} ({tessdata})", required=False)
    return DependencyStatus(
        "OCR",
        False,
        f"dados do idioma '{settings.ocr_language}' não encontrados",
        required=False,
        hint="PDFs digitalizados não serão lidos. Coloque por.traineddata em resources/tessdata "
        "ou instale o Tesseract com o idioma português.",
    )


def check_voice(settings: Settings, provider=None) -> DependencyStatus:
    provider = provider or VoiceRegistry()
    voice = provider.find_voice(settings.voice)
    if voice:
        return DependencyStatus("Voz", True, voice.display_name)
    installed = provider.list_voices()
    if installed:
        return DependencyStatus(
            "Voz",
            False,
            f"voz '{settings.voice}' ausente",
            hint="Escolha outra voz instalada: " + ", ".join(v.id for v in installed),
        )
    return DependencyStatus(
        "Voz",
        False,
        "nenhuma voz instalada",
        hint=f"Instale com: docaudio baixar-voz {settings.voice}",
    )


def check_all(settings: Settings, provider=None) -> list[DependencyStatus]:
    return [check_voice(settings, provider), check_ffmpeg(settings), check_ocr(settings)]
