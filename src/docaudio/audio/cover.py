"""Capa do audiolivro: primeira pagina do PDF ou uma capa simples com o titulo."""

import logging
import textwrap
from pathlib import Path

import pymupdf

from docaudio.core.model import Document, DocumentKind

log = logging.getLogger(__name__)

SIZE = 600  # pixels; players de celular mostram capas quadradas pequenas
BACKGROUND = (0.145, 0.388, 0.922)  # azul do icone do DocAudio
TEXT = (1, 1, 1)


def _pdf_first_page(source: Path, target: Path) -> bool:
    with pymupdf.open(source) as pdf:
        if not pdf.page_count:
            return False
        page = pdf[0]
        zoom = SIZE / max(page.rect.width, page.rect.height)
        page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).save(str(target), jpg_quality=88)
    return True


def _drawn_cover(title: str, author: str, target: Path) -> None:
    with pymupdf.open() as doc:
        page = doc.new_page(width=SIZE, height=SIZE)
        page.draw_rect(page.rect, color=None, fill=BACKGROUND)
        # Folha com linhas, como no icone do programa.
        page.draw_rect(pymupdf.Rect(60, 60, 150, 180), color=None, fill=TEXT, radius=0.08)
        for y in (90, 115, 140):
            page.draw_line((80, y), (130, y), color=BACKGROUND, width=6)
        lines = textwrap.wrap(title, 22)[:6] or ["Audiolivro"]
        page.insert_textbox(
            pymupdf.Rect(60, 220, SIZE - 60, SIZE - 110), "\n".join(lines),
            fontsize=38, fontname="helv", color=TEXT, lineheight=1.15,
        )
        if author:
            page.insert_textbox(
                pymupdf.Rect(60, SIZE - 100, SIZE - 60, SIZE - 50), author[:60], fontsize=22, fontname="helv", color=TEXT
            )
        page.get_pixmap().save(str(target), jpg_quality=90)


def make_cover(document: Document, target: Path) -> Path | None:
    """Gera `target` (JPEG); None se nao foi possivel (a conversao segue sem capa)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if document.kind in (DocumentKind.PDF, DocumentKind.PDF_OCR):
        try:
            if _pdf_first_page(document.source, target):
                return target
        except Exception as exc:  # PDF ilegivel ou movido: usa a capa desenhada
            log.info("Primeira página de %s não serviu de capa: %s", document.source.name, exc)
    try:
        _drawn_cover(document.title or document.source.stem, document.author, target)
        return target
    except Exception as exc:  # capa e enfeite: nunca impede a conversao
        log.warning("Capa não gerada para %s: %s", document.source.name, exc)
        return None
