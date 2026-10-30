"""Traducao direta de texto: janela, salvar em arquivo e enviar para a conversao."""

import os
import time

import pytest

from docaudio.core import paths, text_export, translation
from docaudio.core.model import Block, BlockType, Chapter, Document, DocumentKind

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture
def fake_translator(monkeypatch):
    monkeypatch.setattr(
        translation._translator, "translate_text", lambda text, src, dst: f"[{dst}] {text}" if text else text
    )
    monkeypatch.setattr(translation, "is_installed", lambda src, dst: True)


@pytest.fixture
def dialog(tmp_path):
    from docaudio.ui.translate_dialog import TranslateDialog

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    sent, opened = [], []
    widget = TranslateDialog(lambda: tmp_path / "saida", lambda: opened.append(True), sent.append)
    widget.app, widget.sent, widget.opened = app, sent, opened
    yield widget
    widget.close()


def wait(widget, limit=20.0):
    end = time.time() + limit
    while widget._worker is not None and time.time() < end:
        widget.app.processEvents()
        time.sleep(0.005)
    assert widget._worker is None


def test_texto_avulso_mantem_as_linhas_e_avisa_o_andamento(fake_translator):
    messages = []
    text = "\n".join(["linha"] * 20 + ["", "fim"])
    result = translation.translate_plain(text, "pt", "en", messages.append)
    assert result.split("\n")[0] == "[en] linha"
    assert result.split("\n")[20] == ""
    assert result.endswith("[en] fim")
    assert messages


def test_janela_detecta_o_idioma_traduz_e_libera_os_botoes(dialog, fake_translator):
    assert not dialog.translate_button.isEnabled() and not dialog.save_button.isEnabled()
    dialog.source_edit.setPlainText("Esta é uma frase em português para a tradução.")
    assert dialog.translate_button.isEnabled()
    dialog.translate()
    wait(dialog)
    assert dialog.result_edit.toPlainText().startswith("[en] Esta")
    assert dialog.save_button.isEnabled() and dialog.send_button.isEnabled()
    assert dialog.status.text().startswith("Traduzido de português para inglês")


def test_mesmo_idioma_nao_traduz_e_explica(dialog, fake_translator):
    dialog.source_edit.setPlainText("Esta é uma frase em português para a tradução.")
    dialog.target_combo.setCurrentIndex(dialog.target_combo.findData("pt"))
    dialog.translate()
    assert dialog._worker is None
    assert "já está em português" in dialog.status.text()
    assert not dialog.result_edit.toPlainText()


def test_sem_modelo_oferece_o_download(dialog, monkeypatch):
    monkeypatch.setattr(translation, "is_installed", lambda src, dst: False)
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes
    )
    dialog.source_edit.setPlainText("Esta é uma frase em português para a tradução.")
    dialog.translate()
    assert dialog.opened == [True]
    assert dialog._worker is None


def test_inverter_troca_idiomas_e_textos(dialog, fake_translator):
    dialog.source_edit.setPlainText("Esta é uma frase em português para a tradução.")
    dialog.translate()
    wait(dialog)
    translated = dialog.result_edit.toPlainText()
    dialog.swap()
    assert dialog.source_combo.currentData() == "en" and dialog.target_combo.currentData() == "pt"
    assert dialog.source_edit.toPlainText() == translated


def test_salva_em_txt_e_docx(dialog, tmp_path):
    from docx import Document as WordDocument

    dialog.result_edit.setPlainText("Primeira linha.\n\nSegunda linha.")
    assert dialog._save(tmp_path / "a.txt")
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "Primeira linha.\n\nSegunda linha.\n"
    assert dialog._save(tmp_path / "a.docx")
    assert [p.text for p in WordDocument(tmp_path / "a.docx").paragraphs] == ["Primeira linha.", "Segunda linha."]


def test_enviar_cria_um_txt_que_o_programa_le_no_idioma_certo(dialog):
    from docaudio.core.loader import load_document
    from docaudio.storage.settings import Settings

    dialog.target_combo.setCurrentIndex(dialog.target_combo.findData("en"))
    dialog.result_edit.setPlainText("The queue follows the first in first out policy and the server answers requests.")
    dialog.send()
    [path] = dialog.sent
    assert path.suffix == ".txt" and path.is_file()
    document = load_document(path, Settings())
    assert document.language == "en"
    assert document.char_count() > 0


def _translated_document(tmp_path):
    chapter = Chapter(1, "Title", [
        Block(BlockType.PARAGRAPH, "Kept paragraph."),
        Block(BlockType.CODE, "x = 1"),
        Block(BlockType.NOTE, "Removed note.", include=False),
    ], level=1)
    skipped = Chapter(2, "Skipped", [Block(BlockType.PARAGRAPH, "Not exported.")], include=False)
    return Document(tmp_path / "a.pdf", DocumentKind.PDF, "Book", [chapter, skipped], language="en", translated_from="pt")


def test_documento_exporta_so_o_que_esta_incluido(tmp_path):
    from docx import Document as WordDocument

    document = _translated_document(tmp_path)
    assert text_export.document_sections(document) == [("Title", ["Kept paragraph.", "x = 1"])]
    text_export.save_text(tmp_path / "a.docx", document.title, text_export.document_sections(document))
    assert [p.text for p in WordDocument(tmp_path / "a.docx").paragraphs] == ["Book", "Title", "Kept paragraph.", "x = 1"]


def test_janela_principal_salva_o_documento_traduzido(qapp_window, tmp_path, monkeypatch):
    window = qapp_window
    document = _translated_document(tmp_path)
    key = str(document.source)
    window.loaded[key] = document
    monkeypatch.setattr(window, "_current_key", lambda: key)
    window._write_translation(document, tmp_path / "saida" / "livro")
    assert (tmp_path / "saida" / "livro.docx").is_file()
    window._write_translation(document, tmp_path / "saida" / "livro2.tudo", "Texto (*.txt)")
    assert (tmp_path / "saida" / "livro2.tudo.txt").is_file()


@pytest.fixture
def qapp_window(tmp_path, monkeypatch):
    from docaudio.core import logs
    from docaudio.speech.registry import VoiceRegistry
    from docaudio.storage.settings import Settings
    from docaudio.ui import main_window as mw

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(mw.MainWindow, "_save_settings", lambda self: None)
    window = mw.MainWindow(Settings(output_dir=str(tmp_path / "saida")), VoiceRegistry(), logs.setup_logging())
    yield window
    window.loaded.clear()
    window.close()


def test_atalho_abre_uma_janela_so(qapp_window):
    qapp_window.open_text_translator()
    first = qapp_window.text_translator
    qapp_window.open_text_translator()
    assert qapp_window.text_translator is first and first.isVisible()
    first.close()


PROJECT_MODELS = paths.project_root() / "data" / "traducao"


@pytest.mark.skipif(not (PROJECT_MODELS / "pt_en" / "model" / "model.bin").is_file(), reason="modelo pt→en não baixado")
def test_traducao_real_de_texto_avulso(monkeypatch):
    monkeypatch.setattr(translation, "pair_dir", lambda src, dst: PROJECT_MODELS / f"{src}_{dst}")
    monkeypatch.setattr(translation, "_translator", translation.Translator())
    result = translation.translate_plain("O servidor responde.\n\nA fila anda.", "pt", "en")
    lines = result.split("\n")
    assert len(lines) == 3 and "server" in lines[0].lower() and lines[1] == ""
