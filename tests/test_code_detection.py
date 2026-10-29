import pytest

from docaudio.core.model import Block, BlockType, Chapter
from docaudio.extractors.classify import classify_blocks
from docaudio.extractors.code_detection import looks_like_code, mark_code_runs
from docaudio.text_processing.segmenter import SKIPPED_CODE_NOTICE, segment_chapter

CODE = [
    "public class Usuario {",
    "private final String nome;",
    "if (nome == null || nome.isBlank()) {",
    'throw new IllegalArgumentException("nome inválido");',
    "this.nome = nome;",
    "def media(valores: list[float]) -> float:",
    "if not valores:",
    'raise ValueError("lista vazia")',
    "return sum(valores) / len(valores)",
    "try:",
    "except ValueError as erro:",
    "print(erro)",
    "numeros = [1, 2, 3, 4, 5]",
    "GET /usuarios/42 HTTP/1.1",
    "Host: api.exemplo.gov.br",
    "HTTP/1.1 200 OK",
    "Content-Type: application/json",
    '{"id": 42, "nome": "Ana", "ativo": true}',
    "<main>",
    '<label for="numero">Número</label>',
    "async function carregarUsuario(id) {",
    "const resposta = await fetch(`/api/usuarios/${id}`);",
    "SELECT nome FROM usuarios WHERE id = 1",
    ".menu { display: flex; }",
    "soma <- soma + i",
    "para i de 1 ate 5 faca",
    "fim_para",
    "escreva soma // resultado: 15",
    "chmod 750 executar.sh",
    "ps aux | grep java",
]

PROSE = [
    "Java é estaticamente tipada e normalmente compilada para bytecode executado pela JVM.",
    "Constante: valor que não deve mudar durante a execução.",
    "Atenção: 401 indica ausência ou falha de autenticação.",
    "I - a União (administração direta);",
    "Para cada item da lista, some o valor ao total.",
    "Se o valor for negativo, entao o sistema rejeita.",
    "Finalidade",
    "Regra de três direta: as grandezas variam no mesmo sentido.",
    "Em Python, == compara igualdade de valores; is verifica identidade do objeto.",
    "Programa: conjunto passivo de instruções armazenadas.",
    # Edital lido por OCR: enumeracao com parenteses e ruido de pontuacao.
    "i) 04 (quatro) fotos 3x4 recentes;",
    "[..]",
    "[...]",
    ";",
    ",",
    "}",
]


@pytest.mark.parametrize("line", CODE)
def test_code_lines(line):
    assert looks_like_code(line)


@pytest.mark.parametrize("line", PROSE)
def test_prose_is_not_code(line):
    assert not looks_like_code(line)


def test_runs_include_comments_and_short_gaps():
    texts = ["x = 1", "# resultado: 1", "Texto normal com ponto final.", "y = 2", "fim", "z = 3"]
    flags = [looks_like_code(t) for t in texts]
    assert mark_code_runs(flags, texts) == [True, True, False, True, True, True]


def test_closing_braces_after_code_are_code_but_alone_are_not():
    texts = ["if (x) {", "y();", "}", "}", "Texto depois.", ";"]
    flags = [looks_like_code(t) for t in texts]
    assert mark_code_runs(flags, texts) == [True, True, True, True, False, False]


def test_classify_marks_code_before_lists():
    blocks = classify_blocks(
        [
            Block(BlockType.PARAGRAPH, "Exemplo em Java:"),
            Block(BlockType.PARAGRAPH, "public void run() {"),
            Block(BlockType.PARAGRAPH, "- x;"),
            Block(BlockType.PARAGRAPH, "}"),
        ]
    )
    assert [b.type for b in blocks] == [BlockType.PARAGRAPH, BlockType.CODE, BlockType.CODE, BlockType.CODE]


def test_skipped_code_is_announced_once_per_run():
    chapter = Chapter(
        1,
        "",
        [
            Block(BlockType.PARAGRAPH, "Veja o exemplo."),
            Block(BlockType.CODE, "x = 1", include=False),
            Block(BlockType.CODE, "y = 2", include=False),
            Block(BlockType.PARAGRAPH, "Depois do exemplo."),
            Block(BlockType.CODE, "z = 3", include=False),
        ],
    )
    texts = [s.text for s in segment_chapter(chapter, lambda t: t, announce_title=False)]
    assert texts == ["Veja o exemplo.", SKIPPED_CODE_NOTICE, "Depois do exemplo.", SKIPPED_CODE_NOTICE]
