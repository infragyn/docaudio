"""Divisao do capitulo em trechos curtos para a sintese.

Cada trecho e sintetizado e guardado separadamente, o que permite retomar uma
conversao interrompida sem refazer o que ja foi gerado.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from docaudio.core.model import BlockType, Chapter

MAX_SEGMENT_CHARS = 400
SKIPPED_CODE_NOTICE = "Exemplo de código omitido na narração."
_SENTENCE_END = re.compile(r"(?<=[.!?…:;])\s+(?=[\"“'(\[]?[A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9])")


@dataclass(frozen=True)
class Segment:
    text: str
    pause_after_ms: int


def is_speakable(text: str) -> bool:
    """Tem ao menos uma letra ou algarismo para a voz pronunciar."""
    return any(c.isalnum() for c in text)


def split_sentences(text: str) -> list[str]:
    sentences = []
    for line in text.split("\n"):
        sentences.extend(s.strip() for s in _SENTENCE_END.split(line) if s.strip())
    return sentences


def _split_long(sentence: str, limit: int) -> list[str]:
    """Frases enormes (sem pontuacao) sao quebradas em virgulas ou espacos."""
    parts: list[str] = []
    while len(sentence) > limit:
        cut = sentence.rfind(", ", 0, limit)
        cut = cut + 1 if cut > limit // 3 else sentence.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(sentence[:cut].strip())
        sentence = sentence[cut:].strip()
    if sentence:
        parts.append(sentence)
    return parts


def group_sentences(sentences: list[str], limit: int = MAX_SEGMENT_CHARS) -> list[str]:
    groups: list[str] = []
    current = ""
    for sentence in sentences:
        for piece in _split_long(sentence, limit):
            if current and len(current) + 1 + len(piece) > limit:
                groups.append(current)
                current = piece
            else:
                current = f"{current} {piece}".strip()
    if current:
        groups.append(current)
    return groups


def segment_chapter(
    chapter: Chapter,
    normalizer: Callable[[str], str],
    announce_title: bool = True,
    sentence_pause_ms: int = 250,
    paragraph_pause_ms: int = 700,
    limit: int = MAX_SEGMENT_CHARS,
) -> list[Segment]:
    """Trechos do capitulo: titulo (opcional), depois cada bloco incluido.

    Cada sequencia de codigo-fonte nao narrado vira um aviso curto, para o
    ouvinte saber que o texto se refere a um exemplo que nao foi lido.
    """
    segments: list[Segment] = []
    heading_pause = int(paragraph_pause_ms * 1.3)
    if announce_title and chapter.title.strip():
        title = normalizer(chapter.title).rstrip(".")
        if is_speakable(title):
            segments.append(Segment(title + ".", heading_pause))
    previous_skipped_code = False
    for block in chapter.blocks:
        if not block.include:
            if block.type == BlockType.CODE and not previous_skipped_code:
                segments.append(Segment(SKIPPED_CODE_NOTICE, paragraph_pause_ms))
            previous_skipped_code = previous_skipped_code or block.type == BlockType.CODE
            continue
        previous_skipped_code = False
        text = normalizer(block.text)
        if not is_speakable(text):
            continue
        if block.type == BlockType.HEADING:
            segments.append(Segment(text.rstrip(".") + ".", heading_pause))
            continue
        # Linhas so com simbolos (ex.: "}" em codigo-fonte) nao tem o que narrar.
        sentences = [s for s in split_sentences(text) if is_speakable(s)]
        pieces = group_sentences(sentences, limit)
        block_pause = paragraph_pause_ms if block.pause_after_ms is None else block.pause_after_ms
        for index, piece in enumerate(pieces):
            last = index == len(pieces) - 1
            segments.append(Segment(piece, block_pause if last else sentence_pause_ms))
    return segments
