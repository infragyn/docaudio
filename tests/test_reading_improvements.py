"""Melhorias de leitura avaliadas com editais e apostilas de concurso."""

from pathlib import Path

import pytest

from docaudio.core.model import Block, BlockType
from docaudio.extractors.docx_extractor import format_table
from docaudio.extractors.pdf_extractor import (
    _prettify_filename,
    guess_title,
    split_caps_headings,
    split_caps_numbered_heading,
)
from docaudio.text_processing.normalize import normalize
from docaudio.text_processing.pronunciation import load_default


@pytest.fixture(scope="module")
def dictionary():
    return load_default()


# ---- titulos de edital -------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1. DAS DISPOSIÇÕES PRELIMINARES 11 O Concurso Público realizar-se-á", ("1. DAS DISPOSIÇÕES PRELIMINARES", "11 O Concurso Público realizar-se-á")),
        ("4, DAS INSCRIÇÕES 41 A inscrição do candidato", ("4. DAS INSCRIÇÕES", "41 A inscrição do candidato")),
        ("14. DA HOMOLOGAÇÃO 141 O resultado final", ("14. DA HOMOLOGAÇÃO", "141 O resultado final")),
        ("7. DAS PROVAS TA O Concurso constará", ("7. DAS PROVAS", "TA O Concurso constará")),
        ("10. DA PROVA DISCURSIVA PARA O CARGO DE TÉCNICO:", ("10. DA PROVA DISCURSIVA PARA O CARGO DE TÉCNICO:", "")),
    ],
)
def test_split_caps_numbered_heading(text, expected):
    assert split_caps_numbered_heading(text) == expected


@pytest.mark.parametrize(
    "text",
    ["1. O candidato deverá comparecer.", "2 TCP e UDP são protocolos.", "3 CPU GPU", "Texto sem número"],
)
def test_not_a_caps_heading(text):
    assert split_caps_numbered_heading(text) is None


def test_split_caps_headings_creates_heading_and_annex():
    blocks = split_caps_headings(
        [
            Block(BlockType.PARAGRAPH, "2. DO CARGO E VAGAS 21 O cargo exige nível médio.", page=2, ocr=True),
            Block(BlockType.PARAGRAPH, "ANEXO II CRONOGRAMA DAS PROVAS", page=20, ocr=True),
        ]
    )
    assert [(b.type, b.text, b.level) for b in blocks] == [
        (BlockType.HEADING, "2. DO CARGO E VAGAS", 1),
        (BlockType.PARAGRAPH, "21 O cargo exige nível médio.", 0),
        (BlockType.HEADING, "ANEXO II CRONOGRAMA DAS PROVAS", 1),
    ]
    assert all(b.ocr for b in blocks)


def test_guess_title_and_filename():
    blocks = [
        Block(BlockType.PARAGRAPH, "TRIBUNAL DE CONTAS DO ESTADO DE GOIÁS", page=1),
        Block(BlockType.HEADING, "1. DAS DISPOSIÇÕES PRELIMINARES", level=1, page=1),
    ]
    assert guess_title(blocks, Path("x.pdf")) == "TRIBUNAL DE CONTAS DO ESTADO DE GOIÁS"
    assert guess_title([], Path("edital_de_retificacao__3_.pdf")) == "Edital de retificacao"
    assert _prettify_filename("apostila-final") == "Apostila final"


# ---- normalizacao ------------------------------------------------------------


def test_caps_runs_read_as_text_but_acronyms_stay(dictionary):
    assert normalize("O cargo de TÉCNICO DE CONTROLE EXTERNO exige nível médio.", dictionary) == (
        "O cargo de técnico de controle externo exige nível médio."
    )
    assert normalize("Use TCP IP.", dictionary) == "Use tê cê pê í pê."


def test_sql_and_english_terms(dictionary):
    assert normalize("SELECT setor FROM servidores GROUP BY setor HAVING COUNT(*) > 5;", dictionary) == (
        "silécti setor fróm servidores grúpi bai setor révin cáunti maior que cinco;"
    )
    assert normalize("O phishing exige hardening.", dictionary) == "O fíxin exige rárdenin."


def test_alternatives_and_answer_letters(dictionary):
    assert normalize("(A) Pilha\n(B) Fila\nC) Árvore", dictionary) == (
        "Alternativa á: Pilha\nAlternativa bê: Fila\nAlternativa cê: Árvore"
    )
    assert normalize("Resposta: A. Gabarito E.", dictionary) == "Resposta: á. Gabarito é."
    assert normalize("A pilha e a vitamina C.", dictionary) == "A pilha e a vitamina cê."


def test_big_o_notation(dictionary):
    assert normalize("Busca binária é O(log n) e força bruta O(2ⁿ).", dictionary) == (
        "Busca binária é ó de log de n e força bruta ó de dois elevado a n."
    )
    assert normalize("Bubble sort é O(n²); hash é O(1).", dictionary).endswith("ó de n ao quadrado; hash é ó de um.")


# ---- tabelas -----------------------------------------------------------------


def test_answer_key_table_uses_header():
    text = format_table(
        [
            ["Questão", "Resposta", "Comentário"],
            ["1", "C", "Pilha segue LIFO."],
            ["2", "B", "Busca binária exige vetor ordenado."],
        ]
    )
    assert text == (
        "Questão: 1; Resposta: C; Comentário: Pilha segue LIFO.\n"
        "Questão: 2; Resposta: B; Comentário: Busca binária exige vetor ordenado."
    )


def test_table_without_header_and_no_double_period():
    assert format_table([["Pilha", "LIFO."], ["Pilha", "LIFO."]]) == "Pilha; LIFO.\nPilha; LIFO."
    assert format_table([["Só uma célula"]]) == "Só uma célula."
