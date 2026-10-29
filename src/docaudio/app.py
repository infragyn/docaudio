"""Ponto de entrada da interface grafica."""

import logging
import sys
import traceback
from pathlib import Path

from docaudio import APP_NAME, __version__
from docaudio.core import logs
from docaudio.storage.settings import Settings

log = logging.getLogger("docaudio")


def _install_excepthook() -> None:
    from PySide6.QtWidgets import QApplication, QMessageBox

    def hook(exc_type, exc, tb) -> None:
        log.critical("Erro não tratado:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        if QApplication.instance():
            QMessageBox.critical(None, APP_NAME, f"Ocorreu um erro inesperado:\n{exc}\n\nDetalhes no log.")

    sys.excepthook = hook


def _bring_to_front(window, files: list[str]) -> None:
    from PySide6.QtCore import Qt

    window.setWindowState((window.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
    window.show()
    window.raise_()
    window.activateWindow()
    if files:
        window.add_documents([Path(f) for f in files])


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from docaudio.ui import single_instance, theme

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    files = [str(Path(a).resolve()) for a in sys.argv[1:] if Path(a).is_file()]
    if single_instance.notify_running(files):
        # Ja aberto: a janela existente aparece e recebe os arquivos.
        return 0

    # Escuta desde ja: uma segunda abertura durante o carregamento tambem e reaproveitada.
    pending: list[str] = list(files)
    server = single_instance.InstanceServer(pending.extend)

    log_file = logs.setup_logging()
    theme.apply(app)
    _install_excepthook()
    # Aparece antes de carregar vozes e verificacoes, que levam alguns segundos.
    splash = theme.splash_screen()
    splash.show()
    app.processEvents()

    from docaudio.speech.registry import VoiceRegistry
    from docaudio.ui.main_window import MainWindow

    window = MainWindow(Settings.load(), VoiceRegistry(), log_file)
    server.on_open = lambda f: _bring_to_front(window, f)
    window.show()
    splash.finish(window)
    if pending:
        window.add_documents([Path(f) for f in pending])
    code = app.exec()
    server.server.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
