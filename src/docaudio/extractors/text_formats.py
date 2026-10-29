"""Leitura de TXT, HTML, EPUB e PPTX so com a biblioteca padrao.

EPUB e PPTX sao arquivos ZIP com XML/XHTML dentro; HTML usa o html.parser.
Todos produzem a mesma sequencia de blocos que o DOCX e o PDF.
"""

import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from docaudio.core.model import Block, BlockType, Document, DocumentKind
from docaudio.extractors.base import Extraction, ExtractionError
from docaudio.text_processing.cleanup import clean_text

# ---- TXT ----------------------------------------------------------------------

# Titulo provavel em texto puro: "Capítulo 3", "2.1 Redes", "PARTE II" ou linha curta toda em maiusculas.
TXT_HEADING = re.compile(
    r"^(?:(?:cap[ií]tulo|parte|se[cç][aã]o|m[oó]dulo|unidade)\s+\S+.*|\d+(?:\.\d+)*\.?\s+\S.{0,70}|[A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9 ,:;–-]{4,70})$",
    re.IGNORECASE,
)
ENDS_SENTENCE = re.compile(r"[.!?;:,]$")


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def extract_txt(path: Path) -> Extraction:
    try:
        text = _read_text(path).replace("\r\n", "\n")
    except OSError as exc:
        raise ExtractionError(f"Não foi possível abrir '{path.name}': {exc}") from exc
    blocks = []
    for chunk in re.split(r"\n\s*\n", text):
        lines = [clean_text(line) for line in chunk.splitlines() if line.strip()]
        if not lines:
            continue
        joined = " ".join(lines)
        if len(lines) == 1 and TXT_HEADING.match(joined) and not ENDS_SENTENCE.search(joined):
            level = joined.count(".") + 1 if re.match(r"\d", joined) else 1
            blocks.append(Block(BlockType.HEADING, joined, level=min(level, 3)))
        else:
            blocks.append(Block(BlockType.PARAGRAPH, joined))
    document = Document(source=path, kind=DocumentKind.TXT, title=path.stem)
    return Extraction(document, blocks)


# ---- HTML ---------------------------------------------------------------------

HEADING_TAGS = {f"h{n}": n for n in range(1, 7)}
BLOCK_TAGS = {"p", "li", "blockquote", "dd", "dt", "figcaption", "caption", "div", "section", "article"}
# O <head> nao entra aqui: o <title> dele vira o titulo do documento.
SKIP_TAGS = {"script", "style", "nav", "noscript", "svg", "template", "button", "form"}


class _HtmlBlocks(HTMLParser):
    """Converte HTML em blocos: titulos, paragrafos, itens de lista, tabelas e codigo."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self.title = ""
        self._text: list[str] = []
        self._skip = 0
        self._in_title = False
        self._pre = 0
        self._list_item = 0
        self._table_rows: list[list[str]] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "table":
            self._flush()
            self._table_rows = []
        elif tag == "tr" and self._table_rows is not None:
            self._table_rows.append([])
        elif tag in ("td", "th") and self._table_rows is not None:
            self._cell = []
        elif tag == "pre":
            self._flush()
            self._pre += 1
        elif tag == "br":
            self._text.append("\n" if self._pre else " ")
        elif tag in HEADING_TAGS or tag in BLOCK_TAGS:
            self._flush()
            if tag == "li":
                self._list_item += 1

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in ("td", "th") and self._cell is not None and self._table_rows:
            self._table_rows[-1].append(clean_text(" ".join(self._cell)))
            self._cell = None
        elif tag == "table" and self._table_rows is not None:
            rows = ["; ".join(c for c in row if c) for row in self._table_rows]
            text = "\n".join(r for r in rows if r)
            if text:
                self.blocks.append(Block(BlockType.TABLE, text))
            self._table_rows = None
        elif tag == "pre":
            code = "".join(self._text).strip("\n")
            self._text = []
            self._pre = max(0, self._pre - 1)
            for line in code.splitlines():
                if line.strip():
                    self.blocks.append(Block(BlockType.CODE, line.rstrip()))
        elif tag in HEADING_TAGS:
            self._flush(BlockType.HEADING, HEADING_TAGS[tag])
        elif tag in BLOCK_TAGS:
            self._flush(BlockType.LIST if tag == "li" else BlockType.PARAGRAPH)
            if tag == "li":
                self._list_item = max(0, self._list_item - 1)

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title += data
        elif self._cell is not None:
            self._cell.append(data)
        elif self._table_rows is None:
            self._text.append(data)

    def _flush(self, kind: BlockType = BlockType.PARAGRAPH, level: int = 0) -> None:
        text = clean_text(re.sub(r"\s+", " ", "".join(self._text)))
        self._text = []
        if not text:
            return
        if kind == BlockType.PARAGRAPH and self._list_item:
            kind = BlockType.LIST
        self.blocks.append(Block(kind, text, level=level))

    def close(self):
        super().close()
        self._flush()


def html_blocks(markup: str) -> tuple[str, list[Block]]:
    parser = _HtmlBlocks()
    parser.feed(markup)
    parser.close()
    return clean_text(parser.title), parser.blocks


def extract_html(path: Path) -> Extraction:
    try:
        title, blocks = html_blocks(_read_text(path))
    except OSError as exc:
        raise ExtractionError(f"Não foi possível abrir '{path.name}': {exc}") from exc
    document = Document(source=path, kind=DocumentKind.HTML, title=title or path.stem)
    return Extraction(document, blocks)


# ---- EPUB ---------------------------------------------------------------------

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def _zip_path(base: str, href: str) -> str:
    """Caminho dentro do ZIP de um href relativo ao arquivo `base`."""
    parts = []
    for part in (PurePosixPath(base).parent / href.split("#")[0]).parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part != ".":
            parts.append(part)
    return "/".join(parts)


def extract_epub(path: Path) -> Extraction:
    try:
        with zipfile.ZipFile(path) as book:
            container = ElementTree.fromstring(book.read("META-INF/container.xml"))
            opf_path = container.find(".//c:rootfile", NS).get("full-path")
            opf = ElementTree.fromstring(book.read(opf_path))
            title = (opf.findtext(".//dc:title", default="", namespaces=NS) or "").strip()
            author = (opf.findtext(".//dc:creator", default="", namespaces=NS) or "").strip()
            manifest = {item.get("id"): item for item in opf.iterfind(".//opf:manifest/opf:item", NS)}
            blocks: list[Block] = []
            for ref in opf.iterfind(".//opf:spine/opf:itemref", NS):
                item = manifest.get(ref.get("idref"))
                if item is None or "html" not in (item.get("media-type") or ""):
                    continue
                if ref.get("linear") == "no":
                    continue
                markup = book.read(_zip_path(opf_path, item.get("href"))).decode("utf-8", errors="replace")
                blocks.extend(html_blocks(markup)[1])
    except (OSError, KeyError, AttributeError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise ExtractionError(f"Não foi possível ler o EPUB '{path.name}': {exc}") from exc
    if not blocks:
        raise ExtractionError(f"O EPUB '{path.name}' não tem texto (pode estar protegido por DRM).")
    document = Document(source=path, kind=DocumentKind.EPUB, title=title or path.stem, author=author)
    return Extraction(document, blocks)


# ---- PPTX ---------------------------------------------------------------------

PPT_NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
TITLE_PLACEHOLDERS = {"title", "ctrTitle"}


def _relationships(book: zipfile.ZipFile, part: str) -> dict[str, str]:
    rels_path = str(PurePosixPath(part).parent / "_rels" / (PurePosixPath(part).name + ".rels"))
    try:
        rels = ElementTree.fromstring(book.read(rels_path))
    except KeyError:
        return {}
    return {r.get("Id"): _zip_path(part, r.get("Target")) for r in rels.iterfind("rel:Relationship", PPT_NS)}


def _shape_paragraphs(shape) -> list[str]:
    paragraphs = []
    for paragraph in shape.iterfind(".//a:p", PPT_NS):
        text = clean_text("".join(t.text or "" for t in paragraph.iterfind(".//a:t", PPT_NS)))
        if text:
            paragraphs.append(text)
    return paragraphs


def _slide_blocks(xml: bytes, number: int) -> list[Block]:
    root = ElementTree.fromstring(xml)
    title, body = "", []
    for shape in root.iterfind(".//p:sp", PPT_NS):
        placeholder = shape.find(".//p:nvPr/p:ph", PPT_NS)
        paragraphs = _shape_paragraphs(shape)
        if placeholder is not None and placeholder.get("type") in TITLE_PLACEHOLDERS and not title:
            title = " ".join(paragraphs)
        else:
            body.extend(paragraphs)
    for frame in root.iterfind(".//a:tbl", PPT_NS):
        rows = []
        for row in frame.iterfind("a:tr", PPT_NS):
            cells = [" ".join(_shape_paragraphs(cell)) for cell in row.iterfind("a:tc", PPT_NS)]
            rows.append("; ".join(c for c in cells if c))
        if any(rows):
            body.append(None)  # marcador: a tabela entra como bloco proprio
            body.append("\n".join(r for r in rows if r))
    # Slide sem titulo continua o capitulo anterior (nao vale narrar "Slide 7").
    blocks = [Block(BlockType.HEADING, title, level=1)] if title else []
    table_next = False
    for text in body:
        if text is None:
            table_next = True
            continue
        blocks.append(Block(BlockType.TABLE if table_next else BlockType.LIST, text))
        table_next = False
    return blocks


def extract_pptx(path: Path, include_notes: bool = True) -> Extraction:
    try:
        with zipfile.ZipFile(path) as book:
            presentation = "ppt/presentation.xml"
            root = ElementTree.fromstring(book.read(presentation))
            rels = _relationships(book, presentation)
            slides = [
                rels[s.get(f"{{{PPT_NS['r']}}}id")]
                for s in root.iterfind(".//p:sldIdLst/p:sldId", PPT_NS)
                if s.get(f"{{{PPT_NS['r']}}}id") in rels
            ]
            title = author = ""
            try:
                core = ElementTree.fromstring(book.read("docProps/core.xml"))
                title = (core.findtext(".//dc:title", default="", namespaces=NS) or "").strip()
                author = (core.findtext(".//dc:creator", default="", namespaces=NS) or "").strip()
            except KeyError:
                pass
            blocks: list[Block] = []
            for number, slide in enumerate(slides, start=1):
                blocks.extend(_slide_blocks(book.read(slide), number))
                notes = [t for t in _relationships(book, slide).values() if "notesSlide" in t]
                if include_notes and notes:
                    note_root = ElementTree.fromstring(book.read(notes[0]))
                    for shape in note_root.iterfind(".//p:sp", PPT_NS):
                        placeholder = shape.find(".//p:nvPr/p:ph", PPT_NS)
                        if placeholder is not None and placeholder.get("type") == "body":
                            text = " ".join(_shape_paragraphs(shape))
                            if text:
                                blocks.append(Block(BlockType.PARAGRAPH, text))
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise ExtractionError(f"Não foi possível ler o PowerPoint '{path.name}': {exc}") from exc
    document = Document(source=path, kind=DocumentKind.PPTX, title=title or path.stem, author=author)
    return Extraction(document, blocks)
