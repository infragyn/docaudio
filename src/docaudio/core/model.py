"""Modelo de dados: Documento -> Capitulo -> Bloco.

Os trechos de sintese (Trecho) entram na Etapa 3.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class BlockType(StrEnum):
    HEADING = "titulo"
    PARAGRAPH = "paragrafo"
    LIST = "lista"
    TABLE = "tabela"
    NOTE = "nota"
    URL = "url"
    REFERENCE = "referencia"
    TOC = "sumario"
    CODE = "codigo"


BLOCK_TYPE_LABELS = {
    BlockType.CODE: "Código-fonte",
    BlockType.HEADING: "Título",
    BlockType.PARAGRAPH: "Parágrafo",
    BlockType.LIST: "Lista",
    BlockType.TABLE: "Tabela",
    BlockType.NOTE: "Nota de rodapé",
    BlockType.URL: "Endereço web",
    BlockType.REFERENCE: "Referência",
    BlockType.TOC: "Sumário",
}


class DocumentKind(StrEnum):
    DOCX = "docx"
    PDF = "pdf"
    PDF_OCR = "pdf_ocr"
    TXT = "txt"
    HTML = "html"
    EPUB = "epub"
    PPTX = "pptx"


@dataclass
class Block:
    type: BlockType
    original_text: str
    # Texto que sera narrado; a previa editavel altera este campo.
    text: str = ""
    # Nivel do titulo (0 = titulo do documento, 1 = capitulo, ...); 0 para outros tipos.
    level: int = 0
    page: int | None = None
    # Definido pelas regras de leitura por tipo de bloco.
    include: bool = True
    # Texto reconhecido por OCR: destacado na previa para revisao.
    ocr: bool = False
    # Pausa depois do bloco no lugar da pausa de paragrafo (ex.: tempo para pensar no modo questoes).
    pause_after_ms: int | None = None
    # Idioma deste bloco quando difere do documento (modo bilingue); "" = o do documento.
    language: str = ""

    def __post_init__(self) -> None:
        if not self.text:
            self.text = self.original_text

    @property
    def edited(self) -> bool:
        return self.text != self.original_text


@dataclass
class Chapter:
    number: int
    title: str
    blocks: list[Block] = field(default_factory=list)
    include: bool = True
    # Nivel do titulo que abriu o capitulo; None quando o capitulo foi criado por tamanho.
    level: int | None = None

    def included_blocks(self) -> list[Block]:
        return [b for b in self.blocks if b.include]

    def narration_text(self) -> str:
        return "\n\n".join(b.text for b in self.included_blocks())

    def char_count(self) -> int:
        return sum(len(b.text) for b in self.included_blocks())


@dataclass
class Document:
    source: Path
    kind: DocumentKind
    title: str
    chapters: list[Chapter] = field(default_factory=list)
    page_count: int | None = None
    # Paginas (1-based) sem camada de texto, que dependem de OCR.
    pages_without_text: list[int] = field(default_factory=list)
    # Paginas (1-based) cujo texto veio do OCR.
    ocr_pages: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Autor das propriedades do arquivo, quando houver (vai para os metadados do audio).
    author: str = ""
    # Idioma de leitura: "pt", "en" ou "es" (detectado ou escolhido pelo usuario).
    language: str = "pt"
    # Idioma original quando este documento e uma traducao; "" quando nao e.
    translated_from: str = ""

    def included_chapters(self) -> list[Chapter]:
        return [c for c in self.chapters if c.include]

    def char_count(self) -> int:
        return sum(c.char_count() for c in self.included_chapters())
