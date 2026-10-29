"""Corrigir a pronuncia de um termo selecionado na previa."""

from collections.abc import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from docaudio.ui import theme


class PronunciationDialog(QDialog):
    """Pergunta como falar o termo; `on_listen(texto)` toca o resultado antes de salvar."""

    def __init__(self, term: str, current: str, on_listen: Callable[[str], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Corrigir pronúncia")
        self.setMinimumWidth(420)
        self.on_listen = on_listen

        layout = QVBoxLayout(self)
        hint = QLabel(
            "Escreva como a palavra deve soar, do jeito que se fala. "
            "Ex.: “SQL” → “esse quê éle”, “backup” → “békap”."
        )
        hint.setWordWrap(True)
        theme.set_role(hint, "muted")
        layout.addWidget(hint)

        form = QFormLayout()
        self.term_edit = QLineEdit(term)
        form.addRow("Termo:", self.term_edit)
        self.spoken_edit = QLineEdit(current)
        self.spoken_edit.selectAll()
        form.addRow("Falar como:", self.spoken_edit)
        self.case_check = QCheckBox("Só quando escrito exatamente assim (maiúsculas e minúsculas)")
        self.case_check.setChecked(term.isupper() and len(term) <= 5)
        form.addRow("", self.case_check)
        layout.addLayout(form)

        self.listen_button = QPushButton("Ouvir")
        self.listen_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))
        self.listen_button.clicked.connect(lambda: self.on_listen(self.spoken_edit.text()))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton(self.listen_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.spoken_edit.setFocus()

    def _accept(self) -> None:
        if self.term_edit.text().strip() and self.spoken_edit.text().strip():
            self.accept()

    def entry(self) -> tuple[str, str, bool]:
        return self.term_edit.text().strip(), self.spoken_edit.text().strip(), self.case_check.isChecked()
