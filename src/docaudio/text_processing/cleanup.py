"""Limpeza de texto extraido de documentos."""

import re
import unicodedata

LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
_SPACES = re.compile(r"[ \t  -   　]+")
_CONTROL = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f​-‍﻿­]")
_HYPHEN_END = re.compile(r"(\w)[-‐‑]$")


def clean_text(text: str) -> str:
    """Normaliza unicode, remove caracteres invisiveis e espacos repetidos."""
    for ligature, plain in LIGATURES.items():
        text = text.replace(ligature, plain)
    text = unicodedata.normalize("NFC", text)
    text = _CONTROL.sub("", text)
    text = _SPACES.sub(" ", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def join_lines(lines: list[str]) -> str:
    """Une as linhas de um paragrafo, desfazendo hifenizacao de fim de linha.

    'progra-' + 'mação' vira 'programação'; 'guarda-' + 'Chuva' mantem o hifen
    porque a proxima linha comeca com maiuscula.
    """
    result = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if not result:
            result = line
        elif _HYPHEN_END.search(result) and line[0].islower():
            result = result[:-1] + line
        else:
            result = f"{result} {line}"
    return result


def normalize_key(text: str) -> str:
    """Chave para comparar textos ignorando acentos, caixa, numeros e pontuacao."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\d+", "#", text)
    return re.sub(r"[\W_]+", " ", text).strip()
