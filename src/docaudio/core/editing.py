"""Aplicacao das edicoes da previa ao modelo de blocos."""

import re
from difflib import SequenceMatcher

from docaudio.core.model import Block, BlockType, Chapter

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


def split_edited_text(text: str) -> list[str]:
    """Paragrafos separados por linha em branco; quebras simples sao mantidas (tabelas)."""
    paragraphs = []
    for chunk in _PARAGRAPH_BREAK.split(text):
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        if lines:
            paragraphs.append("\n".join(lines))
    return paragraphs


def apply_chapter_text(chapter: Chapter, text: str) -> bool:
    """Atualiza os blocos incluidos do capitulo a partir do texto editado.

    Paragrafos iguais mantem o bloco original; alterados atualizam Block.text;
    novos viram blocos de paragrafo; apagados saem do capitulo. Blocos ignorados
    pelas regras de leitura nao aparecem na previa e sao preservados.
    Devolve True se algo mudou.
    """
    included = chapter.included_blocks()
    old = [b.text for b in included]
    new = split_edited_text(text)
    if old == new:
        return False

    emitted: dict[int, list[Block]] = {}
    prefix: list[Block] = []

    def new_block(content: str, near: Block | None) -> Block:
        return Block(BlockType.PARAGRAPH, "", text=content, page=near.page if near else None)

    def attach(after_index: int, blocks: list[Block]) -> None:
        if after_index < 0:
            prefix.extend(blocks)
        else:
            emitted.setdefault(id(included[after_index]), [included[after_index]]).extend(blocks)

    matcher = SequenceMatcher(a=old, b=new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for i in range(i1, i2):
                emitted[id(included[i])] = [included[i]]
        elif tag == "delete":
            for i in range(i1, i2):
                emitted[id(included[i])] = []
        elif tag == "insert":
            near = included[i1 - 1] if i1 > 0 else (included[0] if included else None)
            attach(i1 - 1, [new_block(new[j], near) for j in range(j1, j2)])
        else:  # replace
            paired = min(i2 - i1, j2 - j1)
            for k in range(paired):
                block = included[i1 + k]
                block.text = new[j1 + k]
                emitted[id(block)] = [block]
            for i in range(i1 + paired, i2):
                emitted[id(included[i])] = []
            extra = [new_block(new[j], included[i1]) for j in range(j1 + paired, j2)]
            if extra:
                last = i1 + paired - 1
                if last >= i1:
                    emitted[id(included[last])].extend(extra)
                else:
                    attach(i1 - 1, extra)

    rebuilt = list(prefix)
    for block in chapter.blocks:
        rebuilt.extend(emitted.get(id(block), [block]) if block.include else [block])
    chapter.blocks = rebuilt
    return True
