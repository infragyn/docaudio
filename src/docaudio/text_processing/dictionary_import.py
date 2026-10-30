"""Importacao de dicionarios de pronuncia de outros programas.

Formatos aceitos (todos "termo -> como falar", com letras comuns):

- NVDA (.dic): ``padrao<TAB>substituto<TAB>maiusculas(0/1)<TAB>tipo``, tipo 0 = em
  qualquer lugar, 1 = expressao regular, 2 = palavra inteira;
- PLS (.pls/.xml), padrao W3C usado pelo Amazon Polly e outros: ``<lexeme>`` com
  ``<grapheme>`` e ``<alias>`` (entradas so com ``<phoneme>`` sao fonemas e ficam de fora);
- CSV/TSV/TXT: termo e pronuncia separados por virgula, ponto e virgula, tabulacao
  ou "=", com cabecalho opcional e uma terceira coluna opcional de maiusculas.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from xml.etree import ElementTree

from docaudio.text_processing.pronunciation import TRUE_VALUES, Entry, read_csv, user_dictionary_file

MAX_TERM = 80
# Termo com cara de expressao regular: o DocAudio so substitui palavras inteiras.
REGEX_CHARS = re.compile(r"[\\^$*+?{}\[\]|()]")


@dataclass
class ImportResult:
    entries: list[Entry] = field(default_factory=list)
    # (linha ou posicao, motivo) de cada entrada deixada de fora
    skipped: list[tuple[str, str]] = field(default_factory=list)


class Status(StrEnum):
    NEW = "nova"
    SAME = "igual"
    CONFLICT = "conflito"


@dataclass(frozen=True)
class Planned:
    entry: Entry
    status: Status
    current: str = ""  # pronuncia que voce ja tinha, nos conflitos


def _read(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = raw.decode(encoding)
            if "\x00" not in text:
                return text
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _accept(result: ImportResult, where: str, term: str, spoken: str, case_sensitive: bool) -> None:
    term, spoken = term.strip(), spoken.strip()
    if not term or not spoken:
        result.skipped.append((where, "termo ou pronúncia vazios"))
    elif len(term) > MAX_TERM:
        result.skipped.append((where, "termo longo demais"))
    elif REGEX_CHARS.search(term):
        result.skipped.append((where, "expressão regular (o DocAudio substitui só palavras inteiras)"))
    elif term == spoken:
        result.skipped.append((where, "pronúncia igual ao termo"))
    else:
        result.entries.append(Entry(term, spoken, case_sensitive))


def parse_nvda(text: str) -> ImportResult:
    result = ImportResult()
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            result.skipped.append((f"linha {number}", "formato desconhecido"))
            continue
        pattern, replacement = parts[0], parts[1]
        case_sensitive = len(parts) > 2 and parts[2].strip() == "1"
        kind = parts[3].strip() if len(parts) > 3 else "0"
        if kind == "1":
            result.skipped.append((f"linha {number}", "expressão regular (o DocAudio substitui só palavras inteiras)"))
            continue
        _accept(result, f"linha {number}", pattern, replacement, case_sensitive)
    return result


def parse_pls(text: str) -> ImportResult:
    result = ImportResult()
    try:
        root = ElementTree.fromstring(text.encode("utf-8"))
    except ElementTree.ParseError as exc:
        raise ValueError(f"Arquivo PLS inválido: {exc}") from exc
    lexemes = [el for el in root.iter() if el.tag.rsplit("}", 1)[-1] == "lexeme"]
    for number, lexeme in enumerate(lexemes, start=1):
        children = {}
        for child in lexeme:
            children.setdefault(child.tag.rsplit("}", 1)[-1], []).append((child.text or "").strip())
        aliases = [a for a in children.get("alias", []) if a]
        if not aliases:
            reason = "só fonemas (formato fonético)" if children.get("phoneme") else "sem pronúncia"
            result.skipped.append((f"entrada {number}", reason))
            continue
        for grapheme in children.get("grapheme", []):
            # No PLS o grafema e sensivel a maiusculas por padrao; siglas costumam vir assim.
            _accept(result, f"entrada {number}", grapheme, aliases[0], grapheme.isupper())
    return result


def parse_table(text: str) -> ImportResult:
    result = ImportResult()
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return result
    delimiter = next((d for d in ("\t", ";", "=", ",") if all(d in line for line in lines[:5] if not line.startswith("#"))), ",")
    rows = csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter) if delimiter != "=" else (
        line.split("=", 1) for line in lines
    )
    for number, row in enumerate(rows, start=1):
        if not row or row[0].strip().startswith("#"):
            continue
        first = row[0].strip().lower()
        if number == 1 and first in ("termo", "term", "palavra", "word", "grapheme", "pattern"):
            continue  # cabecalho
        if len(row) < 2:
            result.skipped.append((f"linha {number}", "falta a pronúncia"))
            continue
        case_sensitive = len(row) > 2 and row[2].strip().lower() in TRUE_VALUES
        _accept(result, f"linha {number}", row[0], row[1], case_sensitive)
    return result


def parse_file(path: Path) -> ImportResult:
    """Le um dicionario pelo formato da extensao (ou do conteudo)."""
    text = _read(path)
    suffix = path.suffix.lower()
    if suffix in (".pls", ".xml") or text.lstrip().startswith("<"):
        return parse_pls(text)
    if suffix == ".dic":
        return parse_nvda(text)
    return parse_table(text)


def plan_import(entries: list[Entry], existing: list[Entry] | None = None) -> list[Planned]:
    """Compara com o dicionario do usuario: novas, iguais e conflitos (sem duplicar)."""
    existing = read_csv(user_dictionary_file()) if existing is None else existing
    current = {(e.term if e.case_sensitive else e.term.lower(), e.case_sensitive): e for e in existing}
    planned, seen = [], set()
    for entry in entries:
        key = (entry.term if entry.case_sensitive else entry.term.lower(), entry.case_sensitive)
        if key in seen:
            continue
        seen.add(key)
        mine = current.get(key)
        if mine is None:
            planned.append(Planned(entry, Status.NEW))
        elif mine.spoken.strip().lower() == entry.spoken.strip().lower():
            planned.append(Planned(entry, Status.SAME, mine.spoken))
        else:
            planned.append(Planned(entry, Status.CONFLICT, mine.spoken))
    return planned
