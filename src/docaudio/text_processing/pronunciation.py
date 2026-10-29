"""Dicionarios de pronuncia em CSV (termo,pronuncia,diferenciar_maiusculas)."""

import csv
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from docaudio.core import paths

log = logging.getLogger(__name__)

DEFAULT_FILE = "padrao.csv"
USER_FILE = "pronuncia_usuario.csv"
HEADER = ["termo", "pronuncia", "diferenciar_maiusculas"]
TRUE_VALUES = {"1", "s", "sim", "true", "x", "verdadeiro"}


@dataclass(frozen=True)
class Entry:
    term: str
    spoken: str
    case_sensitive: bool


class PronunciationDictionary:
    """Substitui termos inteiros; entradas carregadas depois tem prioridade."""

    def __init__(self, entries: list[Entry] | None = None) -> None:
        self._entries: dict[tuple[str, bool], Entry] = {}
        self._pattern_cache: list[tuple[re.Pattern, str]] | None = None
        for entry in entries or []:
            self.add(entry)

    def add(self, entry: Entry) -> None:
        key = (entry.term if entry.case_sensitive else entry.term.lower(), entry.case_sensitive)
        self._entries[key] = entry
        self._pattern_cache = None

    def __len__(self) -> int:
        return len(self._entries)

    def _patterns(self) -> list[tuple[re.Pattern, str]]:
        if self._pattern_cache is None:
            # Termos maiores primeiro para "TCP/IP" vencer "TCP".
            ordered = sorted(self._entries.values(), key=lambda e: len(e.term), reverse=True)
            self._pattern_cache = [
                (
                    re.compile(
                        rf"(?<![\w-]){re.escape(e.term)}(?![\w-])",
                        0 if e.case_sensitive else re.IGNORECASE,
                    ),
                    e.spoken,
                )
                for e in ordered
            ]
        return self._pattern_cache

    def apply(self, text: str) -> str:
        for pattern, spoken in self._patterns():
            text = pattern.sub(spoken, text)
        return text

    @classmethod
    def from_csv(cls, *files: Path) -> "PronunciationDictionary":
        dictionary = cls()
        for file in files:
            for entry in read_csv(file):
                dictionary.add(entry)
        return dictionary


def read_csv(path: Path) -> list[Entry]:
    if not path.is_file():
        return []
    entries = []
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for line_number, row in enumerate(csv.DictReader(handle), start=2):
                term = (row.get("termo") or "").strip()
                spoken = (row.get("pronuncia") or "").strip()
                if not term or not spoken or term.startswith("#"):
                    continue
                flag = (row.get("diferenciar_maiusculas") or "").strip().lower() in TRUE_VALUES
                entries.append(Entry(term, spoken, flag))
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        log.warning("Dicionário de pronúncia inválido em %s: %s", path, exc)
    return entries


def user_dictionary_file() -> Path:
    return paths.data_dir() / USER_FILE


def ensure_user_dictionary() -> Path:
    """Cria o dicionario do usuario com cabecalho e um exemplo, se nao existir."""
    path = user_dictionary_file()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(HEADER)
            writer.writerow(["# exemplo: SIGA", "siga", "sim"])
    return path


def save_user_entry(term: str, spoken: str, case_sensitive: bool = False) -> Path:
    """Grava (ou troca) um termo no dicionario do usuario, preservando as outras linhas."""
    term, spoken = term.strip(), spoken.strip()
    if not term or not spoken:
        raise ValueError("Informe o termo e como ele deve ser falado.")
    path = ensure_user_dictionary()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.reader(handle) if row]
    same = (lambda t: t == term) if case_sensitive else (lambda t: t.lower() == term.lower())
    body = [row for row in rows[1:] if not same(row[0].strip())]
    body.append([term, spoken, "sim" if case_sensitive else "não"])
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(body)
    tmp.replace(path)
    log.info("Pronúncia salva: %s → %s", term, spoken)
    return path


def load_default() -> PronunciationDictionary:
    """Dicionario padrao do pacote mais o do usuario (que tem prioridade)."""
    return PronunciationDictionary.from_csv(
        paths.resources_dir() / "pronunciation" / DEFAULT_FILE, user_dictionary_file()
    )
