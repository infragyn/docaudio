"""manifest.json: estado de uma conversao, usado para retomar e para o historico."""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

VERSION = 1


@dataclass
class ChapterState:
    number: int
    title: str
    hash: str
    status: str = "pendente"  # pendente | concluido | erro
    output: str = ""
    segments: int = 0
    error: str = ""
    # [inicio em segundos, texto falado] de cada trecho, para o player acompanhar o texto.
    timings: list = field(default_factory=list)
    # Inicio do capitulo dentro do arquivo (M4B tem todos os capitulos num arquivo so).
    start_s: float = 0.0


@dataclass
class Manifest:
    document: str
    title: str
    voice: str
    speed: float
    format: str
    chapters: list[ChapterState] = field(default_factory=list)
    final_output: str = ""
    final_hash: str = ""
    updated_at: str = ""
    # Estado do documento, para retomar o mesmo texto: o arquivo em `document` e o original.
    language: str = ""
    translated_from: str = ""
    bilingual: bool = False
    version: int = VERSION

    def chapter(self, chapter_hash: str) -> ChapterState | None:
        return next((c for c in self.chapters if c.hash == chapter_hash), None)

    def save(self, path: Path) -> None:
        self.updated_at = datetime.now().isoformat(timespec="seconds")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "Manifest | None":
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            chapters = [ChapterState(**c) for c in raw.pop("chapters", [])]
            return cls(**raw, chapters=chapters)
        except (OSError, ValueError, TypeError) as exc:
            log.warning("Manifesto ignorado (%s): %s", path, exc)
            return None
