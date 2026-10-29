"""Janela unica e estimativa de tempo de conversao."""

import os
import subprocess
import sys

import pytest

from docaudio.core.loader import LONG_CONVERSION_SECONDS, estimated_generation_seconds

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_segunda_abertura_entrega_arquivos_a_primeira(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QDeadlineTimer, QEventLoop

    from docaudio.ui import single_instance

    monkeypatch.setenv("DOCAUDIO_DATA_DIR", str(tmp_path))
    assert not single_instance.notify_running([])  # ninguem aberto ainda
    received = []
    server = single_instance.InstanceServer(received.append)
    # A segunda abertura e outro processo, como no uso real.
    code = "import sys; from docaudio.ui import single_instance as s; sys.exit(0 if s.notify_running(['a.pdf']) else 3)"
    second = subprocess.Popen([sys.executable, "-c", code], env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    try:
        deadline = QDeadlineTimer(15000)
        while second.poll() is None and not deadline.hasExpired():
            QEventLoop().processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        assert second.poll() == 0
        assert received == [["a.pdf"]]
    finally:
        second.kill()
        server.server.close()


def test_nome_do_servidor_depende_da_pasta_de_dados(tmp_path, monkeypatch):
    from docaudio.ui import single_instance

    monkeypatch.setenv("DOCAUDIO_DATA_DIR", str(tmp_path / "a"))
    first = single_instance.server_name()
    monkeypatch.setenv("DOCAUDIO_DATA_DIR", str(tmp_path / "b"))
    assert single_instance.server_name() != first


def test_estimativa_avisa_so_para_voz_clonada():
    characters = 20_000  # um modulo de estudo tipico
    assert estimated_generation_seconds(characters, "Clonada") > LONG_CONVERSION_SECONDS
    for engine in ("Piper", "Kokoro", "Windows"):
        assert estimated_generation_seconds(characters, engine) < LONG_CONVERSION_SECONDS


def test_corrigir_pronuncia_grava_e_substitui_no_dicionario_do_usuario(tmp_path, monkeypatch):
    from docaudio.text_processing import pronunciation

    monkeypatch.setenv("DOCAUDIO_DATA_DIR", str(tmp_path))
    pronunciation.save_user_entry("backup", "bécap")
    pronunciation.save_user_entry("Backup", "békap")  # mesmo termo: substitui
    pronunciation.save_user_entry("SIGA", "siga", case_sensitive=True)
    entries = pronunciation.read_csv(pronunciation.user_dictionary_file())
    assert [(e.term, e.spoken, e.case_sensitive) for e in entries] == [
        ("Backup", "békap", False),
        ("SIGA", "siga", True),
    ]
    assert pronunciation.load_default().apply("Fazer o backup do SIGA e do siga") == (
        "Fazer o békap do siga e do siga"
    )


def test_lista_de_documentos_volta_na_proxima_abertura(qapp, tmp_path, monkeypatch):
    from pathlib import Path

    from docaudio.speech.registry import VoiceRegistry
    from docaudio.storage.settings import Settings
    from docaudio.ui.main_window import MainWindow

    monkeypatch.setenv("DOCAUDIO_DATA_DIR", str(tmp_path))
    files = []
    for name in ("a.docx", "b.pdf"):
        path = tmp_path / name
        path.write_bytes(b"x")
        files.append(path)
    settings = Settings(output_dir=str(tmp_path / "saida"))
    window = MainWindow(settings, VoiceRegistry(), tmp_path / "log.txt")
    window.add_documents(files)
    window.close()
    assert Settings.load().documents == [str(p) for p in files]

    files[1].unlink()  # apagado entre uma sessao e outra
    again = MainWindow(Settings.load(), VoiceRegistry(), tmp_path / "log.txt")
    assert again.document_paths() == [files[0]]
    assert Settings.load().documents == [str(files[0])]
    again.close()


def test_configuracao_com_lista_invalida_e_ignorada():
    from docaudio.storage.settings import Settings

    assert Settings(documents="nao e lista").documents == []
    assert Settings(documents=["a.pdf", 3, ""]).documents == ["a.pdf"]


def test_pasta_vigiada_pega_documento_novo_so_depois_de_parar_de_crescer(qapp, tmp_path, monkeypatch):
    from docaudio.ui.watch import FolderWatcher

    monkeypatch.setenv("DOCAUDIO_DATA_DIR", str(tmp_path / "dados"))
    folder = tmp_path / "entrada"
    folder.mkdir()
    (folder / "antigo.pdf").write_bytes(b"x")
    received = []
    watcher, again = FolderWatcher(), FolderWatcher()
    # Vigias esquecidos ligados atrasam o resto da suite: sempre desliga, mesmo se falhar.
    try:
        watcher.files_ready.connect(received.append)
        watcher.start(folder)  # o que ja estava na pasta nao entra

        novo = folder / "novo.docx"
        novo.write_bytes(b"parte")
        (folder / "~$novo.docx").write_bytes(b"x")  # temporario do Word
        (folder / "notas.odt").write_bytes(b"x")  # formato nao aceito
        watcher.scan()
        assert received == []  # primeira olhada: espera ver se o arquivo para de crescer
        watcher.scan()
        assert received == [[novo.resolve()]]

        # Ja convertido: nao entra de novo, nem depois de reabrir o programa.
        watcher.scan()
        again.files_ready.connect(received.append)
        again.start(folder, include_existing=True)
        again.scan()
        again.scan()
        assert len(received) == 1
    finally:
        watcher.stop()
        again.stop()
