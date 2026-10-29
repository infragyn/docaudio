"""Classificacao comum aos extratores: listas, URLs, sumario e referencias."""

import re

from docaudio.core.model import Block, BlockType
from docaudio.extractors.code_detection import looks_like_code, mark_code_runs
from docaudio.text_processing.cleanup import normalize_key

BULLET_SYMBOL = re.compile(r"^[•◦▪▫●○■□►▶✓✔\-–—*]\s+")
BULLET_ENUM = re.compile(r"^\(?([a-zA-Z]|\d{1,2}|[ivxIVX]{1,4})\)\s+")
URL_ONLY = re.compile(r"^(<)?(https?://|www\.)\S+(>)?$", re.IGNORECASE)
DOT_LEADER = re.compile(r"(\.{4,}|…{2,}|(\s\.){4,})\s*\d+\s*$")
TOC_TITLES = {"sumario", "indice", "conteudo", "table of contents", "contents"}
REFERENCE_TITLES = {
    "referencias",
    "referencias bibliograficas",
    "bibliografia",
    "bibliografia consultada",
    "fontes",
    "references",
}


def _title_key(text: str) -> str:
    return re.sub(r"^[#\s.]+", "", normalize_key(text))


def classify_blocks(blocks: list[Block]) -> list[Block]:
    """Ajusta o tipo dos blocos e remove simbolos de marcador do texto narrado."""
    # Codigo-fonte primeiro: linhas como "* comentario" ou "- x" nao sao listas.
    flags = [b.type == BlockType.CODE or (b.type == BlockType.PARAGRAPH and looks_like_code(b.text)) for b in blocks]
    texts = [b.text if b.type in (BlockType.PARAGRAPH, BlockType.CODE) else "." for b in blocks]
    for block, is_code in zip(blocks, mark_code_runs(flags, texts)):
        if is_code and block.type in (BlockType.PARAGRAPH, BlockType.CODE):
            block.type = BlockType.CODE

    for block in blocks:
        if block.type not in (BlockType.PARAGRAPH, BlockType.LIST):
            continue
        text = block.text
        if URL_ONLY.match(text):
            block.type = BlockType.URL
        elif DOT_LEADER.search(text):
            block.type = BlockType.TOC
        elif match := BULLET_SYMBOL.match(text):
            block.type = BlockType.LIST
            block.text = block.original_text = text[match.end():]
        elif BULLET_ENUM.match(text):
            block.type = BlockType.LIST

    # Secoes inteiras de sumario e referencias, ate o proximo titulo de mesmo nivel ou acima.
    section: tuple[BlockType, int] | None = None
    for block in blocks:
        if block.type == BlockType.HEADING:
            if section and block.level <= section[1]:
                section = None
            key = _title_key(block.text)
            if key in TOC_TITLES:
                section = (BlockType.TOC, block.level)
            elif key in REFERENCE_TITLES:
                section = (BlockType.REFERENCE, block.level)
            continue
        if section and block.type in (BlockType.PARAGRAPH, BlockType.LIST, BlockType.URL):
            block.type = section[0]
    return blocks
