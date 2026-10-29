"""Divisao do documento em capitulos por titulos ou por tamanho."""

from docaudio.core.model import Block, BlockType, Chapter

DEFAULT_PART_CHARS = 20_000
PREAMBLE_TITLE = "Início"


def choose_split_level(blocks: list[Block]) -> int | None:
    """Nivel de titulo usado para separar capitulos.

    Usa o nivel mais alto presente; se ele aparece uma unica vez (titulo do
    documento) e ha niveis abaixo, usa o seguinte.
    """
    counts: dict[int, int] = {}
    for block in blocks:
        if block.type == BlockType.HEADING:
            counts[block.level] = counts.get(block.level, 0) + 1
    if not counts:
        return None
    levels = sorted(counts)
    if counts[levels[0]] == 1 and len(levels) > 1:
        return levels[1]
    return levels[0]


def _split_by_size(title: str, blocks: list[Block], max_chars: int, level: int | None) -> list[Chapter]:
    parts: list[list[Block]] = [[]]
    size = 0
    for block in blocks:
        if parts[-1] and size + len(block.text) > max_chars:
            parts.append([])
            size = 0
        parts[-1].append(block)
        size += len(block.text)
    if len(parts) == 1:
        return [Chapter(0, title, parts[0], level=level)]
    return [
        Chapter(0, f"{title} (parte {i})", part, level=level) for i, part in enumerate(parts, start=1)
    ]


def build_chapters(
    blocks: list[Block],
    max_chars: int = 0,
    part_chars: int = DEFAULT_PART_CHARS,
) -> list[Chapter]:
    """Agrupa blocos em capitulos.

    O titulo que abre um capitulo vira Chapter.title e nao fica entre os blocos;
    titulos de nivel inferior permanecem como blocos. Sem titulos, o documento e
    dividido em partes de ate `part_chars` caracteres.
    """
    if not blocks:
        return []
    split_level = choose_split_level(blocks)
    groups: list[tuple[str, list[Block], int | None]] = []

    if split_level is None:
        groups.append(("Parte", list(blocks), None))
        limit = max_chars or part_chars
    else:
        limit = max_chars
        current: list[Block] = []
        title = PREAMBLE_TITLE
        level: int | None = None
        for block in blocks:
            if block.type == BlockType.HEADING and block.level <= split_level:
                # Titulos sem conteudo proprio (ex.: titulo do documento logo antes
                # do primeiro capitulo) nao geram capitulos vazios.
                if current:
                    groups.append((title, current, level))
                title, level, current = block.text, block.level, []
            else:
                current.append(block)
        if current or not groups:
            groups.append((title, current, level))

    chapters: list[Chapter] = []
    for title, group, level in groups:
        if limit:
            chapters.extend(_split_by_size(title, group, limit, level))
        else:
            chapters.append(Chapter(0, title, group, level=level))
    if split_level is None:
        for i, chapter in enumerate(chapters, start=1):
            chapter.title = f"Parte {i}"
    for number, chapter in enumerate(chapters, start=1):
        chapter.number = number
    return chapters
