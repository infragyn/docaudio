import pymupdf

from docaudio.extractors.pdf_extractor import (
    Line,
    RawBlock,
    _font_headings,
    _looks_like_ocr_heading,
    _read_line,
    extract_pdf,
    _remove_headers_footers,
    order_blocks,
    split_paragraphs,
)


def line(text, x0, y0, x1=None, size=11, bold=False, page=1):
    return Line(text, x0, y0, x1 if x1 is not None else x0 + 200, y0 + size, size, bold, page)


def block(*lines):
    return RawBlock(list(lines))


def test_superscript_footnote_numbers_are_dropped():
    raw = {
        "bbox": (0, 0, 100, 10),
        "spans": [
            {"text": "Texto", "flags": 0, "size": 11, "font": "Helvetica"},
            {"text": "1", "flags": pymupdf.TEXT_FONT_SUPERSCRIPT, "size": 7, "font": "Helvetica"},
            {"text": " final", "flags": pymupdf.TEXT_FONT_BOLD, "size": 11, "font": "Helvetica"},
        ],
    }
    result = _read_line(raw, 1)
    assert result.text == "Texto final"
    assert result.bold is False


def test_repeated_headers_and_page_numbers_are_removed():
    pages = {
        n: [block(line("Curso de Redes", 50, 20)), block(line(f"Conteúdo {n}", 50, 300)), block(line(str(n), 290, 820))]
        for n in (1, 2, 3)
    }
    pages[2].append(block(line("Curso de Redes no corpo", 50, 400)))
    removed = _remove_headers_footers(pages, {n: 842 for n in pages})
    assert removed == 6
    assert [[b.lines[0].text for b in pages[n]] for n in (1, 2)] == [
        ["Conteúdo 1"],
        ["Conteúdo 2", "Curso de Redes no corpo"],
    ]


def test_order_blocks_reads_left_column_then_right():
    title = block(line("Título", 50, 50, 545))
    left1, left2 = block(line("L1", 50, 100, 280)), block(line("L2", 50, 300, 280))
    right1, right2 = block(line("R1", 310, 100, 545)), block(line("R2", 310, 300, 545))
    after = block(line("Fim curto", 50, 500, 150))
    ordered = order_blocks([right2, after, left2, right1, title, left1], 595)
    assert [b.lines[0].text for b in ordered] == ["Título", "L1", "L2", "R1", "R2", "Fim curto"]


def test_single_column_is_sorted_top_to_bottom():
    ordered = order_blocks([block(line("B", 50, 200)), block(line("A", 50, 100))], 595)
    assert [b.lines[0].text for b in ordered] == ["A", "B"]


def ocr_line(text, y0, size=12):
    return Line(text, 50, y0, 400, y0 + size, size, False, 1, ocr=True)


def test_ocr_heading_pattern():
    assert _looks_like_ocr_heading("1.1 Átomos")
    assert _looks_like_ocr_heading("2 Ligações Químicas")
    assert not _looks_like_ocr_heading("1. Este é um item de lista que termina com ponto.")
    assert not _looks_like_ocr_heading("2 bananas e uma maçã")
    assert not _looks_like_ocr_heading("Introdução")


def test_ocr_paragraphs_tolerate_size_noise_and_split_numbered_headings():
    raw = block(
        ocr_line("1 Introdução", 100, size=13.7),
        ocr_line("A química estuda a composição", 116, size=12.1),
        ocr_line("e as propriedades da matéria.", 130, size=10.8),
    )
    assert [p.text for p in split_paragraphs(raw)] == [
        "1 Introdução",
        "A química estuda a composição e as propriedades da matéria.",
    ]


def test_ocr_headings_use_numbering_depth():
    paragraphs = split_paragraphs(block(ocr_line("2 Variáveis", 100))) + split_paragraphs(
        block(ocr_line("2.1 Tipos", 200), ocr_line("Texto comum sem numeração", 230))
    )
    assert _font_headings(paragraphs, 12) == {0: 1, 1: 2}


def test_scanned_page_without_ocr_is_reported(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), False)
    pix.clear_with(255)
    page.insert_image(page.rect, pixmap=pix)
    doc.save(tmp_path / "scan.pdf")
    extraction = extract_pdf(tmp_path / "scan.pdf", ocr=None)
    assert extraction.document.pages_without_text == [1]
    assert "Ative o OCR" in extraction.document.warnings[0]


def test_split_paragraphs_by_gap_and_font_change():
    raw = block(
        line("Título", 50, 100, size=16, bold=True),
        line("primeira linha", 50, 120),
        line("segunda linha", 50, 134),
        line("novo parágrafo", 50, 170),
    )
    assert [p.text for p in split_paragraphs(raw)] == ["Título", "primeira linha segunda linha", "novo parágrafo"]
