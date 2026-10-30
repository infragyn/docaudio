"""Importar um dicionario de pronuncia: previa do que entra, conflitos e o que fica de fora."""

from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from docaudio.text_processing.dictionary_import import ImportResult, Planned, Status
from docaudio.text_processing.pronunciation import Entry
from docaudio.ui import theme

STATUS_LABELS = {Status.NEW: "Nova", Status.SAME: "Já tinha", Status.CONFLICT: "Conflito"}


class ImportDialog(QDialog):
    def __init__(self, source: Path, result: ImportResult, planned: list[Planned], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.planned = planned
        self.setWindowTitle(f"Importar dicionário — {source.name}")
        self.resize(780, 560)
        layout = theme.dialog_body(
            self,
            "Importar dicionário de pronúncia",
            f"{source.name} — confira o que entra no seu dicionário antes de importar.",
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton),
        )

        counts = {status: sum(1 for p in planned if p.status == status) for status in Status}
        summary = QLabel(
            f"<b>{counts[Status.NEW]}</b> nova(s) · {counts[Status.SAME]} que você já tinha · "
            f"<b>{counts[Status.CONFLICT]}</b> conflito(s) com as suas"
            + (f" · {len(result.skipped)} ignorada(s)" if result.skipped else "")
        )
        theme.set_role(summary, "warning" if counts[Status.CONFLICT] else "info")
        layout.addWidget(summary)
        if result.skipped:
            skipped = QLabel(
                "Ignoradas: " + "; ".join(f"{where} ({why})" for where, why in result.skipped[:6])
                + (" …" if len(result.skipped) > 6 else "")
            )
            skipped.setWordWrap(True)
            skipped.setToolTip("\n".join(f"{where}: {why}" for where, why in result.skipped[:200]))
            theme.set_role(skipped, "muted")
            layout.addWidget(skipped)

        table = QTableWidget(len(planned), 4)
        table.setHorizontalHeaderLabels(["Termo", "Falar como", "Situação", "Sua pronúncia atual"])
        theme.style_table(table)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = table.horizontalHeader()
        for column in range(4):
            mode = QHeaderView.ResizeMode.Stretch if column in (1, 3) else QHeaderView.ResizeMode.ResizeToContents
            header.setSectionResizeMode(column, mode)
        # Conflitos primeiro: sao o que pede atencao.
        order = {Status.CONFLICT: 0, Status.NEW: 1, Status.SAME: 2}
        for row, item in enumerate(sorted(planned, key=lambda p: order[p.status])):
            values = [item.entry.term, item.entry.spoken, STATUS_LABELS[item.status], item.current]
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        layout.addWidget(table, 1)

        self.overwrite = QCheckBox("Nos conflitos, trocar a minha pronúncia pela do arquivo")
        self.overwrite.setToolTip("Desmarcado, as pronúncias que você já corrigiu continuam valendo.")
        self.overwrite.setEnabled(counts[Status.CONFLICT] > 0)

        footer = QHBoxLayout()
        footer.addWidget(self.overwrite)
        footer.addStretch(1)
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        self.import_button = QPushButton("Importar")
        theme.set_role(self.import_button, "primary")
        self.import_button.setDefault(True)
        self.import_button.setEnabled(counts[Status.NEW] + counts[Status.CONFLICT] > 0)
        self.import_button.clicked.connect(self.accept)
        footer.addWidget(cancel)
        footer.addWidget(self.import_button)
        layout.addLayout(footer)

    def entries_to_save(self) -> list[Entry]:
        wanted = {Status.NEW} | ({Status.CONFLICT} if self.overwrite.isChecked() else set())
        return [p.entry for p in self.planned if p.status in wanted]
