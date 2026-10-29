"""Selecao dos documentos de entrada."""

from collections.abc import Iterable
from pathlib import Path

SUPPORTED_EXTENSIONS = (".docx", ".pdf", ".epub", ".pptx", ".txt", ".html", ".htm", ".xhtml")
FILE_DIALOG_FILTER = (
    "Documentos (*.docx *.pdf *.epub *.pptx *.txt *.html *.htm *.xhtml);;"
    "Word (*.docx);;PDF (*.pdf);;Livro digital EPUB (*.epub);;PowerPoint (*.pptx);;"
    "Texto (*.txt);;Página web (*.html *.htm *.xhtml)"
)
FORMATS_LABEL = "DOCX, PDF, EPUB, PPTX, TXT ou HTML"


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def split_supported(candidates: Iterable[Path]) -> tuple[list[Path], list[Path]]:
    """Separa arquivos aceitos e recusados, sem repetir caminhos.

    Arquivos temporarios do Word (~$nome.docx) sao recusados.
    """
    accepted: list[Path] = []
    rejected: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        path = Path(candidate).resolve()
        if path in seen:
            continue
        seen.add(path)
        if path.is_file() and is_supported(path) and not path.name.startswith("~$"):
            accepted.append(path)
        else:
            rejected.append(path)
    return accepted, rejected
