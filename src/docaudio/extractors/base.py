"""Resultado comum dos extratores."""

from dataclasses import dataclass, field

from docaudio.core.model import Block, Document


class ExtractionError(RuntimeError):
    """Falha de leitura com mensagem pronta para o usuario."""


@dataclass
class Extraction:
    """Documento sem capitulos e a sequencia de blocos na ordem de leitura."""

    document: Document
    blocks: list[Block] = field(default_factory=list)
