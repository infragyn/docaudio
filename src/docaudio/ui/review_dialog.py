"""Revisao de pronuncia: termos arriscados do documento, para ouvir e corrigir."""

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from docaudio.text_processing.review import Candidate, mark_reviewed
from docaudio.ui import theme

COLUMNS = ["Termo", "Tipo", "Vezes", "Será falado como"]


class ReviewDialog(QDialog):
    """`on_listen(texto_falado)` toca; `on_fix(termo)` abre a correcao e devolve True se salvou."""

    def __init__(
        self,
        title: str,
        candidates: list[Candidate],
        on_listen: Callable[[str], None],
        on_fix: Callable[[str], bool],
        recompute: Callable[[], list[Candidate]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Revisar pronúncia — {title}")
        self.resize(720, 520)
        self.on_listen = on_listen
        self.on_fix = on_fix
        self.recompute = recompute

        layout = QVBoxLayout(self)
        hint = QLabel(
            "Siglas, palavras estrangeiras e nomes técnicos que não estão no dicionário. "
            "Ouça os mais frequentes; corrija os errados e marque os certos para não aparecerem de novo."
        )
        hint.setWordWrap(True)
        theme.set_role(hint, "muted")
        layout.addWidget(hint)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.itemDoubleClicked.connect(lambda *_: self._listen())
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.listen_button = QPushButton("Ouvir")
        self.listen_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))
        self.listen_button.setToolTip("Ouve o termo como será falado (duplo clique também).")
        self.listen_button.clicked.connect(self._listen)
        self.fix_button = QPushButton("Corrigir…")
        self.fix_button.clicked.connect(self._fix)
        self.ok_button = QPushButton("Está certo")
        self.ok_button.setToolTip("Marca os termos selecionados como revisados; eles não aparecem mais aqui.")
        self.ok_button.clicked.connect(self._mark_ok)
        self.count_label = QLabel()
        theme.set_role(self.count_label, "muted")
        close_button = QPushButton("Fechar")
        close_button.clicked.connect(self.accept)
        for widget in (self.listen_button, self.fix_button, self.ok_button):
            buttons.addWidget(widget)
        buttons.addStretch(1)
        buttons.addWidget(self.count_label)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self._fill(candidates)

    def _fill(self, candidates: list[Candidate]) -> None:
        self.candidates = candidates
        self.table.setRowCount(len(candidates))
        for row, item in enumerate(candidates):
            values = [item.term, item.reason_label, str(item.count), item.spoken]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 2:
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, column, cell)
        self.count_label.setText(f"{len(candidates)} termo(s) para revisar")
        if candidates:
            self.table.selectRow(0)
        self._update_buttons()

    def _selected(self) -> list[Candidate]:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        return [self.candidates[row] for row in rows]

    def _update_buttons(self) -> None:
        selected = self._selected()
        self.listen_button.setEnabled(len(selected) == 1)
        self.fix_button.setEnabled(len(selected) == 1)
        self.ok_button.setEnabled(bool(selected))

    def _listen(self) -> None:
        if len(selected := self._selected()) == 1:
            self.on_listen(selected[0].spoken)

    def _fix(self) -> None:
        if len(selected := self._selected()) == 1 and self.on_fix(selected[0].term):
            self._refresh(keep_row=self.table.currentRow())

    def _mark_ok(self) -> None:
        selected = self._selected()
        if selected:
            row = self.table.currentRow()
            mark_reviewed([c.term for c in selected])
            self._refresh(keep_row=row)

    def _refresh(self, keep_row: int) -> None:
        self._fill(self.recompute())
        if self.candidates:
            self.table.selectRow(min(max(keep_row, 0), len(self.candidates) - 1))
