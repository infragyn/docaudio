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
    path = save_user_entries([Entry(term, spoken, case_sensitive)])
    log.info("Pronúncia salva: %s → %s", term, spoken)
    return path


def save_user_entries(entries: list[Entry]) -> Path:
    """Grava varios termos de uma vez; um termo que ja existe e trocado pela versao nova."""
    path = ensure_user_dictionary()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.reader(handle) if row]

    def key(term: str, case_sensitive: bool) -> tuple[str, bool]:
        return (term if case_sensitive else term.lower(), case_sensitive)

    replaced = {key(e.term, e.case_sensitive) for e in entries}
    body = []
    for row in rows[1:]:
        flag = len(row) > 2 and row[2].strip().lower() in TRUE_VALUES
        if key(row[0].strip(), flag) not in replaced:
            body.append(row)
    body += [[e.term, e.spoken, "sim" if e.case_sensitive else "não"] for e in entries]
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(body)
    tmp.replace(path)
    return path


# Dicionarios tematicos em resources/pronunciation/temas/<nome>.csv.
THEMES = {
    "ti": "Tecnologia da informação",
    "direito": "Direito",
    "latim": "Latim jurídico",
    "administracao_publica": "Administração pública e órgãos",
    "contabilidade": "Contabilidade e tributos",
    "saude": "Saúde",
}
# Temas ligados (Ferramentas > Dicionários temáticos); None = todos.
_themes: set[str] | None = None


def set_themes(names: list[str] | None) -> None:
    global _themes
    _themes = None if names is None else set(names)


def theme_file(name: str) -> Path:
    return paths.resources_dir() / "pronunciation" / "temas" / f"{name}.csv"


def user_dictionary_for(language: str) -> Path:
    """Portugues usa pronuncia_usuario.csv; ingles e espanhol tem arquivos proprios."""
    if language == "pt":
        return user_dictionary_file()
    return paths.data_dir() / f"pronuncia_usuario_{language}.csv"


def load_default(language: str = "pt") -> PronunciationDictionary:
    """Dicionario padrao, temas ligados e, por ultimo (com prioridade), o do usuario.

    Os dicionarios do pacote sao de portugues; em outro idioma vale o do pacote
    para esse idioma (se houver) e o do usuario para esse idioma.
    """
    if language != "pt":
        return PronunciationDictionary.from_csv(
            paths.resources_dir() / "pronunciation" / f"{language}.csv", user_dictionary_for(language)
        )
    themes = [theme_file(name) for name in THEMES if _themes is None or name in _themes]
    return PronunciationDictionary.from_csv(
        paths.resources_dir() / "pronunciation" / DEFAULT_FILE, *themes, user_dictionary_file()
    )
