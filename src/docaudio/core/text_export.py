"""Salva texto (por exemplo, uma traducao) em TXT ou DOCX, sem precisar gerar audio."""

from pathlib import Path

from docaudio.core.model import BlockType, Document

Section = tuple[str, list[str]]


def document_sections(document: Document) -> list[Section]:
    """Capitulos incluidos, com o titulo e os paragrafos como estao na previa."""
    sections = []
    for chapter in document.included_chapters():
        # Capitulo sem nivel foi criado por tamanho ("Parte 1"): o titulo nao e do texto.
        heading = chapter.title if chapter.level is not None else ""
        paragraphs = [
            block.text
            for block in chapter.included_blocks()
            if block.text.strip() and not (block.type == BlockType.HEADING and block.text == heading)
        ]
        if heading or paragraphs:
            sections.append((heading, paragraphs))
    return sections


def plain_sections(text: str) -> list[Section]:
    return [("", [line for line in text.split("\n") if line.strip()])]


def save_text(path: Path, title: str, sections: list[Section]) -> Path:
    """O formato vem da extensao: .docx ou texto simples em UTF-8."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".docx":
        _save_docx(path, title, sections)
    else:
        path.write_text(_plain(title, sections), encoding="utf-8")
    return path


def _plain(title: str, sections: list[Section]) -> str:
    parts = [title] if title else []
    for heading, paragraphs in sections:
        parts.extend([heading] if heading else [])
        parts.extend(paragraphs)
    return "\n\n".join(parts) + "\n"


def _save_docx(path: Path, title: str, sections: list[Section]) -> None:
    from docx import Document as WordDocument

    word = WordDocument()
    if title:
        word.add_heading(title, level=0)
    for heading, paragraphs in sections:
        if heading:
            word.add_heading(heading, level=1)
        for paragraph in paragraphs:
            word.add_paragraph(paragraph)
    word.save(str(path))
