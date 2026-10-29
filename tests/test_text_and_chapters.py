from docaudio.core.chapters import build_chapters, choose_split_level
from docaudio.core.model import Block, BlockType, Chapter, Document, DocumentKind
from docaudio.core.rules import apply_rules
from docaudio.extractors.classify import classify_blocks
from docaudio.storage.settings import Settings
from docaudio.text_processing.cleanup import clean_text, join_lines, normalize_key


def h(text, level=1):
    return Block(BlockType.HEADING, text, level=level)


def p(text, kind=BlockType.PARAGRAPH):
    return Block(kind, text)


# ---- limpeza ----------------------------------------------------------------


def test_clean_text_removes_invisible_chars_and_ligatures():
    assert clean_text("  e­xem​plo   de\tdeﬁnição ") == "exemplo de definição"
    assert clean_text("Café") == "Café"


def test_join_lines_dehyphenates_only_lowercase_continuations():
    assert join_lines(["progra-", "mação"]) == "programação"
    assert join_lines(["Rio-", "Grande"]) == "Rio- Grande"
    assert join_lines(["uma linha", "", "outra"]) == "uma linha outra"


def test_normalize_key_ignores_case_accents_and_numbers():
    assert normalize_key("Página 3 de 10") == normalize_key("PAGINA 7 de 10")


# ---- classificacao ------------------------------------------------------------


def test_classify_lists_urls_and_toc_lines():
    blocks = classify_blocks(
        [p("• Primeiro item"), p("a) alternativa"), p("https://site.com/x"), p("Introdução ...... 3"), p("Texto.")]
    )
    assert [b.type for b in blocks] == [
        BlockType.LIST,
        BlockType.LIST,
        BlockType.URL,
        BlockType.TOC,
        BlockType.PARAGRAPH,
    ]
    assert blocks[0].text == "Primeiro item"


def test_reference_section_ends_at_next_heading_of_same_level():
    blocks = classify_blocks([h("Referências"), p("AUTOR. Obra."), h("Detalhe", 2), p("X."), h("Anexo"), p("Y.")])
    assert [b.type for b in blocks if b.type != BlockType.HEADING] == [
        BlockType.REFERENCE,
        BlockType.REFERENCE,
        BlockType.PARAGRAPH,
    ]


# ---- capitulos ----------------------------------------------------------------


def test_split_level_skips_single_document_title():
    assert choose_split_level([h("Livro", 1), h("Cap 1", 2), h("Cap 2", 2)]) == 2
    assert choose_split_level([h("Cap 1", 1), h("Cap 2", 1)]) == 1
    assert choose_split_level([p("sem titulos")]) is None


def test_chapters_by_heading_with_preamble_and_subheadings():
    chapters = build_chapters([p("Prefácio."), h("Um"), p("A."), h("Sub", 2), p("B."), h("Dois"), p("C.")])
    assert [c.title for c in chapters] == ["Início", "Um", "Dois"]
    assert [b.text for b in chapters[1].blocks] == ["A.", "Sub", "B."]
    assert [c.number for c in chapters] == [1, 2, 3]


def test_document_title_does_not_create_empty_chapter():
    chapters = build_chapters([h("Livro", 0), h("Um"), p("A."), h("Dois"), p("B.")])
    assert [c.title for c in chapters] == ["Um", "Dois"]


def test_without_headings_splits_by_size():
    chapters = build_chapters([p("x" * 60) for _ in range(5)], part_chars=130)
    assert [c.title for c in chapters] == ["Parte 1", "Parte 2", "Parte 3"]
    assert sum(len(c.blocks) for c in chapters) == 5


def test_long_chapters_are_split_in_parts():
    chapters = build_chapters([h("Longo"), *[p("y" * 50) for _ in range(4)], h("Curto"), p("z")], max_chars=120)
    assert [c.title for c in chapters] == ["Longo (parte 1)", "Longo (parte 2)", "Curto"]


def test_empty_document_has_no_chapters():
    assert build_chapters([]) == []


# ---- regras -------------------------------------------------------------------


def test_rules_follow_settings():
    chapter = Chapter(1, "C", [p("t", BlockType.TABLE), p("n", BlockType.NOTE), p("s", BlockType.TOC), p("x")])
    document = Document(source=None, kind=DocumentKind.DOCX, title="", chapters=[chapter])
    apply_rules(document, Settings(read_tables=False, read_notes=True))
    assert [b.include for b in chapter.blocks] == [False, True, False, True]
    assert chapter.narration_text() == "n\n\nx"
