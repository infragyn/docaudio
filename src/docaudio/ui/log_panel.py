"""Painel que exibe os registros de log dentro da janela."""

import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QPlainTextEdit, QWidget

LEVEL_NAMES = {
    "DEBUG": "DEPURAÇÃO",
    "INFO": "INFO",
    "WARNING": "AVISO",
    "ERROR": "ERRO",
    "CRITICAL": "CRÍTICO",
}


class _Emitter(QObject):
    message = Signal(str)


class QtLogHandler(logging.Handler):
    """Encaminha logs de qualquer thread para a interface via sinal."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.emitter = _Emitter()
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)s  %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        record = logging.makeLogRecord(record.__dict__)
        record.levelname = LEVEL_NAMES.get(record.levelname, record.levelname)
        try:
            self.emitter.message.emit(self.format(record))
        except RuntimeError:
            # Janela ja destruida durante o encerramento.
            pass


class LogPanel(QPlainTextEdit):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(2000)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setFamilies(["Cascadia Mono", "Consolas", font.family()])
        font.setPointSize(9)
        self.setFont(font)
        self.handler = QtLogHandler()
        self.handler.emitter.message.connect(self.append_line)
        logging.getLogger().addHandler(self.handler)

    def append_line(self, text: str) -> None:
        self.appendPlainText(text)
        # Sem quebra de linha o Qt rola para o fim da linha; mantem o horario visivel.
        self.horizontalScrollBar().setValue(0)

    def detach(self) -> None:
        logging.getLogger().removeHandler(self.handler)
