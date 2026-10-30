"""Modo questoes: tempo para pensar e resposta logo depois de cada questao.

Aplicado so na narracao (e na previa "Ver como sera falado"): o documento
editavel continua igual. Reconhece:

- questoes numeradas ("30. Enunciado") seguidas de alternativas "(A)".."(E)",
  com a resposta tirada da tabela de gabarito ("Questão: 30; Resposta: A; ...");
- cartoes de revisao no formato "Pergunta: ... Resposta: ...".
"""

import re
from dataclasses import dataclass, replace

from docaudio.core.language import phrase
from docaudio.core.model import Block, BlockType, Chapter, Document

QUESTION = re.compile(r"^\s*(\d{1,3})[.)]\s+\S")
ALTERNATIVE = re.compile(r"^\s*\(?[A-Ea-e]\)\s+\S")
KEY_ROW = re.compile(r"^\s*Quest[aã]o:\s*(\d{1,3});\s*Resposta:\s*([A-Ea-e])\s*(?:;(.*))?$", re.IGNORECASE)
COMMENT_FIELD = re.compile(r"Coment[aá]rio:\s*(.+)", re.IGNORECASE)
FLASHCARD = re.compile(r"^\s*Pergunta:\s*(.+?)\s+Resposta:\s*(.+?)\s*$", re.IGNORECASE | re.DOTALL)
# Um gabarito de verdade tem varias linhas; evita confundir uma tabela qualquer.
MIN_KEY_ROWS = 3


@dataclass(frozen=True)
class Answer:
    letter: str
    comment: str = ""


def _key_rows(block: Block) -> list[re.Match] | None:
    if block.type != BlockType.TABLE:
        return None
    lines = [line for line in block.text.splitlines() if line.strip()]
    matches = [KEY_ROW.match(line) for line in lines]
    if len(lines) < MIN_KEY_ROWS or not all(matches):
        return None
    return matches


def is_answer_key(block: Block) -> bool:
    return _key_rows(block) is not None


def answer_key(document: Document) -> dict[int, Answer]:
    """Respostas de todas as tabelas de gabarito do documento, pelo numero da questao."""
    answers: dict[int, Answer] = {}
    for chapter in document.chapters:
        for block in chapter.blocks:
            for match in _key_rows(block) or []:
                comment = COMMENT_FIELD.search(match.group(3) or "")
                answers[int(match.group(1))] = Answer(
                    match.group(2).upper(), comment.group(1).strip().rstrip(".") + "." if comment else ""
                )
    return answers


def answer_text(number: int, answer: Answer, language: str = "pt") -> str:
    text = phrase("answer", language, number=number, letter=answer.letter)
    return f"{text} {answer.comment}" if answer.comment else text


def study_chapter(chapter: Chapter, key: dict[int, Answer], think_ms: int, language: str = "pt") -> Chapter:
    """Copia do capitulo com pausas para pensar e as respostas no lugar certo."""
    blocks: list[Block] = []
    question: int | None = None
    source = chapter.blocks
    for index, block in enumerate(source):
        if key and is_answer_key(block):
            # As respostas ja foram lidas junto das questoes.
            blocks.append(replace(block, include=False))
            continue
        if not block.include:
            blocks.append(block)
            continue
        card = FLASHCARD.match(block.text) if block.type in (BlockType.PARAGRAPH, BlockType.LIST) else None
        if card:
            blocks.append(replace(block, text=f"Pergunta: {card.group(1)}", pause_after_ms=think_ms))
            blocks.append(replace(block, text=f"Resposta: {card.group(2)}", pause_after_ms=None))
            continue
        if block.type == BlockType.PARAGRAPH and (match := QUESTION.match(block.text)):
            question = int(match.group(1))
        elif question is not None and ALTERNATIVE.match(block.text):
            following = next((b for b in source[index + 1:] if b.include), None)
            last_alternative = following is None or not ALTERNATIVE.match(following.text)
            if last_alternative and question in key:
                blocks.append(replace(block, pause_after_ms=think_ms))
                blocks.append(Block(BlockType.PARAGRAPH, answer_text(question, key[question], language), page=block.page))
                question = None
                continue
        elif block.type != BlockType.LIST:
            question = None
        blocks.append(block)
    return replace(chapter, blocks=blocks)


def study_document_chapters(document: Document, think_ms: int) -> dict[int, Chapter]:
    """Capitulos transformados, por numero, para a conversao e a previa."""
    key = answer_key(document)
    return {chapter.number: study_chapter(chapter, key, think_ms, document.language) for chapter in document.chapters}
