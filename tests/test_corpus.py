"""Criterio de aceite da Etapa 2: titulos e conteudo de cada documento do corpus."""

import json
import sys
from pathlib import Path

import pytest

from docaudio.core.dependencies import find_tessdata
from docaudio.core.loader import load_document
from docaudio.core.model import DocumentKind
from docaudio.storage.settings import Settings

sys.path.insert(0, str(Path(__file__).parent / "corpus"))
import sinteticos  # noqa: E402

REAL_CORPUS = Path(__file__).parent / "corpus" / "reais"


@pytest.fixture(scope="session")
def synthetic_corpus(tmp_path_factory):
    folder = tmp_path_factory.mktemp("corpus")
    return {ref["arquivo"]: (folder / ref["arquivo"], ref) for ref in sinteticos.generate(folder)}


def _real_cases():
    cases = []
    for expected in sorted(REAL_CORPUS.glob("*.esperado.json")):
        reference = json.loads(expected.read_text(encoding="utf-8"))
        cases.append(pytest.param(REAL_CORPUS / reference["arquivo"], reference, id=reference["arquivo"]))
    return cases


def check_reference(path: Path, reference: dict) -> None:
    if reference.get("requer_ocr") and find_tessdata(Settings()) is None:
        pytest.skip("dados de OCR (por.traineddata) não instalados")
    document = load_document(path, Settings())
    assert [c.title for c in document.chapters] == reference["capitulos"]
    if "titulo" in reference:
        assert document.title == reference["titulo"]
    narrated = "\n\n".join(c.narration_text() for c in document.chapters)
    for text in reference.get("contem", []):
        assert text in narrated, f"esperado na narração: {text!r}"
    for text in reference.get("nao_contem", []):
        assert text not in narrated, f"não deveria ser narrado: {text!r}"
    positions = [narrated.find(text) for text in reference.get("ordem", [])]
    assert all(p >= 0 for p in positions) and positions == sorted(positions), "ordem de leitura incorreta"
    if reference.get("ocr"):
        assert document.kind == DocumentKind.PDF_OCR
        assert document.pages_without_text or document.ocr_pages
    if reference.get("requer_ocr"):
        assert document.ocr_pages
        assert all(b.ocr for c in document.chapters for b in c.blocks)


@pytest.mark.parametrize("name", [g.__name__ for g in sinteticos.GENERATORS])
def test_synthetic_corpus(synthetic_corpus, name):
    path, reference = next(v for k, v in synthetic_corpus.items() if Path(k).stem == name)
    check_reference(path, reference)


@pytest.mark.parametrize("path, reference", _real_cases())
def test_real_corpus(path, reference):
    if not path.exists():
        pytest.skip(f"{path.name} não está no corpus local")
    check_reference(path, reference)
