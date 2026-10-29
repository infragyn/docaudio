from docaudio.core.documents import split_supported


def test_accepts_docx_and_pdf_case_insensitive(tmp_path):
    files = [tmp_path / "a.docx", tmp_path / "b.PDF", tmp_path / "c.odt"]
    for f in files:
        f.write_text("x")
    accepted, rejected = split_supported(files)
    assert [p.name for p in accepted] == ["a.docx", "b.PDF"]
    assert [p.name for p in rejected] == ["c.odt"]


def test_removes_duplicates_and_keeps_order(tmp_path):
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    a.write_text("x")
    b.write_text("x")
    accepted, _ = split_supported([b, a, b, tmp_path / "." / "a.pdf"])
    assert [p.name for p in accepted] == ["b.pdf", "a.pdf"]


def test_rejects_missing_folders_and_word_lock_files(tmp_path):
    lock = tmp_path / "~$apostila.docx"
    lock.write_text("x")
    folder = tmp_path / "pasta.pdf"
    folder.mkdir()
    accepted, rejected = split_supported([lock, folder, tmp_path / "sumiu.docx"])
    assert accepted == []
    assert len(rejected) == 3
