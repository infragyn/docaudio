"""Correcoes da reanalise: retomada de traducao, frases longas, plano B do OCR, acentos e CLI."""

import argparse
import os
import re
import threading
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import pytest

from docaudio import __main__ as cli
from docaudio.audio.ffmpeg import Cancelled
from docaudio.core import paths, translation
from docaudio.core.conversion import find_interrupted
from docaudio.core.language import LANGUAGES
from docaudio.core.loader import _restore_accents
from docaudio.core.model import Block, BlockType, Chapter, Document, DocumentKind
from docaudio.extractors import pdf_extractor
from docaudio.storage.settings import Settings
from docaudio.text_processing.accents import AccentRestorer
from docaudio.ui.conversion_worker import ConversionWorker, QueueItem
from test_conversion import FakeProvider, job, make_document, settings_for

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def fake_translator(monkeypatch):
    monkeypatch.setattr(
        translation._translator, "translate_text", lambda text, src, dst: f"[{dst}] {text}" if text else text
    )


def interrupted_translation(tmp_path, bilingual=False):
    """Conversao de um documento traduzido que parou no meio."""
    (tmp_path / "apostila.pdf").write_bytes(b"%PDF")
    document = translation.translate_document(make_document(tmp_path), "en", bilingual=bilingual)
    settings = settings_for(tmp_path, voice="voz-en")
    cancel = threading.Event()
    with pytest.raises(Cancelled):
        job(document, settings, FakeProvider(cancel_after=1, cancel=cancel), cancel=cancel).run()
    [item] = find_interrupted(settings.resolved_output_dir())
    return document, settings, item


# ---- retomada ---------------------------------------------------------------------


def test_conversao_interrompida_guarda_o_estado_da_traducao(tmp_path, fake_translator):
    _, _, item = interrupted_translation(tmp_path, bilingual=True)
    assert (item.language, item.translated_from, item.bilingual) == ("en", "pt", True)


def test_manifesto_antigo_sem_idioma_ainda_abre(tmp_path):
    from docaudio.storage.manifest import Manifest

    path = tmp_path / "manifest.json"
    path.write_text('{"document": "a.pdf", "title": "A", "voice": "v", "speed": 1.0, "format": "mp3"}', encoding="utf-8")
    assert Manifest.load(path).language == ""


def test_fila_traduz_de_novo_ao_retomar_e_reaproveita_os_trechos(tmp_path, fake_translator, monkeypatch):
    document, settings, item = interrupted_translation(tmp_path)
    original = make_document(tmp_path)
    monkeypatch.setattr("docaudio.ui.conversion_worker.load_document", lambda path, s: original)
    provider = FakeProvider()
    worker = ConversionWorker([QueueItem(str(item.document), None, settings, False, translate_to="en")], provider)
    failed = []
    worker.signals.document_failed.connect(lambda key, message: failed.append(message))
    worker.run()
    assert not failed
    assert len(provider.calls) == item.total_segments - item.done_segments
    assert all("[en]" in text for text in provider.calls)
    assert find_interrupted(settings.resolved_output_dir()) == []


def test_erro_de_traducao_ao_retomar_falha_so_aquele_documento(tmp_path, monkeypatch):
    document = make_document(tmp_path)
    monkeypatch.setattr("docaudio.ui.conversion_worker.load_document", lambda path, s: document)
    monkeypatch.setenv("DOCAUDIO_DATA_DIR", str(tmp_path))
    item = QueueItem("a.pdf", None, settings_for(tmp_path), False, translate_to="es")
    worker = ConversionWorker([item], FakeProvider())
    failed = []
    worker.signals.document_failed.connect(lambda key, message: failed.append(message))
    worker.run()
    assert len(failed) == 1 and "Idiomas e vozes" in failed[0]


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from docaudio.core import logs
    from docaudio.speech.registry import VoiceRegistry
    from docaudio.ui import main_window as mw

    monkeypatch.setattr(mw.MainWindow, "_save_settings", lambda self: None)
    win = mw.MainWindow(
        Settings(output_dir=str(tmp_path / "saida"), voice="voz-pt", voices_by_language={"en": "voz-en"}),
        VoiceRegistry(),
        logs.setup_logging(),
    )
    yield win
    win.originals.clear()
    win.loaded.clear()
    win.close()


def test_retomar_traducao_depois_de_reabrir_nao_le_o_original_com_a_voz_traduzida(window, tmp_path, fake_translator):
    _, _, item = interrupted_translation(tmp_path, bilingual=True)
    window.loaded[str(item.document)] = make_document(tmp_path)  # reaberto: em portugues
    queued = window._resume_item(item)
    assert queued.document is None
    assert (queued.translate_to, queued.bilingual) == ("en", True)
    assert queued.settings.voice == "voz-en"
    assert queued.settings.document_language == "pt"
    # No modo bilingue, o trecho original continua com a voz em portugues.
    assert queued.settings.voice_for("pt") == window.settings.voice_for("pt") != "voz-en"


def test_retomar_na_mesma_sessao_mantem_a_traducao_aberta(window, tmp_path, fake_translator):
    document, _, item = interrupted_translation(tmp_path)
    window.loaded[str(item.document)] = document
    queued = window._resume_item(item)
    assert queued.document is not None and queued.document.language == "en"
    assert queued.translate_to == ""


def test_retomar_original_com_traducao_aberta_volta_ao_portugues(window, tmp_path, fake_translator):
    (tmp_path / "apostila.pdf").write_bytes(b"%PDF")
    settings = settings_for(tmp_path, voice="voz-pt")
    cancel = threading.Event()
    with pytest.raises(Cancelled):
        job(make_document(tmp_path), settings, FakeProvider(cancel_after=1, cancel=cancel), cancel=cancel).run()
    [item] = find_interrupted(settings.resolved_output_dir())
    window.loaded[str(item.document)] = translation.translate_document(make_document(tmp_path), "en")
    queued = window._resume_item(item)
    assert queued.document is None and queued.translate_to == ""
    assert queued.settings.document_language == "pt"


# ---- frases longas ----------------------------------------------------------------


def test_frase_longa_e_partida_sem_perder_texto():
    sentence = ", ".join(f"item {i} da lista" for i in range(200)) + "."
    pieces = translation.split_long(sentence)
    assert len(pieces) > 1
    assert all(len(piece) <= translation.MAX_PIECE_CHARS for piece in pieces)
    assert " ".join(pieces) == sentence


def test_frase_longa_sem_pontuacao_e_palavra_gigante():
    sentence = " ".join(["palavra"] * 200) + " " + "x" * 700
    pieces = translation.split_long(sentence)
    assert all(len(piece) <= translation.MAX_PIECE_CHARS for piece in pieces)
    assert "".join(pieces).replace(" ", "") == sentence.replace(" ", "")


def test_frase_curta_fica_inteira():
    assert translation.split_long("A pilha segue LIFO.") == ["A pilha segue LIFO."]


PROJECT_MODELS = paths.project_root() / "data" / "traducao"


@pytest.mark.skipif(not (PROJECT_MODELS / "pt_en" / "model" / "model.bin").is_file(), reason="modelo pt→en não baixado")
def test_traducao_real_de_frase_longa_mantem_os_itens(monkeypatch):
    monkeypatch.setattr(translation, "pair_dir", lambda src, dst: PROJECT_MODELS / f"{src}_{dst}")
    sentence = ", ".join(f"o item {i} do edital deve ser verificado pelo candidato" for i in range(40)) + "."
    text = translation.Translator().translate_text(sentence, "pt", "en")
    assert len(re.findall(r"\b\d+\b", text)) >= 36  # quase todos os 40 numeros continuam la
    assert len(text) > len(sentence) * 0.6


# ---- empacotamento ------------------------------------------------------------------


def test_executavel_leva_o_dicionario_de_cada_idioma():
    spec = (paths.project_root() / "packaging" / "docaudio.spec").read_text(encoding="utf-8")
    kept = set(re.search(r"KEEP_DICTS = \{(.*?)\}", spec).group(1).replace('"', "").replace(" ", "").split(","))
    assert {f"{language}_dict" for language in LANGUAGES} <= kept


# ---- plano B do OCR -----------------------------------------------------------------


class _BrokenPool:
    def __init__(self, max_workers=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, *args):
        future = Future()
        future.set_exception(BrokenProcessPool("processo morreu"))
        return future


def test_ocr_le_pagina_a_pagina_quando_o_paralelo_quebra(tmp_path, monkeypatch):
    import pymupdf

    path = tmp_path / "scan.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.new_page()
        pdf.save(str(path))
    monkeypatch.setattr(pdf_extractor, "ProcessPoolExecutor", _BrokenPool)
    monkeypatch.setattr(pdf_extractor, "ocr_workers", lambda pages: 2)
    monkeypatch.setattr(pdf_extractor, "_ocr_page", lambda page, number, ocr: [f"pagina {number}"])
    options = pdf_extractor.OcrOptions(tmp_path, "por")
    assert pdf_extractor.ocr_pages_parallel(path, [1, 2], options) == {1: ["pagina 1"], 2: ["pagina 2"]}


# ---- acentos e OCR ------------------------------------------------------------------


def _document(tmp_path, title="Edital"):
    return Document(tmp_path / "a.pdf", DocumentKind.PDF, title, [Chapter(1, "C", [])])


def test_erro_unico_do_ocr_nao_contamina_a_palavra_certa(tmp_path):
    blocks = [
        Block(BlockType.PARAGRAPH, "Inteligência Artíficial", ocr=True),
        Block(BlockType.PARAGRAPH, "Inteligência Artificial", ocr=True),
    ]
    _restore_accents(_document(tmp_path), blocks)
    assert blocks[1].text == "Inteligência Artificial"


def test_ocr_repetido_ainda_ensina_o_acento(tmp_path):
    blocks = [
        Block(BlockType.PARAGRAPH, "CARGO TÉCNICO. NÍVEL TÉCNICO.", ocr=True),
        Block(BlockType.PARAGRAPH, "ANALISTA TECNICO", ocr=True),
    ]
    _restore_accents(_document(tmp_path), blocks)
    assert blocks[1].text == "ANALISTA TÉCNICO"


def test_texto_digital_continua_ensinando_com_uma_ocorrencia():
    assert AccentRestorer(["Gestão"]).restore("Gestao") == "Gestão"


# ---- linha de comando ---------------------------------------------------------------


@pytest.mark.parametrize(("voz", "expected"), [(None, "voz-en"), ("minha-voz", "minha-voz")])
def test_cli_escolhe_a_voz_do_idioma_do_documento(tmp_path, monkeypatch, voz, expected):
    document = make_document(tmp_path)
    document.language = "en"
    used = []

    class Job:
        def __init__(self, doc, settings, provider, progress=None):
            used.append(settings.voice)

        def run(self):
            return argparse.Namespace(files=[], reused_chapters=0, folder=tmp_path)

    monkeypatch.setattr("docaudio.core.conversion.ConversionJob", Job)
    monkeypatch.setattr("docaudio.core.loader.load_document", lambda path, settings: document)
    args = argparse.Namespace(
        arquivos=[Path("a.pdf")], saida=None, formato=None, voz=voz, velocidade=None, capitulos=None
    )
    settings = Settings(voice="voz-pt", voices_by_language={"en": "voz-en"})
    assert cli.cmd_converter(args, settings) == 0
    assert used == [expected]
