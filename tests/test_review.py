"""Revisao de pronuncia: termos arriscados, dicionario e termos ja revisados."""

from pathlib import Path

from docaudio.core.model import Block, BlockType, Chapter, Document, DocumentKind
from docaudio.text_processing import review
from docaudio.text_processing.pronunciation import Entry, PronunciationDictionary


def document():
    text = (
        "A API REST usa JSON e HTTP. O protocolo IPv6 e o JavaScript rodam na web. "
        "Fazer backup evita deadlock? A compreensão da cooperação é importante. REST de novo."
    )
    blocks = [Block(BlockType.PARAGRAPH, text), Block(BlockType.CODE, "const thread = fetch(url);")]
    return Document(Path("x.docx"), DocumentKind.DOCX, "Doc", [Chapter(1, "Capítulo", blocks)])


def test_encontra_siglas_estrangeiras_e_nomes_tecnicos(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCAUDIO_DATA_DIR", str(tmp_path))
    found = {c.term: c for c in review.candidates(document(), PronunciationDictionary())}
    assert found["REST"].reason == "sigla" and found["REST"].count == 2
    assert found["IPv6"].reason == "tecnico"
    assert found["JavaScript"].reason == "tecnico"
    assert found["backup"].reason == "estrangeira"
    assert found["deadlock"].reason == "estrangeira"
    # Palavras portuguesas com "ee"/"oo" e codigo-fonte nao entram.
    assert not {"compreensão", "cooperação", "thread", "const"} & set(found)
    # Mais frequentes primeiro.
    assert next(iter(found)) == "REST"


def test_dicionario_e_termos_revisados_saem_da_lista(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCAUDIO_DATA_DIR", str(tmp_path))
    dictionary = PronunciationDictionary([Entry("JSON", "jêison", False)])
    review.mark_reviewed(["HTTP", "web"])
    terms = {c.term for c in review.candidates(document(), dictionary)}
    assert not {"JSON", "HTTP", "web"} & terms
    assert "REST" in terms
    assert review.load_reviewed() == {"HTTP", "web"}
