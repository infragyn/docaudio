"""Regras de leitura: quais tipos de bloco sao narrados."""

from docaudio.core.model import BlockType, Document
from docaudio.storage.settings import Settings


def included_types(settings: Settings) -> set[BlockType]:
    types = {BlockType.HEADING, BlockType.PARAGRAPH, BlockType.LIST}
    optional = {
        BlockType.TABLE: settings.read_tables,
        BlockType.URL: settings.read_urls,
        BlockType.NOTE: settings.read_notes,
        BlockType.REFERENCE: settings.read_references,
        BlockType.CODE: settings.read_code,
    }
    types.update(t for t, enabled in optional.items() if enabled)
    # Sumario nunca e narrado: repete os titulos e numeros de pagina.
    return types


def apply_rules(document: Document, settings: Settings) -> None:
    allowed = included_types(settings)
    for chapter in document.chapters:
        for block in chapter.blocks:
            block.include = block.type in allowed
