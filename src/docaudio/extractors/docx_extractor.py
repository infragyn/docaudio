"""Leitura de DOCX com python-docx.

Titulos vem dos estilos (Titulo 1, Heading 2...) ou do nivel de estrutura do
paragrafo. Sem estilos, usa heuristica de formatacao: negrito, tamanho maior
ou padroes como "Capitulo 3".
"""

import re
from collections import Counter
from pathlib import Path

import docx
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from docaudio.core.model import Block, BlockType, Document, DocumentKind
from docaudio.extractors.base import Extraction, ExtractionError
from docaudio.extractors.code_detection import CODE_STYLE, MONOSPACE_FONT, looks_like_code
from docaudio.text_processing.cleanup import clean_text

HEADING_STYLE = re.compile(r"^(heading|t[íi]tulo|cabe[çc]alho)\s*(\d)$", re.IGNORECASE)
TITLE_STYLES = {"title", "título", "titulo"}
TOC_STYLE = re.compile(r"^(toc|sum[áa]rio|[íi]ndice)\s*(heading|\d)", re.IGNORECASE)
LIST_STYLE = re.compile(r"list|lista|marcador|numera[çc]", re.IGNORECASE)
CHAPTER_PATTERN = re.compile(
    r"^(cap[íi]tulo|unidade|m[óo]dulo|parte|aula|se[çc][ãa]o|li[çc][ãa]o)\s+[\wIVXLC]+\b", re.IGNORECASE
)
MAX_HEURISTIC_HEADING_CHARS = 120
DEFAULT_BODY_SIZE = 11.0


def _style_heading_level(paragraph: Paragraph) -> int | None:
    style = paragraph.style
    while style is not None:
        name = (style.name or "").strip()
        if name.lower() in TITLE_STYLES:
            return 0
        match = HEADING_STYLE.match(name)
        if match:
            return int(match.group(2))
        style = style.base_style
    return None


def _outline_level(paragraph: Paragraph) -> int | None:
    """Nivel de estrutura definido direto no paragrafo (w:outlineLvl, 0 = nivel 1)."""
    ppr = paragraph._p.pPr
    node = ppr.find(qn("w:outlineLvl")) if ppr is not None else None
    if node is None:
        return None
    value = int(node.get(qn("w:val")))
    return value + 1 if value < 9 else None


def _is_list(paragraph: Paragraph) -> bool:
    ppr = paragraph._p.pPr
    if ppr is not None and ppr.find(qn("w:numPr")) is not None:
        return True
    return bool(LIST_STYLE.search(paragraph.style.name or ""))


def _run_size(run, paragraph: Paragraph) -> float | None:
    size = run.font.size or paragraph.style.font.size
    return size.pt if size else None


def _formatting(paragraph: Paragraph) -> tuple[bool, float | None]:
    """(todo em negrito, maior tamanho de fonte) considerando apenas runs com texto."""
    runs = [r for r in paragraph.runs if r.text.strip()]
    if not runs:
        return False, None
    style_bold = bool(paragraph.style.font.bold)
    all_bold = all(r.bold if r.bold is not None else style_bold for r in runs)
    sizes = [s for r in runs if (s := _run_size(r, paragraph))]
    return all_bold, max(sizes) if sizes else None


def _is_code_formatted(paragraph: Paragraph) -> bool:
    """Estilo de codigo ou todo o texto em fonte monoespacada."""
    if CODE_STYLE.search(paragraph.style.name or ""):
        return True
    style_font = paragraph.style.font.name or ""
    fonts = [r.font.name or style_font for r in paragraph.runs if r.text.strip()]
    return bool(fonts) and all(MONOSPACE_FONT.search(f) for f in fonts)


def _end_sentence(text: str) -> str:
    return text if text.endswith((".", "!", "?", ":")) else text + "."


def _has_header(rows: list[list[str]]) -> bool:
    header = rows[0]
    return (
        len(rows) >= 2
        and len(header) >= 2
        and all(header)
        and all(len(h) <= 40 and not h.endswith((".", ";")) for h in header)
        and len(set(header)) == len(header)
        and header != rows[1]
    )


def format_table(rows: list[list[str]]) -> str:
    """Texto narravel de uma tabela.

    Com cabecalho, cada linha vira "Coluna: valor; Coluna: valor." (ex.: gabarito
    "Questão: 1; Resposta: C; Comentário: ..."); sem cabecalho, as celulas sao
    lidas em sequencia.
    """
    rows = [row for row in rows if any(row)]
    if not rows:
        return ""
    lines = []
    if _has_header(rows):
        header = [h.rstrip(":") for h in rows[0]]
        for row in rows[1:]:
            parts = [f"{h}: {v.rstrip('.')}" for h, v in zip(header, row) if v]
            if parts:
                lines.append("; ".join(parts) + ".")
    else:
        for row in rows:
            cells: list[str] = []
            for text in row:
                # Celulas mescladas se repetem em python-docx.
                if text and (not cells or cells[-1] != text):
                    cells.append(text)
            if cells:
                lines.append(_end_sentence("; ".join(cells)))
    return "\n".join(lines)


def _table_text(table: Table) -> str:
    return format_table([[clean_text(cell.text).replace("\n", " ") for cell in row.cells] for row in table.rows])


def _body_size(paragraphs: list[Paragraph]) -> float:
    sizes: Counter[float] = Counter()
    for paragraph in paragraphs:
        for run in paragraph.runs:
            size = _run_size(run, paragraph)
            if size and run.text.strip():
                sizes[size] += len(run.text)
    return sizes.most_common(1)[0][0] if sizes else DEFAULT_BODY_SIZE


def _heuristic_levels(candidates: list[tuple[int, float | None, bool]], body: float) -> dict[int, int]:
    """Atribui niveis aos titulos inferidos.

    Padroes como "Capitulo 2" ficam no nivel 1; depois fontes maiores primeiro e,
    por ultimo, negrito no tamanho do corpo.
    """
    sizes = sorted({round(size, 1) for _, size, _ in candidates if size and size >= body + 1.5}, reverse=True)
    levels: dict[int, int] = {}
    for index, size, chapter_like in candidates:
        if chapter_like:
            levels[index] = 1
        elif size and round(size, 1) in sizes:
            levels[index] = sizes.index(round(size, 1)) + 1
        else:
            levels[index] = len(sizes) + 1
    return levels


def extract_docx(path: Path) -> Extraction:
    try:
        source: DocxDocument = docx.Document(str(path))
    except Exception as exc:
        raise ExtractionError(f"Não foi possível abrir o DOCX '{path.name}': {exc}") from exc

    items = list(source.iter_inner_content())
    paragraphs = [item for item in items if isinstance(item, Paragraph)]
    body = _body_size(paragraphs)
    # O estilo Titulo (nivel 0) sozinho nao indica que o documento usa estilos de secao.
    uses_styles = any(
        (_style_heading_level(p) or 0) >= 1 or _outline_level(p) is not None
        for p in paragraphs
        if p.text.strip()
    )

    blocks: list[Block] = []
    candidates: list[tuple[int, float | None, bool]] = []
    for item in items:
        if isinstance(item, Table):
            text = _table_text(item)
            if text:
                blocks.append(Block(BlockType.TABLE, text))
            continue

        text = clean_text(item.text).replace("\n", " ")
        if not text:
            continue
        if TOC_STYLE.match(item.style.name or ""):
            blocks.append(Block(BlockType.TOC, text))
            continue
        if _is_code_formatted(item):
            blocks.append(Block(BlockType.CODE, text))
            continue

        level = _style_heading_level(item)
        if level is None:
            level = _outline_level(item)
        if level is not None:
            blocks.append(Block(BlockType.HEADING, text, level=level))
            continue

        if (
            not uses_styles
            and len(text) <= MAX_HEURISTIC_HEADING_CHARS
            and not text.endswith((".", ",", ";", ":"))
            and not looks_like_code(text)
        ):
            bold, size = _formatting(item)
            chapter_like = bool(CHAPTER_PATTERN.match(text))
            if chapter_like or bold or (size and size >= body + 1.5):
                candidates.append((len(blocks), size, chapter_like))
                blocks.append(Block(BlockType.HEADING, text))
                continue

        block_type = BlockType.LIST if _is_list(item) else BlockType.PARAGRAPH
        blocks.append(Block(block_type, text))

    for index, level in _heuristic_levels(candidates, body).items():
        blocks[index].level = level

    title = (source.core_properties.title or "").strip()
    if not title:
        title = next((b.text for b in blocks if b.type == BlockType.HEADING and b.level == 0), path.stem)
    document = Document(
        source=path, kind=DocumentKind.DOCX, title=title, author=(source.core_properties.author or "").strip()
    )
    if not blocks:
        document.warnings.append("O documento não contém texto.")
    return Extraction(document, blocks)
