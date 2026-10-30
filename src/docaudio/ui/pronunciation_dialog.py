"""Corrigir a pronuncia de um termo selecionado na previa."""

from collections.abc import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QStyle,
    QWidget,
)

from docaudio.ui import theme


class PronunciationDialog(QDialog):
    """Pergunta como falar o termo; `on_listen(texto)` toca o resultado antes de salvar."""

    def __init__(self, term: str, current: str, on_listen: Callable[[str], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Corrigir pronúncia")
        self.setMinimumWidth(480)
        self.on_listen = on_listen
        body = theme.dialog_body(
            self,
            "Corrigir pronúncia",
            "Escreva como a palavra deve soar, do jeito que se fala. "
            "Ex.: “SQL” → “ésse quê éle”, “backup” → “békap”. Vale para todos os documentos.",
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume),
        )

        box, box_layout = theme.card()
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        self.term_edit = QLineEdit(term)
        form.addRow("Termo:", self.term_edit)
        self.spoken_edit = QLineEdit(current)
        self.spoken_edit.selectAll()
        form.addRow("Falar como:", self.spoken_edit)
        box_layout.addLayout(form)
        self.case_check = QCheckBox("Só quando escrito exatamente assim (maiúsculas e minúsculas)")
        self.case_check.setToolTip("Útil para siglas que também são palavras, como SEI e “sei”.")
        self.case_check.setChecked(term.isupper() and len(term) <= 5)
        box_layout.addWidget(self.case_check)
        body.addWidget(box)

        footer = QHBoxLayout()
        self.listen_button = QPushButton("Ouvir")
        self.listen_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))
        self.listen_button.setToolTip("Ouve como vai ficar, antes de salvar.")
        self.listen_button.clicked.connect(lambda: self.on_listen(self.spoken_edit.text()))
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Salvar")
        save.setDefault(True)
        theme.set_role(save, "primary")
        save.clicked.connect(self._accept)
        footer.addWidget(self.listen_button)
        footer.addStretch(1)
        footer.addWidget(cancel)
        footer.addWidget(save)
        body.addLayout(footer)
        self.spoken_edit.setFocus()

    def _accept(self) -> None:
        if self.term_edit.text().strip() and self.spoken_edit.text().strip():
            self.accept()

    def entry(self) -> tuple[str, str, bool]:
        return self.term_edit.text().strip(), self.spoken_edit.text().strip(), self.case_check.isChecked()
