"""Leitura de PDF usando PyMuPDF, com OCR (Tesseract embutido) para paginas digitalizadas.

Etapas: linhas com fonte e posicao -> remocao de cabecalhos/rodapes repetidos
-> ordem de leitura (inclusive duas colunas) -> paragrafos -> titulos e notas.
"""

import math
import logging
import os
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from docaudio.core.model import Block, BlockType, Document, DocumentKind
from docaudio.extractors.base import Extraction, ExtractionError
from docaudio.extractors.code_detection import MONOSPACE_FONT
from docaudio.text_processing.cleanup import clean_text, join_lines, normalize_key

TEXT_FLAGS = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_LIGATURES & ~pymupdf.TEXT_PRESERVE_IMAGES
MARGIN_RATIO = 0.09
MIN_PAGE_CHARS = 20
PAGE_NUMBER = re.compile(r"^(p[áa]g(ina)?\.?\s*)?[-–]?\s*\d+\s*[-–]?(\s*(de|/)\s*\d+)?$", re.IGNORECASE)
BOLD_FONT = re.compile(r"bold|black|heavy|semibold|demi", re.IGNORECASE)
MAX_HEADING_CHARS = 150
# Titulo numerado ("2 Variaveis", "1.1 Atomos"): unica pista de titulo confiavel no OCR,
# que nao informa negrito e estima o tamanho da fonte com ruido.
NUMBERED_HEADING = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ]")
MAX_OCR_HEADING_CHARS = 80

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OcrOptions:
    tessdata: Path
    language: str = "por"
    dpi: int = 300


@dataclass
class Line:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    bold: bool
    page: int
    ocr: bool = False
    # Toda a linha em fonte monoespacada: indicio de codigo-fonte.
    mono: bool = False


@dataclass
class RawBlock:
    lines: list[Line] = field(default_factory=list)

    @property
    def x0(self) -> float:
        return min(line.x0 for line in self.lines)

    @property
    def x1(self) -> float:
        return max(line.x1 for line in self.lines)

    @property
    def y0(self) -> float:
        return min(line.y0 for line in self.lines)

    @property
    def y1(self) -> float:
        return max(line.y1 for line in self.lines)


@dataclass
class Paragraph:
    lines: list[Line]

    @property
    def text(self) -> str:
        return join_lines([line.text for line in self.lines])

    @property
    def size(self) -> float:
        counts: Counter[float] = Counter()
        for line in self.lines:
            counts[round(line.size * 2) / 2] += len(line.text)
        return counts.most_common(1)[0][0]

    @property
    def bold(self) -> bool:
        return all(line.bold for line in self.lines)

    @property
    def page(self) -> int:
        return self.lines[0].page

    @property
    def ocr(self) -> bool:
        return self.lines[0].ocr

    @property
    def mono(self) -> bool:
        return all(line.mono for line in self.lines)


def _looks_like_ocr_heading(text: str) -> bool:
    return (
        len(text) <= MAX_OCR_HEADING_CHARS
        and len(text.split()) <= 12
        and not text.endswith((".", ",", ";", ":"))
        and bool(NUMBERED_HEADING.match(text))
    )


def _read_line(raw: dict, page: int, ocr: bool = False) -> Line | None:
    parts: list[str] = []
    sizes: Counter[float] = Counter()
    bold_chars = 0
    mono_chars = 0
    total_chars = 0
    for span in raw["spans"]:
        text = span["text"]
        if not text.strip():
            parts.append(text)
            continue
        # Numeros sobrescritos sao chamadas de nota de rodape: nao devem ser narrados.
        if span["flags"] & pymupdf.TEXT_FONT_SUPERSCRIPT and text.strip().isdigit():
            continue
        parts.append(text)
        sizes[span["size"]] += len(text)
        total_chars += len(text)
        if span["flags"] & pymupdf.TEXT_FONT_BOLD or BOLD_FONT.search(span["font"]):
            bold_chars += len(text)
        if span["flags"] & pymupdf.TEXT_FONT_MONOSPACED or MONOSPACE_FONT.search(span["font"]):
            mono_chars += len(text)
    text = clean_text("".join(parts))
    if not text or not sizes:
        return None
    x0, y0, x1, y1 = raw["bbox"]
    # A fonte do OCR (GlyphLessFont) se declara monoespacada; nao vale como indicio.
    bold = bold_chars == total_chars and not ocr
    mono = mono_chars == total_chars and not ocr
    return Line(text, x0, y0, x1, y1, sizes.most_common(1)[0][0], bold, page, ocr, mono)


def _read_page(page: pymupdf.Page, number: int, textpage=None, ocr: bool = False) -> list[RawBlock]:
    data = page.get_text("dict", flags=TEXT_FLAGS, textpage=textpage)
    blocks = []
    for raw_block in data["blocks"]:
        if raw_block.get("type") != 0:
            continue
        lines = [line for raw in raw_block["lines"] if (line := _read_line(raw, number, ocr))]
        if lines:
            blocks.append(RawBlock(lines))
    return blocks


def _remove_headers_footers(pages: dict[int, list[RawBlock]], heights: dict[int, float]) -> int:
    """Remove linhas repetidas nas margens superior/inferior e numeros de pagina."""

    def in_margin(line: Line) -> bool:
        height = heights[line.page]
        return line.y1 <= height * MARGIN_RATIO or line.y0 >= height * (1 - MARGIN_RATIO)

    pages_with_key: defaultdict[str, set[int]] = defaultdict(set)
    for number, blocks in pages.items():
        for block in blocks:
            for line in block.lines:
                if in_margin(line):
                    pages_with_key[normalize_key(line.text)].add(number)

    threshold = max(2, math.ceil(len(pages) * 0.4))
    repeated = {key for key, found in pages_with_key.items() if len(found) >= threshold and key}
    removed = 0
    for number, blocks in pages.items():
        kept_blocks = []
        for block in blocks:
            kept = []
            for line in block.lines:
                if in_margin(line) and (normalize_key(line.text) in repeated or PAGE_NUMBER.match(line.text)):
                    removed += 1
                else:
                    kept.append(line)
            if kept:
                block.lines = kept
                kept_blocks.append(block)
        pages[number] = kept_blocks
    return removed


def order_blocks(blocks: list[RawBlock], page_width: float) -> list[RawBlock]:
    """Ordem de leitura com suporte a duas colunas.

    A regiao de colunas e a faixa vertical ocupada pela coluna direita; fora
    dela, mesmo blocos estreitos a esquerda (titulos curtos) contam como largura
    total. Blocos de largura total separam faixas; dentro de cada faixa le-se a
    coluna esquerda inteira e depois a direita.
    """
    middle = page_width / 2
    tolerance = page_width * 0.03

    def side(block: RawBlock) -> str:
        if block.x1 <= middle + tolerance:
            return "left"
        if block.x0 >= middle - tolerance:
            return "right"
        return "full"

    rights = [b for b in blocks if side(b) == "right"]
    if not rights:
        return sorted(blocks, key=lambda b: (b.y0, b.x0))
    region_top = min(b.y0 for b in rights) - tolerance
    region_bottom = max(b.y1 for b in rights) + tolerance

    def column(block: RawBlock) -> str:
        placement = side(block)
        if placement == "left" and not (block.y0 < region_bottom and block.y1 > region_top):
            return "full"
        return placement

    ordered: list[RawBlock] = []
    band: list[RawBlock] = []

    def flush() -> None:
        band.sort(key=lambda b: (column(b) == "right", b.y0, b.x0))
        ordered.extend(band)
        band.clear()

    for block in sorted(blocks, key=lambda b: (b.y0, b.x0)):
        if column(block) == "full":
            flush()
            ordered.append(block)
        else:
            band.append(block)
    flush()
    return ordered


def split_paragraphs(block: RawBlock) -> list[Paragraph]:
    """Separa um bloco em paragrafos por espaco vertical ou mudanca de fonte.

    No OCR o tamanho estimado varia de linha para linha; usa-se uma tolerancia
    maior e a numeracao para separar titulos do texto seguinte.
    """
    paragraphs: list[Paragraph] = []
    current: list[Line] = []
    for line in block.lines:
        if current:
            previous = current[-1]
            gap = line.y0 - previous.y1
            tolerance = 2.5 if line.ocr else 0.6
            font_changed = abs(line.size - previous.size) > tolerance or line.bold != previous.bold
            ocr_heading = line.ocr and (
                (len(current) == 1 and _looks_like_ocr_heading(previous.text)) or _looks_like_ocr_heading(line.text)
            )
            if gap > previous.size * 0.7 or font_changed or ocr_heading:
                paragraphs.append(Paragraph(current))
                current = []
        current.append(line)
    if current:
        paragraphs.append(Paragraph(current))
    return paragraphs


def _body_size(paragraphs: list[Paragraph]) -> float:
    counts: Counter[float] = Counter()
    for paragraph in paragraphs:
        counts[paragraph.size] += len(paragraph.text)
    return counts.most_common(1)[0][0] if counts else 11.0


def _toc_headings(toc: list, paragraphs: list[Paragraph]) -> dict[int, int]:
    """Associa entradas do sumario do PDF (outline) a paragrafos: indice -> nivel."""
    entries: defaultdict[int, list[tuple[str, int]]] = defaultdict(list)
    for level, title, page in toc:
        key = normalize_key(title)
        if key:
            entries[page].append((key, level))
    found: dict[int, int] = {}
    for index, paragraph in enumerate(paragraphs):
        text = paragraph.text
        if len(text) > MAX_HEADING_CHARS:
            continue
        key = normalize_key(text)
        for page in (paragraph.page, paragraph.page - 1, paragraph.page + 1):
            match = next((e for e in entries.get(page, []) if e[0] == key), None)
            if match:
                entries[page].remove(match)
                found[index] = match[1]
                break
    return found


def _font_headings(paragraphs: list[Paragraph], body: float) -> dict[int, int]:
    """Titulos por tamanho e peso da fonte: indice -> nivel.

    Paragrafos de OCR so viram titulo pela numeracao; o nivel vem da profundidade
    (1 -> 1, 1.1 -> 2).
    """
    candidates: dict[int, float] = {}
    numbered: dict[int, int] = {}
    for index, paragraph in enumerate(paragraphs):
        text = paragraph.text
        if paragraph.ocr:
            if len(paragraph.lines) <= 2 and _looks_like_ocr_heading(text):
                numbered[index] = NUMBERED_HEADING.match(text).group(1).count(".") + 1
            continue
        if len(text) > MAX_HEADING_CHARS or len(paragraph.lines) > 3 or text.endswith((".", ",", ";")):
            continue
        if paragraph.size >= body * 1.15 or (paragraph.bold and paragraph.size >= body - 0.5):
            candidates[index] = paragraph.size
    larger = sorted({s for s in candidates.values() if s >= body * 1.15}, reverse=True)
    by_font = {
        index: larger.index(size) + 1 if size in larger else len(larger) + 1
        for index, size in candidates.items()
    }
    return by_font | numbered


def _has_lowercase(token: str) -> bool:
    return any(c.islower() for c in token)


def split_caps_numbered_heading(text: str) -> tuple[str, str] | None:
    """Separa "1. DAS DISPOSICOES PRELIMINARES 11 O Concurso..." em (titulo, resto).

    Editais numeram secoes com titulos em caixa alta; no OCR o titulo costuma vir
    colado ao primeiro item. O titulo vai ate a ultima palavra sem minusculas antes
    de um numero ou de uma palavra com minusculas.
    """
    tokens = text.split()
    # O OCR as vezes troca o ponto da numeracao por virgula ("4, DAS INSCRIÇÕES").
    if len(tokens) < 3 or not re.fullmatch(r"\d{1,2}[.,]?", tokens[0]):
        return None
    end = 1
    while end < len(tokens) and not _has_lowercase(tokens[end]) and not re.fullmatch(r"[\d.,]+", tokens[end]):
        end += 1
    # Fragmentos curtos no fim ("7. DAS PROVAS TA O Concurso") sao ruido do OCR ou
    # inicio da frase seguinte: voltam para o texto.
    while end > 2 and len(tokens[end - 1]) <= 2 and not re.fullmatch(r"[IVX]+", tokens[end - 1]):
        end -= 1
    words = [t for t in tokens[1:end] if any(c.isalpha() for c in t)]
    letters = sum(c.isalpha() for w in words for c in w)
    if len(words) < 2 or letters < 8 or not any(len(w) >= 4 for w in words):
        return None
    heading = " ".join(tokens[:end]).replace(",", ".", 1) if tokens[0].endswith(",") else " ".join(tokens[:end])
    return heading, " ".join(tokens[end:])


ANNEX_HEADING = re.compile(r"^ANEXO\s+[IVXL\d|]+\b")


def split_caps_headings(blocks: list[Block]) -> list[Block]:
    """Promove a titulo os paragrafos que comecam com secao numerada em caixa alta."""
    result: list[Block] = []
    for block in blocks:
        if block.type == BlockType.PARAGRAPH and ANNEX_HEADING.match(block.text) and not _has_lowercase(block.text):
            result.append(Block(BlockType.HEADING, block.text, level=1, page=block.page, ocr=block.ocr))
            continue
        parts = split_caps_numbered_heading(block.text) if block.type == BlockType.PARAGRAPH else None
        if not parts:
            result.append(block)
            continue
        heading, rest = parts
        level = re.match(r"\d+", heading).group().count(".") + 1
        result.append(Block(BlockType.HEADING, heading.rstrip(":"), level=level, page=block.page, ocr=block.ocr))
        if rest:
            result.append(Block(BlockType.PARAGRAPH, rest, page=block.page, ocr=block.ocr))
    return result


def _prettify_filename(stem: str) -> str:
    name = re.sub(r"[_\s]+", " ", stem.replace("-", " "))
    name = re.sub(r"\s*\(?\d\)?\s*$", "", name).strip()
    return name[:1].upper() + name[1:] if name else stem


def guess_title(blocks: list[Block], path: Path) -> str:
    """Titulo do documento sem metadados.

    Primeira linha curta da primeira pagina que nao seja secao numerada (ex.:
    "EDITAL DE ABERTURA"), depois o primeiro titulo de nivel 1, por fim o nome
    do arquivo legivel.
    """
    first_page = [b for b in blocks if b.page == (blocks[0].page if blocks else 1)][:5]
    for block in first_page:
        text = block.text.strip()
        if 8 <= len(text) <= 120 and not re.match(r"\d", text) and block.type in (BlockType.HEADING, BlockType.PARAGRAPH):
            if block.type == BlockType.HEADING or not _has_lowercase(text):
                return text
    heading = next((b.text for b in blocks if b.type == BlockType.HEADING and b.level == 1), None)
    return heading or _prettify_filename(path.stem)


def _ocr_page(page: pymupdf.Page, number: int, ocr: OcrOptions) -> list[RawBlock]:
    textpage = page.get_textpage_ocr(language=ocr.language, dpi=ocr.dpi, full=True, tessdata=str(ocr.tessdata))
    return _read_page(page, number, textpage=textpage, ocr=True)


def _ocr_page_in_process(path: str, number: int, ocr: OcrOptions) -> list[RawBlock]:
    """Executado em processo separado: abre o PDF e reconhece uma pagina."""
    with pymupdf.open(path) as pdf:
        return _ocr_page(pdf[number - 1], number, ocr)


def ocr_workers(pages: int) -> int:
    """Um processo por nucleo livre (o Tesseract usa um nucleo por pagina), ate 8."""
    return max(1, min(pages, (os.cpu_count() or 2) - 1, 8))


def ocr_pages_parallel(
    path: Path, numbers: list[int], ocr: OcrOptions, progress: Callable[[str], None] | None = None
) -> dict[int, list[RawBlock]]:
    """Reconhece varias paginas em paralelo; paginas que falham ficam fora do resultado."""
    results: dict[int, list[RawBlock]] = {}
    total = len(numbers)

    def report() -> None:
        if progress:
            progress(f"Reconhecendo texto (OCR): {len(results)} de {total} página(s)")

    report()
    workers = ocr_workers(total)
    if workers > 1:
        try:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_ocr_page_in_process, str(path), n, ocr): n for n in numbers}
                for future in as_completed(futures):
                    number = futures[future]
                    try:
                        results[number] = future.result()
                    except Exception as exc:
                        log.warning("OCR falhou na página %d de %s: %s", number, path.name, exc)
                    report()
            return results
        except BrokenProcessPool as exc:
            log.warning("OCR em paralelo indisponível (%s); seguindo página a página.", exc)
    with pymupdf.open(str(path)) as pdf:
        for number in numbers:
            if number in results:
                continue
            try:
                results[number] = _ocr_page(pdf[number - 1], number, ocr)
            except Exception as exc:
                log.warning("OCR falhou na página %d de %s: %s", number, path.name, exc)
            report()
    return results


def _page_list(numbers: list[int]) -> str:
    return ", ".join(map(str, numbers[:10])) + ("…" if len(numbers) > 10 else "")


def extract_pdf(
    path: Path, ocr: OcrOptions | None = None, progress: Callable[[str], None] | None = None
) -> Extraction:
    """Le o PDF; paginas sem camada de texto passam por OCR quando `ocr` e informado."""
    try:
        pdf = pymupdf.open(str(path), filetype="pdf")
    except pymupdf.FileDataError as exc:
        raise ExtractionError(f"'{path.name}' não é um PDF válido ou está danificado.") from exc
    except Exception as exc:
        raise ExtractionError(f"Não foi possível abrir o PDF '{path.name}': {exc}") from exc
    if pdf.needs_pass:
        raise ExtractionError(f"O PDF '{path.name}' é protegido por senha.")

    with pdf:
        pages: dict[int, list[RawBlock]] = {}
        heights: dict[int, float] = {}
        widths: dict[int, float] = {}
        without_text: list[int] = []
        ocr_pages: list[int] = []
        ocr_failures = 0

        def char_count(blocks: list[RawBlock]) -> int:
            return sum(len(line.text) for b in blocks for line in b.lines)

        scanned: list[int] = []
        for index, page in enumerate(pdf):
            number = index + 1
            heights[number] = page.rect.height
            widths[number] = page.rect.width
            pages[number] = _read_page(page, number)
            if char_count(pages[number]) < MIN_PAGE_CHARS and page.get_images():
                scanned.append(number)

        recognized = ocr_pages_parallel(path, scanned, ocr, progress) if ocr and scanned else {}
        for number in scanned:
            blocks = recognized.get(number)
            if blocks is None and ocr:
                ocr_failures += 1
            if blocks and char_count(blocks) >= MIN_PAGE_CHARS:
                pages[number] = blocks
                ocr_pages.append(number)
            else:
                without_text.append(number)
        removed = _remove_headers_footers(pages, heights)
        toc = pdf.get_toc(simple=True)
        metadata_title = (pdf.metadata or {}).get("title", "").strip()
        author = (pdf.metadata or {}).get("author", "").strip()
        page_count = pdf.page_count

    paragraphs = [
        paragraph
        for number in sorted(pages)
        for block in order_blocks(pages[number], widths[number])
        for paragraph in split_paragraphs(block)
    ]
    body = _body_size(paragraphs)
    headings = _toc_headings(toc, paragraphs) if len(toc) >= 2 else {}
    if not headings:
        headings = _font_headings(paragraphs, body)

    blocks: list[Block] = []
    for index, paragraph in enumerate(paragraphs):
        text = paragraph.text
        if index in headings:
            blocks.append(
                Block(BlockType.HEADING, text, level=headings[index], page=paragraph.page, ocr=paragraph.ocr)
            )
            continue
        bottom = paragraph.lines[0].y0 >= heights[paragraph.page] * 0.7
        is_note = bottom and paragraph.size <= body * 0.88
        block_type = BlockType.CODE if paragraph.mono else BlockType.NOTE if is_note else BlockType.PARAGRAPH
        blocks.append(Block(block_type, text, page=paragraph.page, ocr=paragraph.ocr))
    blocks = split_caps_headings(blocks)

    # Navegadores e conversores gravam o nome do arquivo como titulo: nao serve.
    if re.search(r"\.(pdf|docx?|odt)$", metadata_title, re.IGNORECASE) or metadata_title in (path.name, path.stem):
        metadata_title = ""
    title = metadata_title or guess_title(blocks, path)
    scanned = len(ocr_pages) + len(without_text)
    kind = DocumentKind.PDF_OCR if scanned and scanned == page_count else DocumentKind.PDF
    document = Document(
        source=path,
        kind=kind,
        title=title,
        page_count=page_count,
        pages_without_text=without_text,
        ocr_pages=ocr_pages,
        author=author,
    )
    if ocr_pages:
        document.warnings.append(
            f"{len(ocr_pages)} página(s) digitalizada(s) lida(s) por OCR ({_page_list(ocr_pages)}). "
            "O texto reconhecido está destacado: revise antes de converter."
        )
    if without_text and ocr:
        failed = f"; o OCR falhou em {ocr_failures}" if ocr_failures else ""
        document.warnings.append(
            f"{len(without_text)} página(s) sem texto reconhecível ({_page_list(without_text)}){failed}."
        )
    elif without_text:
        document.warnings.append(
            f"{len(without_text)} página(s) sem camada de texto ({_page_list(without_text)}). "
            "Ative o OCR e instale os dados do idioma português para lê-las."
        )
    if removed:
        document.warnings.append(f"{removed} linha(s) de cabeçalho, rodapé ou número de página removidas.")
    return Extraction(document, blocks)
