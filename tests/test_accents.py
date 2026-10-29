import pytest

from docaudio.core.loader import load_document
from docaudio.storage.settings import Settings
from docaudio.text_processing.accents import AccentRestorer, learn_from, restore_word


@pytest.mark.parametrize(
    "word, expected",
    [
        ("Informacao", "Informação"),
        ("comparacoes", "comparações"),
        ("INSCRICOES", "INSCRIÇÕES"),
        ("versao", "versão"),
        ("consistencia", "consistência"),
        ("importancias", "importâncias"),
        ("possivel", "possível"),
        ("responsaveis", "responsáveis"),
        ("nao", "não"),
        ("NAO", "NÃO"),
        ("capitulo", "capítulo"),
        ("Modulo", "Módulo"),
        ("automatica", "automática"),
    ],
)
def test_restore_word(word, expected):
    assert restore_word(word) == expected


@pytest.mark.parametrize(
    "word",
    ["esta", "publico", "referencia", "continua", "pratica", "e", "a", "SO", "TI", "casa", "Informação", "API"],
)
def test_ambiguous_or_correct_words_are_kept(word):
    assert restore_word(word) == word


def test_learns_from_document_only_when_accented_form_dominates():
    texts = ["A informação e a informação.", "Seção de informação.", "Ver a Informacao.", "O que é?"] + ["que"] * 5 + ["Por quê?"]
    learned = learn_from(texts)
    assert learned["informacao"] == "informação"
    assert "que" not in learned
    assert restore_word("Secao", learned) == "Seção"


def test_restorer_counts_changes():
    restorer = AccentRestorer(["Gestão de serviços."])
    assert restorer.restore("Gestao de servicos nao e simples.") == "Gestão de serviços não e simples."
    assert restorer.total == 3


def test_loader_restores_and_warns(tmp_path):
    import docx

    doc = docx.Document()
    doc.add_heading("Modulo 1 Introducao", level=1)
    doc.add_paragraph("Este material nao contem questoes. A informação aparece acentuada aqui.")
    doc.add_paragraph("Mais uma informação sobre a Informacao.")
    path = tmp_path / "modulo.docx"
    doc.save(path)

    document = load_document(path, Settings())
    texts = [b.text for c in document.chapters for b in c.blocks]
    assert document.chapters[0].title == "Módulo 1 Introdução"
    assert "Este material não contem questões." in texts[0]
    assert texts[1] == "Mais uma informação sobre a Informação."
    assert any("sem acento" in w for w in document.warnings)

    off = Settings(restore_accents=False)
    assert load_document(path, off).chapters[0].title == "Modulo 1 Introducao"
