"""Previa editavel: lista de capitulos e texto que sera narrado."""

import html
import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QStyle,
    QGroupBox,
    QTextEdit,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from docaudio.core.editing import apply_chapter_text
from docaudio.core.language import LANGUAGES
from docaudio.core.loader import estimated_seconds, format_duration
from docaudio.core.model import Chapter, Document
from docaudio.core.study import answer_key, study_chapter
from docaudio.text_processing.normalize import normalize
from docaudio.text_processing.pronunciation import PronunciationDictionary, load_default
from docaudio.ui import theme

log = logging.getLogger(__name__)

KIND_LABELS = {
    "docx": "Word (DOCX)",
    "pdf": "PDF com texto",
    "pdf_ocr": "PDF digitalizado",
    "txt": "Texto",
    "html": "Página web",
    "epub": "Livro digital (EPUB)",
    "pptx": "PowerPoint (PPTX)",
}
EDIT_DELAY_MS = 600
# O QTextCursor separa paragrafos selecionados com U+2029.
PARAGRAPH_SEPARATOR = " "


def format_number(value: int) -> str:
    return f"{value:,}".replace(",", ".")


class PreviewPanel(QGroupBox):
    """Mostra um documento carregado e aplica as edicoes ao modelo."""

    reload_requested = Signal()
    document_changed = Signal()
    # texto a ouvir, se ja esta normalizado (modo "Ver como sera falado")
    listen_requested = Signal(str, bool)
    pronunciation_requested = Signal(str)
    review_requested = Signal()
    # idioma de destino, bilingue
    translate_requested = Signal(str, bool)
    restore_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Prévia", parent)
        self.document: Document | None = None
        self.speed = 1.0
        self._dictionary: PronunciationDictionary | None = None
        self._updating = False
        self._locked = False
        # Modo questoes ligado: tempo para pensar em ms; None quando desligado.
        self.study_think_ms: int | None = None
        self._listening = False
        self._edit_timer = QTimer(self, singleShot=True, interval=EDIT_DELAY_MS)
        self._edit_timer.timeout.connect(self.commit_edits)

        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        # O QLabel com quebra de linha dentro de um QHBoxLayout calcula a altura de uma linha so.
        self.summary.setMinimumHeight(self.summary.fontMetrics().lineSpacing() * 2 + 4)
        self.reload_button = QPushButton("Recarregar documento")
        self.reload_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.reload_button.setToolTip("Lê o arquivo de novo, descartando as edições da prévia.")
        self.reload_button.clicked.connect(self.reload_requested)
        self.review_button = QPushButton("Revisar pronúncia…")
        self.review_button.setToolTip(
            "Lista siglas, palavras estrangeiras e nomes técnicos deste documento para ouvir e corrigir antes de converter."
        )
        self.review_button.clicked.connect(self.review_requested)
        self.translate_button = QPushButton("Traduzir")
        self.translate_button.setToolTip(
            "Traduz o documento (offline) para ouvir em outro idioma; o original fica guardado."
        )
        self.translate_menu = QMenu(self.translate_button)
        self.translate_menu.aboutToShow.connect(self._fill_translate_menu)
        self.translate_button.setMenu(self.translate_menu)
        header.addWidget(self.summary, 1)
        header.addWidget(self.translate_button, 0, Qt.AlignmentFlag.AlignTop)
        header.addWidget(self.review_button, 0, Qt.AlignmentFlag.AlignTop)
        header.addWidget(self.reload_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        self.warnings = QLabel()
        self.warnings.setWordWrap(True)
        theme.set_role(self.warnings, "warning")
        layout.addWidget(self.warnings)

        self.chapters = QListWidget()
        self.chapters.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chapters.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.chapters.setMinimumWidth(150)
        self.chapters.setToolTip("Marque os capítulos a converter. Clique duas vezes para renomear.")
        self.chapters.currentRowChanged.connect(self._show_chapter)
        self.chapters.itemChanged.connect(self._on_chapter_item_changed)

        editor_box = QWidget()
        editor_layout = QVBoxLayout(editor_box)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Selecione um documento para ver o texto que será narrado.")
        self.editor.textChanged.connect(self._on_text_changed)
        self.chapter_stats = QLabel()
        theme.set_role(self.chapter_stats, "muted")
        self.spoken_view = QCheckBox("Ver como será falado")
        self.spoken_view.setToolTip(
            "Mostra o texto depois da normalização (números, siglas, abreviações). "
            "Para editar, desmarque."
        )
        self.spoken_view.toggled.connect(self._on_spoken_view_toggled)
        self.listen_button = QPushButton("Ouvir trecho")
        self.listen_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))
        self.listen_button.setToolTip("Ouve o texto selecionado ou, sem seleção, o parágrafo onde está o cursor.")
        self.listen_button.clicked.connect(self._listen)
        self.pronounce_button = QPushButton("Corrigir pronúncia…")
        self.pronounce_button.setToolTip(
            "Selecione uma palavra ou sigla e diga como ela deve soar. Vale para todos os documentos."
        )
        self.pronounce_button.clicked.connect(self._fix_pronunciation)
        self.editor.selectionChanged.connect(self._update_text_actions)
        self.editor.cursorPositionChanged.connect(self._update_text_actions)
        # Estatisticas numa linha propria: o texto longo nao alarga a janela.
        self.chapter_stats.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        footer = QHBoxLayout()
        footer.addWidget(self.listen_button)
        footer.addWidget(self.pronounce_button)
        footer.addStretch(1)
        footer.addWidget(self.spoken_view)
        editor_layout.addWidget(self.editor, 1)
        editor_layout.addWidget(self.chapter_stats)
        editor_layout.addLayout(footer)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.chapters)
        splitter.addWidget(editor_box)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)

        self.set_document(None)

    # ---- exibicao ---------------------------------------------------------

    def set_document(self, document: Document | None, message: str = "") -> None:
        self.commit_edits()
        self.document = document
        self._updating = True
        self.chapters.clear()
        self.editor.clear()
        if document:
            for chapter in document.chapters:
                item = QListWidgetItem(chapter.title)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable)
                item.setCheckState(Qt.CheckState.Checked if chapter.include else Qt.CheckState.Unchecked)
                if any(b.ocr for b in chapter.blocks):
                    item.setBackground(QColor(theme.tokens().ocr_highlight))
                    item.setToolTip("Contém texto reconhecido por OCR: revise antes de converter.")
                self.chapters.addItem(item)
        self._updating = False

        has_document = document is not None
        self.reload_button.setEnabled(has_document)
        self.translate_button.setEnabled(bool(document and document.chapters))
        # A revisao procura termos estranhos ao portugues: nao faz sentido em outro idioma.
        self.review_button.setEnabled(bool(document and document.chapters and document.language == "pt"))
        self._update_read_only()
        self.warnings.setText("\n".join(document.warnings) if document else "")
        self.warnings.setVisible(bool(document and document.warnings))
        if document and not document.chapters:
            self.editor.setPlaceholderText("Nenhum texto encontrado neste documento.")
        else:
            self.editor.setPlaceholderText(message or "Selecione um documento para ver o texto que será narrado.")
        if document and document.chapters:
            self.chapters.setCurrentRow(0)
        self._refresh_summary()
        self._refresh_chapter_stats()
        self._update_text_actions()

    def refresh(self) -> None:
        """Reexibe o documento atual (ex.: apos mudar as regras de leitura)."""
        row = self.chapters.currentRow()
        self.set_document(self.document)
        if 0 <= row < self.chapters.count():
            self.chapters.setCurrentRow(row)

    def set_speed(self, speed: float) -> None:
        self.speed = speed
        self._refresh_summary()
        self._refresh_chapter_stats()

    def current_chapter(self) -> Chapter | None:
        row = self.chapters.currentRow()
        if self.document and 0 <= row < len(self.document.chapters):
            return self.document.chapters[row]
        return None

    def _show_chapter(self, row: int) -> None:
        self.commit_edits()
        chapter = self.current_chapter()
        if not chapter:
            text = ""
        elif self.spoken_view.isChecked():
            language = self.document.language
            dictionary = self._dictionary if language == "pt" and self._dictionary else load_default(language)
            if language == "pt":
                self._dictionary = dictionary
            if self.study_think_ms is not None:
                chapter = study_chapter(chapter, answer_key(self.document), self.study_think_ms, language)
            parts = [normalize(chapter.title, dictionary, language)]
            for block in chapter.included_blocks():
                parts.append(normalize(block.text, dictionary, language))
                if block.pause_after_ms and block.pause_after_ms >= 1000:
                    parts.append(f"[pausa de {block.pause_after_ms // 1000} s para pensar]")
            text = "\n\n".join(parts)
        else:
            text = chapter.narration_text()
        self._updating = True
        self.editor.setPlainText(text)
        self._updating = False
        self._highlight_ocr()
        self._refresh_chapter_stats()

    def _highlight_ocr(self) -> None:
        """Fundo destacado nos paragrafos que vieram do OCR (so no modo de edicao)."""
        chapter = self.current_chapter()
        selections = []
        if chapter and not self.spoken_view.isChecked():
            offset = 0
            for block in chapter.included_blocks():
                if block.ocr:
                    selection = QTextEdit.ExtraSelection()
                    selection.format.setBackground(QColor(theme.tokens().ocr_highlight))
                    cursor = QTextCursor(self.editor.document())
                    cursor.setPosition(offset)
                    cursor.setPosition(offset + len(block.text), QTextCursor.MoveMode.KeepAnchor)
                    selection.cursor = cursor
                    selections.append(selection)
                offset += len(block.text) + 2
        self.editor.setExtraSelections(selections)

    # ---- ouvir e corrigir pronuncia ------------------------------------------

    MAX_TERM_CHARS = 40
    MAX_LISTEN_CHARS = 600

    def selected_term(self) -> str:
        text = self.editor.textCursor().selectedText().strip()
        if not text or PARAGRAPH_SEPARATOR in text or len(text) > self.MAX_TERM_CHARS:
            return ""
        return text.strip(".,;:!?()[]\"'“”")

    def text_to_listen(self) -> str:
        cursor = self.editor.textCursor()
        text = cursor.selectedText().replace(PARAGRAPH_SEPARATOR, "\n") or cursor.block().text()
        text = text.strip()
        if len(text) > self.MAX_LISTEN_CHARS:
            # Corta no fim de uma frase para a amostra nao demorar (voz clonada e lenta).
            cut = text.rfind(". ", 0, self.MAX_LISTEN_CHARS)
            text = text[: cut + 1 if cut > 0 else self.MAX_LISTEN_CHARS]
        return text

    def _update_text_actions(self) -> None:
        has_chapter = self.current_chapter() is not None and not self._locked
        self.listen_button.setEnabled(has_chapter and bool(self.text_to_listen()) and not self._listening)
        self.pronounce_button.setEnabled(has_chapter and bool(self.selected_term()))

    def set_listening(self, active: bool) -> None:
        self._listening = active
        self.listen_button.setText("Gerando…" if active else "Ouvir trecho")
        self._update_text_actions()

    def _listen(self) -> None:
        text = self.text_to_listen()
        if text:
            self.listen_requested.emit(text, self.spoken_view.isChecked())

    def _fix_pronunciation(self) -> None:
        if term := self.selected_term():
            self.pronunciation_requested.emit(term)

    def _fill_translate_menu(self) -> None:
        self.translate_menu.clear()
        document = self.document
        if document is None:
            return
        original = document.translated_from or document.language
        for code, name in LANGUAGES.items():
            if code == original:
                continue
            plain = self.translate_menu.addAction(f"Para {name.lower()}")
            plain.triggered.connect(lambda _=False, c=code: self.translate_requested.emit(c, False))
            both = self.translate_menu.addAction(f"Para {name.lower()} — bilíngue (original e tradução)")
            both.setToolTip("Cada parágrafo no original, com a voz do idioma original, seguido da tradução.")
            both.triggered.connect(lambda _=False, c=code: self.translate_requested.emit(c, True))
            self.translate_menu.addSeparator()
        back = self.translate_menu.addAction(f"Voltar ao original ({LANGUAGES[original].lower()})")
        back.setEnabled(bool(document.translated_from))
        back.triggered.connect(self.restore_requested)

    def dictionary_changed(self) -> None:
        """Recarrega o dicionario de pronuncia (ex.: depois de corrigir um termo)."""
        self._dictionary = load_default()
        if self.spoken_view.isChecked():
            self._show_chapter(self.chapters.currentRow())

    def _update_read_only(self) -> None:
        editable = bool(self.document and self.document.chapters) and not self.spoken_view.isChecked()
        self.editor.setReadOnly(not editable)

    def _on_spoken_view_toggled(self, checked: bool) -> None:
        self.commit_edits()
        if checked:
            # Recarrega o dicionario para refletir edicoes feitas no CSV.
            self._dictionary = load_default()
        self._update_read_only()
        self._show_chapter(self.chapters.currentRow())

    def _duration(self, characters: int) -> str:
        return format_duration(estimated_seconds(characters, self.speed))

    def _refresh_summary(self) -> None:
        document = self.document
        if not document:
            self.summary.setText("Nenhum documento selecionado.")
            return
        selected = document.included_chapters()
        parts = [
            f"<b>{html.escape(document.title)}</b>",
            KIND_LABELS.get(document.kind.value, document.kind.value),
            LANGUAGES.get(document.language, document.language)
            + (f" (traduzido do {LANGUAGES[document.translated_from].lower()})" if document.translated_from else ""),
        ]
        if document.page_count:
            parts.append(f"{document.page_count} página(s)")
        parts.append(f"{len(selected)} de {len(document.chapters)} capítulo(s) selecionados")
        characters = document.char_count()
        parts.append(f"{format_number(characters)} caracteres")
        parts.append(f"duração estimada ≈ {self._duration(characters)}")
        self.summary.setText(" · ".join(parts))

    def _refresh_chapter_stats(self) -> None:
        chapter = self.current_chapter()
        if not chapter:
            self.chapter_stats.setText("")
            return
        hidden = len(chapter.blocks) - len(chapter.included_blocks())
        text = f"{format_number(chapter.char_count())} caracteres · ≈ {self._duration(chapter.char_count())}"
        if hidden:
            text += f" · {hidden} item(ns) ignorado(s) pelas regras de leitura"
        if not chapter.char_count():
            text = "Capítulo sem texto narrável" + (f" · {hidden} item(ns) ignorado(s)" if hidden else "")
        self.chapter_stats.setText(text)

    # ---- edicao -----------------------------------------------------------

    def set_locked(self, locked: bool) -> None:
        """Bloqueia edicoes enquanto ha conversao em andamento."""
        self.chapters.setEnabled(not locked)
        self._locked = locked
        self._update_text_actions()
        self.reload_button.setEnabled(not locked and self.document is not None)
        if locked:
            self.commit_edits()
            self.editor.setReadOnly(True)
        else:
            self._update_read_only()

    def _on_text_changed(self) -> None:
        if not self._updating and not self.spoken_view.isChecked() and self.current_chapter():
            self._edit_timer.start()

    def commit_edits(self) -> None:
        """Aplica ao modelo as edicoes pendentes do capitulo exibido."""
        if not self._edit_timer.isActive():
            return
        self._edit_timer.stop()
        chapter = self.current_chapter()
        if chapter and apply_chapter_text(chapter, self.editor.toPlainText()):
            log.debug("Capítulo '%s' editado na prévia.", chapter.title)
            self._highlight_ocr()
            self._refresh_summary()
            self._refresh_chapter_stats()
            self.document_changed.emit()

    def _on_chapter_item_changed(self, item: QListWidgetItem) -> None:
        if self._updating or not self.document:
            return
        chapter = self.document.chapters[self.chapters.row(item)]
        chapter.include = item.checkState() == Qt.CheckState.Checked
        title = item.text().strip()
        if title and title != chapter.title:
            log.info("Capítulo %d renomeado para '%s'.", chapter.number, title)
            chapter.title = title
        elif not title:
            self._updating = True
            item.setText(chapter.title)
            self._updating = False
        self._refresh_summary()
        self.document_changed.emit()
