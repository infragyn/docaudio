"""Importacao de dicionarios (NVDA, PLS, CSV/TSV) e dicionarios tematicos."""

import csv

import pytest

from docaudio.text_processing import dictionary_import as di
from docaudio.text_processing import pronunciation
from docaudio.text_processing.normalize import normalize
from docaudio.text_processing.pronunciation import Entry


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCAUDIO_DATA_DIR", str(tmp_path))
    pronunciation.set_themes(None)
    yield
    pronunciation.set_themes(None)


def terms(result):
    return [(e.term, e.spoken, e.case_sensitive) for e in result.entries]


def test_nvda_aceita_palavras_e_ignora_expressoes_regulares():
    text = (
        "# dicionario de teste\n"
        "SQL\tésse quê éle\t1\t2\n"
        "backup\tbékap\t0\t0\n"
        "^\\d+ºC$\tgraus\t0\t1\n"
        "linha sem tab\n"
    )
    result = di.parse_nvda(text)
    assert terms(result) == [("SQL", "ésse quê éle", True), ("backup", "békap", False)]
    assert [why for _, why in result.skipped] == [
        "expressão regular (o DocAudio substitui só palavras inteiras)",
        "formato desconhecido",
    ]


def test_pls_usa_alias_e_ignora_entradas_so_foneticas():
    text = """<?xml version="1.0" encoding="UTF-8"?>
<lexicon version="1.0" xmlns="http://www.w3.org/2005/01/pronunciation-lexicon" xml:lang="pt-BR">
  <lexeme><grapheme>W3C</grapheme><alias>dábliu três cê</alias></lexeme>
  <lexeme><grapheme>deadlock</grapheme><grapheme>dead-lock</grapheme><alias>dédlok</alias></lexeme>
  <lexeme><grapheme>tomate</grapheme><phoneme>to'matʃi</phoneme></lexeme>
</lexicon>"""
    result = di.parse_pls(text)
    assert terms(result) == [
        ("W3C", "dábliu três cê", True),
        ("deadlock", "dédlok", False),
        ("dead-lock", "dédlok", False),
    ]
    assert result.skipped == [("entrada 3", "só fonemas (formato fonético)")]


@pytest.mark.parametrize("text", [
    "termo,pronuncia\nYAML,iámel\nOAuth,ó auth\n",
    "YAML;iámel\nOAuth;ó auth\n",
    "YAML\tiámel\nOAuth\tó auth\n",
    "YAML=iámel\nOAuth=ó auth\n",
])
def test_planilhas_com_separadores_diferentes(text):
    assert terms(di.parse_table(text)) == [("YAML", "iámel", False), ("OAuth", "ó auth", False)]


def test_arquivo_detecta_formato_e_codificacao(tmp_path):
    path = tmp_path / "meu.dic"
    path.write_bytes("Kubernetes\tcubernétis\t0\t2\n".encode("utf-16"))
    assert terms(di.parse_file(path)) == [("Kubernetes", "cubernétis", False)]


def test_previa_separa_novas_iguais_e_conflitos_sem_duplicar():
    existing = [Entry("backup", "bécap", False), Entry("SQL", "ésse quê éle", True)]
    imported = [Entry("Backup", "békap", False), Entry("SQL", "ésse quê éle", True), Entry("YAML", "iámel", False),
                Entry("yaml", "iámel", False)]
    planned = di.plan_import(imported, existing)
    assert [(p.entry.term, p.status, p.current) for p in planned] == [
        ("Backup", di.Status.CONFLICT, "bécap"),
        ("SQL", di.Status.SAME, "ésse quê éle"),
        ("YAML", di.Status.NEW, ""),
    ]


def test_gravar_varias_pronuncias_troca_as_repetidas():
    pronunciation.save_user_entry("backup", "bécap")
    pronunciation.save_user_entries([Entry("Backup", "békap", False), Entry("YAML", "iámel", False)])
    with pronunciation.user_dictionary_file().open(encoding="utf-8") as handle:
        rows = [r for r in csv.reader(handle)][1:]
    assert [r[:2] for r in rows if not r[0].startswith("#")] == [["Backup", "békap"], ["YAML", "iámel"]]


def test_temas_corrigem_latim_siglas_e_podem_ser_desligados():
    dictionary = pronunciation.load_default()
    assert normalize("Cabe habeas corpus com efeito ex tunc.", dictionary) == (
        "Cabe rábeas córpus com efeito écs tunc."
    )
    assert normalize("A COFINS e a SELIC subiram.", dictionary) == "A cofíns e a sélic subiram."
    # Sigla que tambem e palavra: so em maiusculas.
    assert normalize("Eu sei que o processo está no SEI.", dictionary) == "Eu sei que o processo está no sêi."

    pronunciation.set_themes(["ti"])
    assert "habeas" in normalize("habeas corpus", pronunciation.load_default())


def test_todos_os_temas_tem_formato_valido():
    for name in pronunciation.THEMES:
        entries = pronunciation.read_csv(pronunciation.theme_file(name))
        assert entries, name
        assert all(e.term and e.spoken and e.term != e.spoken for e in entries), name
