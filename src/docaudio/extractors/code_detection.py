"""Deteccao de codigo-fonte pelo conteudo das linhas.

Apostilas costumam colar codigo em paragrafos comuns, sem fonte monoespacada
nem estilo proprio. Cada paragrafo e testado contra sinais fortes de codigo
(chaves, palavras-chave, tags, linhas HTTP/JSON); depois, linhas curtas entre
dois trechos de codigo sao incorporadas ao bloco.
"""

import re

KEYWORD_START = re.compile(
    r"^(public|private|protected|static|final|abstract|class|interface|enum|package|import|"
    r"def|return|throw|raise|try|catch|finally|except|elif|else|switch|case|break|continue|"
    r"const|let|var|function|async|await|yield|lambda|new|void|int|float|double|boolean|"
    r"String|System\.|console\.|this\.|self\.|print\(|printf\(|#include|@\w+)\b"
)
CONTROL_START = re.compile(r"^(if|for|while|elif|with)\b")
CODE_PUNCTUATION_ONLY = re.compile(r"^[\s{}()\[\];,]+$")
HTML_TAG = re.compile(r"^<[/!]?[a-zA-Z][^>]*>")
# Objeto/lista JSON com chave ou valor; "[...]" de citacao juridica nao conta.
JSON_LINE = re.compile(r"^(\{.*[\":].*\}|\[.*[\"\d].*\]),?$")
HTTP_REQUEST = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+/\S*(\s+HTTP/\d(\.\d)?)?$")
HTTP_STATUS = re.compile(r"^HTTP/\d(\.\d)?\s+\d{3}\b")
HTTP_HEADER = re.compile(r"^[A-Za-z][A-Za-z-]*:\s+\S+$")
SQL_START = re.compile(r"^(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\s+\S")
ASSIGNMENT = re.compile(r"^[\w.\[\]\"']+\s*([+\-*/%]?=|:=)\s*[^=\s]")
CODE_CALL = re.compile(r"[A-Za-z_]\w*\(")
CODE_ASSIGN = re.compile(r"[\w\]]\s*(?<![=!<>])=(?!=)\s*\S")
CALL_LINE = re.compile(r"^[\w.]+\(.*\);?$")
CSS_RULE = re.compile(r"^[.#@]?[\w\-\s,.#:>*]+\{")
CSS_PROPERTY = re.compile(r"^[a-z-]+\s*:\s*[^;]+;$")
# Pseudocodigo (Portugol), comum em apostilas de algoritmos.
PSEUDO_ASSIGNMENT = re.compile(r"^[\w\[\]]+\s*<-\s*\S")
PSEUDO_LINE = re.compile(
    r"^(fim_?\w+|fimalgoritmo|inicio|algoritmo\s+\S+|senao|repita)$"
    r"|^(escreva|escreval|leia)\b"
    r"|^(para|enquanto|se)\b.*\b(faca|entao)$"
    r"|^ate\s+\S"
)
SHELL_COMMAND = re.compile(
    r"^(sudo\s+)?(chmod|chown|chgrp|grep|ls|cd|cp|mv|rm|mkdir|rmdir|ps|kill|cat|apt|apt-get|yum|dnf|"
    r"systemctl|ssh|scp|tar|find|echo|ping|ipconfig|ifconfig|netstat|curl|wget|git|docker|pip|npm|"
    r"javac|java|python|python3|node|dir|copy|del|tracert|nslookup|traceroute)\s+\S"
)
COMMENT_START = ("#", "//", "/*", "*/", "--", "<!--", "'")
MONOSPACE_FONT = re.compile(
    r"consolas|courier|mono|lucida console|menlo|monaco|cascadia|fira code|source code|inconsolata",
    re.IGNORECASE,
)
CODE_STYLE = re.compile(r"code|c[óo]digo|preformat|source|listing|verbatim", re.IGNORECASE)
MAX_FILLER_CHARS = 60


def _prose_outside_strings(line: str) -> bool:
    """Palavras acentuadas fora de aspas indicam portugues, nao codigo."""
    outside = re.sub(r"\"[^\"]*\"|'[^']*'", "", line)
    return bool(re.search(r"[áéíóúâêôãõçÁÉÍÓÚÂÊÔÃÕÇ]", outside))


def looks_like_code(text: str) -> bool:
    """Sinais fortes de codigo em uma unica linha ou paragrafo curto."""
    line = text.strip()
    if not line or len(line.split()) > 25:
        return False
    prose = _prose_outside_strings(line)
    # Linhas so de pontuacao ("}", ";") so contam como codigo junto de outro codigo
    # (ver mark_code_runs): no OCR elas costumam ser ruido.
    if CODE_PUNCTUATION_ONLY.match(line):
        return False
    if line.endswith("{") or HTML_TAG.match(line) or JSON_LINE.match(line):
        return True
    if HTTP_REQUEST.match(line) or HTTP_STATUS.match(line) or SQL_START.match(line):
        return True
    if (HTTP_HEADER.match(line) and "/" in line) or re.match(r"^(Host|Accept|Content-Type|Authorization):", line):
        return True
    if prose:
        return False
    if PSEUDO_ASSIGNMENT.match(line) or PSEUDO_LINE.match(line):
        return True
    if SHELL_COMMAND.match(line) and not line.endswith("."):
        return True
    if KEYWORD_START.match(line) and (line.endswith((";", ":", ")", "}")) or "(" in line or "=" in line):
        return True
    if CONTROL_START.match(line) and (line.endswith(":") or "(" in line):
        return True
    # ";" final so indica codigo com chamada colada ("f(x);") ou atribuicao; enumeracoes
    # como "i) 04 (quatro) fotos 3x4 recentes;" sao texto.
    if line.endswith(";") and (CODE_CALL.search(line) or CODE_ASSIGN.search(line)) and len(line.split()) <= 12:
        return True
    if ASSIGNMENT.match(line) and len(line.split()) <= 8:
        return True
    if CALL_LINE.match(line) and " " not in line.split("(")[0]:
        return True
    return bool(CSS_RULE.match(line) or CSS_PROPERTY.match(line))


def mark_code_runs(flags: list[bool], texts: list[str]) -> list[bool]:
    """Completa trechos de codigo.

    Comentarios logo apos codigo ("# resultado: 15") e linhas curtas sem ponto
    final entre dois trechos de codigo tambem sao codigo.
    """
    result = list(flags)
    # Cada linha marcada pode completar a seguinte; repete ate estabilizar.
    changed = True
    while changed:
        changed = False
        for index in range(1, len(texts)):
            if result[index] or not result[index - 1]:
                continue
            text = texts[index].strip()
            closes = text.startswith(COMMENT_START) and len(text) <= 120
            if closes or (text and CODE_PUNCTUATION_ONLY.match(text)):
                result[index] = changed = True
                continue
            if len(text) > MAX_FILLER_CHARS or text.endswith((".", "?", "!")):
                continue
            # Linha curta entre codigo e mais codigo, ou "}"/";" que fecha o trecho.
            following = next(
                (result[j] or bool(CODE_PUNCTUATION_ONLY.match(texts[j])) for j in range(index + 1, len(texts)) if texts[j].strip()),
                False,
            )
            if following:
                result[index] = changed = True
    return result
