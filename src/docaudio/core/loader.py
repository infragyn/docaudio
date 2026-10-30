"""Carrega um documento: extracao -> classificacao -> capitulos -> regras de leitura."""

import logging
from collections.abc import Callable
from pathlib import Path

from docaudio.core.chapters import build_chapters
from docaudio.core.dependencies import find_tessdata, ocr_language_code
from docaudio.core.language import LANGUAGES, detect_language
from docaudio.core.model import Block, BlockType, Document
from docaudio.core.rules import apply_rules
from docaudio.extractors.base import ExtractionError
from docaudio.extractors.classify import classify_blocks
from docaudio.storage.settings import Settings
from docaudio.text_processing.accents import AccentRestorer

log = logging.getLogger(__name__)

# Velocidade media da voz Piper pt-BR em 1,0x, em caracteres por segundo, ja com
# pausas e titulos: medida numa apostila real (22.439 caracteres -> 30 min).
CHARS_PER_SECOND = 12.5


def load_document(path: Path, settings: Settings, progress: Callable[[str], None] | None = None) -> Document:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        from docaudio.extractors.docx_extractor import extract_docx

        extraction = extract_docx(path)
    elif suffix == ".pdf":
        from docaudio.extractors.pdf_extractor import OcrOptions, extract_pdf

        ocr = None
        if settings.ocr_enabled:
            code = ocr_language_code(settings)
            tessdata = find_tessdata(settings, code)
            if tessdata:
                ocr = OcrOptions(tessdata, code)
        extraction = extract_pdf(path, ocr, progress)
    elif suffix in (".txt", ".html", ".htm", ".xhtml", ".epub", ".pptx"):
        from docaudio.extractors import text_formats

        readers = {
            ".txt": text_formats.extract_txt,
            ".epub": text_formats.extract_epub,
            ".pptx": text_formats.extract_pptx,
        }
        extraction = readers.get(suffix, text_formats.extract_html)(path)
    else:
        raise ExtractionError(f"Formato não suportado: {path.name}")

    blocks = classify_blocks(extraction.blocks)
    document = extraction.document
    if settings.document_language in LANGUAGES:
        document.language = settings.document_language
    else:
        sample = " ".join(b.text for b in blocks if b.type != BlockType.CODE)
        document.language = detect_language(sample)
    # A correcao de acentos conhece so palavras em portugues.
    if settings.restore_accents and document.language == "pt":
        _restore_accents(document, blocks)
    document.chapters = build_chapters(blocks, max_chars=settings.max_chapter_chars)
    apply_rules(document, settings)
    log.info(
        "%s: %d capítulo(s), %d caracteres narráveis, idioma %s.",
        path.name,
        len(document.chapters),
        document.char_count(),
        LANGUAGES[document.language].lower(),
    )
    for warning in document.warnings:
        log.warning("%s: %s", path.name, warning)
    return document


def _restore_accents(document: Document, blocks: list[Block]) -> None:
    """Corrige palavras digitadas sem acento (ex.: Informacao); codigo e enderecos ficam intactos."""
    restorer = AccentRestorer([document.title, *(b.text for b in blocks)])
    for block in blocks:
        if block.type not in (BlockType.CODE, BlockType.URL):
            block.text = restorer.restore(block.text)
    document.title = restorer.restore(document.title)
    if restorer.total:
        examples = ", ".join(f"{a} → {b}" for (a, b), _ in restorer.changes.most_common(3))
        document.warnings.append(
            f"{restorer.total} palavra(s) sem acento corrigida(s) automaticamente (ex.: {examples})."
        )


def estimated_seconds(characters: int, speed: float) -> float:
    return characters / (CHARS_PER_SECOND * speed)


# Segundos de processamento por caractere, medidos neste projeto (RTX 4050, Core i7):
# a voz clonada e ~75x mais lenta que a Kokoro, que e ~10x mais lenta que a Piper.
GENERATION_SECONDS_PER_CHAR = {"Clonada": 0.18, "Kokoro": 0.025, "Piper": 0.003, "Windows": 0.001}
# Acima disso o usuario e avisado antes de comecar a conversao.
LONG_CONVERSION_SECONDS = 15 * 60


def estimated_generation_seconds(characters: int, engine: str) -> float:
    """Tempo aproximado para gerar o audio (sem contar trechos ja gerados antes)."""
    return characters * GENERATION_SECONDS_PER_CHAR.get(engine, 0.01)


def format_duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 1:
        return "menos de 1 min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"
