"""Termos com pronuncia arriscada, para revisar antes de converter.

Siglas, palavras com cara de estrangeiras e nomes tecnicos com maiusculas no
meio costumam sair errados; os que ja estao no dicionario ficam de fora.
"""

import re
from collections import Counter
from dataclasses import dataclass

from docaudio.core import paths
from docaudio.core.model import BlockType, Document
from docaudio.text_processing.normalize import normalize
from docaudio.text_processing.pronunciation import PronunciationDictionary

UPPER = "A-ZÁÉÍÓÚÂÊÔÃÕÇÀ"
WORD = re.compile(r"(?<![\w@/.-])[A-Za-zÀ-ÿ][\w-]*[A-Za-zÀ-ÿ0-9](?![\w@/-])")
ACRONYM = re.compile(rf"^[{UPPER}]{{2,7}}s?$")
# Mistura de maiusculas e minusculas no meio da palavra: IPv6, JavaScript, PostgreSQL.
MIXED_CASE = re.compile(r"^[A-Za-z]+[a-z][A-Z]|^[A-Z]{2,}[a-z]|[a-z]\d|^\w+[A-Z]\d")
# Letras e combinacoes raras em portugues ("ee" e "oo" ficam de fora: compreensao, cooperar).
FOREIGN = re.compile(r"[kwy]|th|sh|ck|ph|ght|^[a-z]+[bdgkpt]$", re.IGNORECASE)
REASONS = {"sigla": "Sigla", "estrangeira": "Palavra estrangeira", "tecnico": "Nome técnico"}
# Blocos que nao sao narrados por padrao nao entram na revisao.
SKIPPED_TYPES = {BlockType.CODE, BlockType.URL}


@dataclass(frozen=True)
class Candidate:
    term: str
    count: int
    spoken: str
    reason: str

    @property
    def reason_label(self) -> str:
        return REASONS[self.reason]


def _reason(term: str) -> str | None:
    if ACRONYM.match(term):
        return "sigla"
    if MIXED_CASE.search(term):
        return "tecnico"
    if term.isalpha() and FOREIGN.search(term.lower()) and not term.isupper():
        return "estrangeira"
    return None


def reviewed_file():
    return paths.data_dir() / "pronuncia_revisada.txt"


def load_reviewed() -> set[str]:
    """Termos que o usuario ouviu e marcou como certos."""
    try:
        return {line.strip() for line in reviewed_file().read_text(encoding="utf-8").splitlines() if line.strip()}
    except OSError:
        return set()


def mark_reviewed(terms: list[str]) -> None:
    reviewed = load_reviewed() | {t.strip() for t in terms if t.strip()}
    reviewed_file().parent.mkdir(parents=True, exist_ok=True)
    reviewed_file().write_text("".join(f"{term}\n" for term in sorted(reviewed)), encoding="utf-8")


def candidates(document: Document, dictionary: PronunciationDictionary, limit: int = 300) -> list[Candidate]:
    """Termos a revisar, dos mais frequentes para os menos."""
    reviewed = load_reviewed()
    counts: Counter[str] = Counter()
    for chapter in document.included_chapters():
        texts = [chapter.title, *(b.text for b in chapter.included_blocks() if b.type not in SKIPPED_TYPES)]
        for text in texts:
            for term in WORD.findall(text):
                if _reason(term):
                    counts[term] += 1
    found = []
    for term, count in counts.most_common():
        # Ja corrigido no dicionario (padrao ou do usuario).
        if term in reviewed or dictionary.apply(term) != term:
            continue
        found.append(Candidate(term, count, normalize(term, dictionary), _reason(term)))
        if len(found) >= limit:
            break
    return found
