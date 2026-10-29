from docaudio.core.editing import apply_chapter_text, split_edited_text
from docaudio.core.model import Block, BlockType, Chapter


def chapter(*texts, excluded_at=None):
    blocks = [Block(BlockType.PARAGRAPH, t, page=1) for t in texts]
    if excluded_at is not None:
        hidden = Block(BlockType.NOTE, "nota oculta", include=False)
        blocks.insert(excluded_at, hidden)
    return Chapter(1, "C", blocks)


def test_split_keeps_single_line_breaks():
    assert split_edited_text("a\nb\n\n\n  c  \n") == ["a\nb", "c"]


def test_unchanged_text_returns_false():
    c = chapter("A", "B")
    assert apply_chapter_text(c, "A\n\nB") is False


def test_edit_updates_block_and_keeps_original():
    c = chapter("A", "B", "C")
    original = c.blocks[1]
    assert apply_chapter_text(c, "A\n\nB editado\n\nC")
    assert c.blocks[1] is original
    assert original.text == "B editado"
    assert original.original_text == "B"
    assert original.edited


def test_delete_insert_and_preserve_hidden_blocks():
    c = chapter("A", "B", "C", excluded_at=2)
    apply_chapter_text(c, "Novo\n\nA\n\nC\n\nFim")
    assert [b.text for b in c.blocks] == ["Novo", "A", "nota oculta", "C", "Fim"]
    assert c.blocks[0].type == BlockType.PARAGRAPH and c.blocks[0].page == 1
    assert c.blocks[2].include is False


def test_replacing_with_more_paragraphs_adds_blocks():
    c = chapter("A", "B")
    apply_chapter_text(c, "A\n\nB1\n\nB2")
    assert [b.text for b in c.blocks] == ["A", "B1", "B2"]


def test_clearing_everything_keeps_only_hidden_blocks():
    c = chapter("A", "B", excluded_at=0)
    apply_chapter_text(c, "")
    assert [b.text for b in c.blocks] == ["nota oculta"]
