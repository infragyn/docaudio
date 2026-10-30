"""Traduzir texto: cole, traduza, corrija e salve, com ou sem gerar audio depois."""

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from docaudio.core import text_export, translation
from docaudio.core.language import LANGUAGES, detect_language
from docaudio.ui import theme
from docaudio.ui.workers import Worker

log = logging.getLogger(__name__)

AUTO = "auto"
SAVE_FILTER = "Documento Word (*.docx);;Texto (*.txt)"


class TranslateDialog(QDialog):
    """`open_languages()` abre o download de modelos; `send_to_list(arquivo)` manda para a conversao."""

    def __init__(
        self,
        output_dir: Callable[[], Path],
        open_languages: Callable[[], None],
        send_to_list: Callable[[Path], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.output_dir = output_dir
        self.open_languages = open_languages
        self.send_to_list = send_to_list
        self.pool = QThreadPool.globalInstance()
        self._worker: Worker | None = None
        self.setWindowTitle("Traduzir texto")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(920, 600)
        body = theme.dialog_body(
            self,
            "Traduzir texto",
            "Cole o texto e traduza entre português, inglês e espanhol, sem internet. "
            "Depois copie, salve em arquivo ou envie para virar áudio.",
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView),
        )

        languages = QHBoxLayout()
        languages.addWidget(QLabel("De:"))
        self.source_combo = QComboBox()
        self.source_combo.addItem("Detectar idioma", AUTO)
        for code, name in LANGUAGES.items():
            self.source_combo.addItem(name, code)
        languages.addWidget(self.source_combo)
        self.swap_button = QPushButton("⇄")
        self.swap_button.setToolTip("Inverter os idiomas e usar a tradução como novo texto")
        self.swap_button.clicked.connect(lambda: self.swap())
        languages.addWidget(self.swap_button)
        languages.addWidget(QLabel("Para:"))
        self.target_combo = QComboBox()
        for code, name in LANGUAGES.items():
            self.target_combo.addItem(name, code)
        self.target_combo.setCurrentIndex(self.target_combo.findData("en"))
        languages.addWidget(self.target_combo)
        languages.addStretch(1)
        self.translate_button = QPushButton("Traduzir")
        theme.set_role(self.translate_button, "primary")
        self.translate_button.clicked.connect(lambda: self.translate())
        languages.addWidget(self.translate_button)
        body.addLayout(languages)

        self.source_edit = QPlainTextEdit()
        self.source_edit.setPlaceholderText("Cole ou digite aqui o texto a traduzir.")
        self.source_edit.textChanged.connect(self._update_buttons)
        self.result_edit = QPlainTextEdit()
        self.result_edit.setPlaceholderText("A tradução aparece aqui, e você pode corrigi-la.")
        self.result_edit.textChanged.connect(self._update_buttons)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.source_edit)
        splitter.addWidget(self.result_edit)
        splitter.setChildrenCollapsible(False)
        body.addWidget(splitter, 1)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setVisible(False)
        body.addWidget(self.status)

        footer = QHBoxLayout()
        self.copy_button = QPushButton("Copiar tradução")
        self.copy_button.clicked.connect(lambda: self.copy_result())
        self.save_button = QPushButton("Salvar em arquivo…")
        self.save_button.clicked.connect(lambda: self.choose_and_save())
        self.send_button = QPushButton("Enviar para conversão em áudio")
        self.send_button.setToolTip("Salva a tradução como texto e a coloca na lista de documentos")
        self.send_button.clicked.connect(lambda: self.send())
        close = QPushButton("Fechar")
        close.clicked.connect(self.close)
        for button in (self.copy_button, self.save_button, self.send_button):
            footer.addWidget(button)
        footer.addStretch(1)
        footer.addWidget(close)
        body.addLayout(footer)
        self._update_buttons()

    # ---- estado --------------------------------------------------------------------

    def _set_status(self, text: str, role: str) -> None:
        self.status.setText(text)
        theme.set_role(self.status, role)
        self.status.setVisible(True)

    def _update_buttons(self) -> None:
        busy = self._worker is not None
        has_result = bool(self.result_edit.toPlainText().strip())
        self.translate_button.setEnabled(not busy and bool(self.source_edit.toPlainText().strip()))
        self.swap_button.setEnabled(not busy)
        for button in (self.copy_button, self.save_button, self.send_button):
            button.setEnabled(not busy and has_result)

    # ---- traduzir ------------------------------------------------------------------

    def source_language(self) -> str:
        chosen = self.source_combo.currentData()
        return detect_language(self.source_edit.toPlainText()) if chosen == AUTO else chosen

    def translate(self) -> None:
        text = self.source_edit.toPlainText()
        if not text.strip() or self._worker is not None:
            return
        source, target = self.source_language(), self.target_combo.currentData()
        if source == target:
            self._set_status(
                f"O texto já está em {LANGUAGES[target].lower()}. Escolha outro idioma em “Para” "
                "ou indique o idioma certo em “De”.",
                "warning",
            )
            return
        if not translation.is_installed(source, target):
            answer = QMessageBox.question(
                self,
                "Traduzir",
                f"O modelo de tradução {LANGUAGES[source].lower()} → {LANGUAGES[target].lower()} "
                f"ainda não foi baixado (cerca de {translation.PACKAGE_MB} MB, uma vez só).\n\n"
                "Abrir “Idiomas e vozes” para baixar?",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.open_languages()
            if not translation.is_installed(source, target):
                return
        self._set_status(f"Traduzindo de {LANGUAGES[source].lower()} para {LANGUAGES[target].lower()}…", "info")
        worker = Worker(translation.translate_plain, text, source, target, with_progress=True)
        worker.signals.progress.connect(lambda message: self._set_status(message, "info"))
        worker.signals.finished.connect(lambda result, s=source, t=target: self._on_translated(result, s, t))
        worker.signals.failed.connect(self._on_failed)
        self._worker = worker
        self._update_buttons()
        self.pool.start(worker)

    def _on_translated(self, result: str, source: str, target: str) -> None:
        self._worker = None
        self.result_edit.setPlainText(result)
        self._update_buttons()
        self._set_status(
            f"Traduzido de {LANGUAGES[source].lower()} para {LANGUAGES[target].lower()}. "
            "Revise termos técnicos: a tradução automática pode errá-los.",
            "success",
        )

    def _on_failed(self, message: str) -> None:
        self._worker = None
        self._update_buttons()
        self._set_status(f"Não foi possível traduzir: {message}", "error")

    def swap(self) -> None:
        source, target = self.source_language(), self.target_combo.currentData()
        self.source_combo.setCurrentIndex(self.source_combo.findData(target))
        self.target_combo.setCurrentIndex(self.target_combo.findData(source))
        result = self.result_edit.toPlainText()
        if result.strip():
            self.result_edit.setPlainText(self.source_edit.toPlainText())
            self.source_edit.setPlainText(result)

    # ---- destino -------------------------------------------------------------------

    def copy_result(self) -> None:
        QGuiApplication.clipboard().setText(self.result_edit.toPlainText())
        self._set_status("Tradução copiada.", "success")

    def _save(self, path: Path) -> bool:
        try:
            text_export.save_text(path, "", text_export.plain_sections(self.result_edit.toPlainText()))
        except OSError as exc:
            self._set_status(f"Não foi possível salvar {path.name}: {exc}", "error")
            return False
        log.info("Tradução salva em %s", path)
        return True

    def choose_and_save(self) -> None:
        name, chosen = QFileDialog.getSaveFileName(
            self, "Salvar tradução", str(self.output_dir() / self._default_name()), SAVE_FILTER
        )
        if not name:
            return
        path = Path(name)
        if path.suffix.lower() not in (".docx", ".txt"):
            path = path.with_suffix(".txt" if "txt" in chosen else ".docx")
        if self._save(path):
            self._set_status(f"Salvo em {path}.", "success")

    def _default_name(self) -> str:
        return f"traducao_{self.target_combo.currentData()}_{datetime.now():%Y%m%d_%H%M%S}.docx"

    def send(self) -> None:
        path = self.output_dir() / "Textos traduzidos" / Path(self._default_name()).with_suffix(".txt")
        if self._save(path):
            self.send_to_list(path)
            self._set_status("Enviado para a lista de documentos. Agora é só converter.", "success")
