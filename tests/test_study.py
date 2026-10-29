"""Modo questoes: pausa para pensar e resposta do gabarito depois de cada questao."""

from pathlib import Path

import pytest

from docaudio.core.model import Block, BlockType, Chapter, Document, DocumentKind
from docaudio.core.study import answer_key, study_chapter
from docaudio.text_processing.segmenter import segment_chapter

P, L, T = BlockType.PARAGRAPH, BlockType.LIST, BlockType.TABLE


def chapters():
    simulado = Chapter(14, "Simulado", [
        Block(P, "Tempo sugerido: 45 minutos."),
        Block(P, "30. Uma tabela hash oferece busca de ordem"),
        Block(L, "(A) O(1)"),
        Block(L, "(B) O(log n)"),
        Block(P, "31. Discursiva: explique o polimorfismo."),
        Block(P, "Texto comum depois."),
    ])
    gabarito = Chapter(15, "Gabarito comentado", [
        Block(T, "Questão: 29; Resposta: B; Comentário: Algo.\n"
                 "Questão: 30; Resposta: A; Comentário: Hash tem acesso médio constante\n"
                 "Questão: 31; Resposta: C; Comentário: Outra."),
        Block(T, "Resultado: 90 por cento; Ação: avançar."),
    ])
    revisao = Chapter(16, "Revisão", [Block(P, "Pergunta: Qual registro DNS representa IPv6? Resposta: AAAA.")])
    return [simulado, gabarito, revisao]


def document():
    return Document(source=Path("x.docx"), kind=DocumentKind.DOCX, title="Material", chapters=chapters())


def texts(chapter):
    return [(b.text, b.pause_after_ms) for b in chapter.included_blocks()]


def test_gabarito_vira_dicionario_de_respostas_com_comentario():
    key = answer_key(document())
    assert key[30].letter == "A"
    assert key[30].comment == "Hash tem acesso médio constante."
    assert set(key) == {29, 30, 31}


def test_resposta_vem_depois_da_ultima_alternativa_com_pausa_para_pensar():
    doc = document()
    simulado = study_chapter(doc.chapters[0], answer_key(doc), 5000)
    assert texts(simulado) == [
        ("Tempo sugerido: 45 minutos.", None),
        ("30. Uma tabela hash oferece busca de ordem", None),
        ("(A) O(1)", None),
        ("(B) O(log n)", 5000),
        ("Resposta da questão 30: letra A. Hash tem acesso médio constante.", None),
        # Questao sem alternativas (discursiva): nada muda.
        ("31. Discursiva: explique o polimorfismo.", None),
        ("Texto comum depois.", None),
    ]
    # O documento original nao e alterado.
    assert doc.chapters[0].blocks[3].pause_after_ms is None


def test_tabela_de_gabarito_deixa_de_ser_lida_mas_outras_tabelas_continuam():
    doc = document()
    gabarito = study_chapter(doc.chapters[1], answer_key(doc), 5000)
    assert texts(gabarito) == [("Resultado: 90 por cento; Ação: avançar.", None)]


def test_cartao_de_revisao_pausa_antes_da_resposta():
    doc = document()
    revisao = study_chapter(doc.chapters[2], answer_key(doc), 4000)
    assert texts(revisao) == [("Pergunta: Qual registro DNS representa IPv6?", 4000), ("Resposta: AAAA.", None)]
    segments = segment_chapter(revisao, lambda t: t, announce_title=False, paragraph_pause_ms=700)
    assert [s.pause_after_ms for s in segments] == [4000, 700]


def test_sem_gabarito_questoes_ficam_como_estao():
    simulado = chapters()[0]
    assert texts(study_chapter(simulado, {}, 5000)) == texts(simulado)


@pytest.mark.parametrize("name", sorted(Path("docs texte").glob("material_estudo_*.docx")))
def test_materiais_reais_tem_resposta_para_cada_questao(name):
    from docaudio.core.loader import load_document
    from docaudio.storage.settings import Settings

    doc = load_document(name, Settings())
    key = answer_key(doc)
    answers = [
        b.text for c in doc.chapters for b in study_chapter(c, key, 5000).included_blocks()
        if b.text.startswith("Resposta da questão")
    ]
    assert key and len(answers) == len(key)
