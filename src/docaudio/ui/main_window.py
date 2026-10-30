"""Janela principal: selecao de documentos, configuracoes de voz e log."""

import copy
import itertools
import logging
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QThreadPool, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QDragEnterEvent, QDropEvent, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QScrollArea,
    QStyle,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from docaudio import APP_NAME, __version__
from docaudio.core import dependencies, documents, paths
from docaudio.core.conversion import Interrupted, find_interrupted, safe_filename
from docaudio.core.language import AUTO as AUTO_LANGUAGE
from docaudio.core.language import DEFAULT as DEFAULT_LANGUAGE
from docaudio.core.language import LANGUAGES, detect_language, language_of_voice, phrase
from docaudio.core.loader import (
    LONG_CONVERSION_SECONDS,
    estimated_generation_seconds,
    format_duration,
    load_document,
)
from docaudio.core.model import BlockType, Document
from docaudio.core import text_export, translation
from docaudio.core.rules import apply_rules
from docaudio.speech import clone_provider
from docaudio.speech.registry import VoiceRegistry
from docaudio.storage.settings import MAX_SPEED, MIN_SPEED, Settings
from docaudio.ui import audio_player
from docaudio.ui import theme
from docaudio.ui.log_panel import LogPanel
from docaudio.text_processing import review
from docaudio.text_processing.normalize import normalize
from docaudio.text_processing import dictionary_import, pronunciation
from docaudio.text_processing.pronunciation import ensure_user_dictionary, load_default, save_user_entries, save_user_entry
from docaudio.ui.conversion_worker import ConversionWorker, QueueItem
from docaudio.audio.playback import PlaybackError
from docaudio.ui.player import PlayerDialog
from docaudio.ui.player_panel import PlayerPanel
from docaudio.ui.translate_dialog import SAVE_FILTER, TranslateDialog
from docaudio.ui.preview import PreviewPanel
from docaudio.ui.watch import FolderWatcher
from docaudio.ui.pronunciation_dialog import PronunciationDialog
from docaudio.ui.import_dialog import ImportDialog
from docaudio.ui.languages_dialog import LanguagesDialog
from docaudio.ui.review_dialog import ReviewDialog
from docaudio.ui.workers import Worker

log = logging.getLogger(__name__)

# Texto do item da lista sem a situacao na fila.
BASE_TEXT_ROLE = Qt.ItemDataRole.UserRole + 1

# Frase de amostra em portugues (os outros idiomas usam core.language.phrase).
SAMPLE_PHRASE = (
    "Olá! Esta é uma amostra da voz escolhida. "
    "O capítulo um apresenta os conceitos básicos do documento."
)


class DocumentList(QListWidget):
    """Lista de documentos que aceita arrastar e soltar arquivos."""

    def __init__(self, on_files_dropped, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_files_dropped = on_files_dropped
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if not urls:
            return super().dropEvent(event)
        self._on_files_dropped([Path(u.toLocalFile()) for u in urls if u.isLocalFile()])
        event.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, provider: VoiceRegistry, log_file: Path) -> None:
        super().__init__()
        self.settings = settings
        self.provider = provider
        self.log_file = log_file
        self.pool = QThreadPool.globalInstance()
        self._sample_counter = itertools.count(1)
        # Documentos carregados, por caminho; edicoes da previa vivem nestes objetos.
        self.loaded: dict[str, Document] = {}
        self.load_errors: dict[str, str] = {}
        self.loading: set[str] = set()
        self.edited: set[str] = set()
        # Original de cada documento traduzido (o traduzido fica em self.loaded).
        self.originals: dict[str, Document] = {}
        # Referencias as tarefas em andamento: sem elas os sinais podem ser coletados antes de chegar.
        self._workers: set[Worker] = set()
        self.conversion: ConversionWorker | None = None
        # Situacao de cada documento na fila ("na fila", "convertendo", "concluído"...).
        self.doc_status: dict[str, str] = {}
        # Conversoes por terminar encontradas na pasta de saida, por caminho do documento.
        self.interrupted: dict[str, Interrupted] = {}
        self.tray: QSystemTrayIcon | None = None
        self.player_dialog: PlayerDialog | None = None
        self.text_translator: TranslateDialog | None = None

        self.setWindowTitle(f"{APP_NAME} — Documentos para áudio")
        self.resize(1280, 800)
        self._build_menu()
        self._build_ui()
        self._init_clone_support()
        self._load_voices()
        self._refresh_output_dir()
        self._update_engine_status()
        self.check_dependencies(show_dialog=False)
        self.refresh_interrupted(announce=True)
        self._restore_documents()
        self._init_watch()
        log.info("%s %s iniciado. Log em %s", APP_NAME, __version__, log_file)

    # ---- construcao -------------------------------------------------------

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&Arquivo")
        add_action = QAction("&Adicionar documentos…", self)
        add_action.setShortcut(QKeySequence.StandardKey.Open)
        add_action.triggered.connect(self.choose_documents)
        file_menu.addAction(add_action)
        listen_action = QAction("&Ouvir audiolivro convertido…", self)
        listen_action.setShortcut("Ctrl+L")
        listen_action.triggered.connect(self.choose_audiobook)
        file_menu.addAction(listen_action)
        file_menu.addSeparator()
        quit_action = QAction("&Sair", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        tools_menu = self.menuBar().addMenu("&Ferramentas")
        check_action = QAction("&Verificar dependências", self)
        check_action.triggered.connect(lambda: self.check_dependencies(show_dialog=True))
        tools_menu.addAction(check_action)
        logs_action = QAction("Abrir pasta de &logs", self)
        logs_action.triggered.connect(lambda: self._open_folder(self.log_file.parent))
        tools_menu.addAction(logs_action)
        tools_menu.setToolTipsVisible(True)
        self.clone_action = QAction("Criar voz &clonada…", self)
        self.clone_action.triggered.connect(self.open_clone_dialog)
        tools_menu.addAction(self.clone_action)
        self.clone_allowed_action = QAction("Permitir clonagem de voz nesta máquina", self)
        self.clone_allowed_action.setCheckable(True)
        self.clone_allowed_action.setChecked(self.settings.clone_enabled)
        self.clone_allowed_action.toggled.connect(self._on_clone_allowed_toggled)
        tools_menu.addAction(self.clone_allowed_action)
        tools_menu.addSeparator()
        languages_action = QAction("&Idiomas e vozes…", self)
        languages_action.setToolTip("Vozes e OCR para documentos em português, inglês e espanhol.")
        languages_action.triggered.connect(self.open_languages)
        tools_menu.addAction(languages_action)
        translate_action = QAction("&Traduzir texto…", self)
        translate_action.setShortcut("Ctrl+T")
        translate_action.triggered.connect(lambda: self.open_text_translator())
        tools_menu.addAction(translate_action)
        tools_menu.addSeparator()
        watch_choose = QAction("Escolher pasta &vigiada…", self)
        watch_choose.setToolTip("Documentos novos colocados nessa pasta são convertidos sozinhos, com a voz atual.")
        watch_choose.triggered.connect(self.choose_watch_folder)
        tools_menu.addAction(watch_choose)
        self.watch_action = QAction("Converter automaticamente da pasta vigiada", self)
        self.watch_action.setCheckable(True)
        self.watch_action.toggled.connect(self._on_watch_toggled)
        tools_menu.addAction(self.watch_action)
        tools_menu.addSeparator()
        review_action = QAction("&Revisar pronúncia do documento…", self)
        review_action.triggered.connect(self.open_pronunciation_review)
        tools_menu.addAction(review_action)
        dictionary_action = QAction("Editar dicionário de &pronúncia…", self)
        dictionary_action.triggered.connect(self.edit_dictionary)
        tools_menu.addAction(dictionary_action)
        import_action = QAction("&Importar dicionário de pronúncia…", self)
        import_action.setToolTip("Dicionários de leitores de tela (NVDA .dic), léxicos PLS ou planilhas CSV/TSV.")
        import_action.triggered.connect(self.import_dictionary)
        tools_menu.addAction(import_action)
        themes_menu = tools_menu.addMenu("Dicionários &temáticos")
        themes_menu.setToolTipsVisible(True)
        pronunciation.set_themes(self.settings.pronunciation_themes)
        for name, label in pronunciation.THEMES.items():
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(name in self.settings.pronunciation_themes)
            action.setToolTip(f"Pronúncias prontas de {label.lower()} (arquivo temas/{name}.csv).")
            action.toggled.connect(lambda on, n=name: self._on_theme_toggled(n, on))
            themes_menu.addAction(action)
        tools_menu.addSeparator()
        data_action = QAction("Abrir pasta de &dados", self)
        data_action.triggered.connect(lambda: self._open_folder(paths.data_dir()))
        tools_menu.addAction(data_action)

        help_menu = self.menuBar().addMenu("A&juda")
        about_action = QAction("&Sobre", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _icon(self, pixmap: QStyle.StandardPixmap):
        return self.style().standardIcon(pixmap)

    @staticmethod
    def _compact_combo() -> QComboBox:
        """Caixa que encolhe com o painel; a lista aberta mostra o texto inteiro."""
        combo = QComboBox()
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(16)
        combo.view().setMinimumWidth(320)
        return combo

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(8)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setTextFormat(Qt.TextFormat.PlainText)
        self.banner.setVisible(False)
        root.addWidget(self.banner)

        left_content = QWidget()
        left_layout = QVBoxLayout(left_content)
        left_layout.setContentsMargins(0, 0, 6, 1)
        left_layout.setSpacing(10)
        left_layout.addWidget(self._build_documents_group(), 1)
        # Opcoes em abas: cada grupo aparece inteiro, sem depender de rolagem.
        self.options_tabs = QTabWidget()
        self.options_tabs.setDocumentMode(True)
        self.options_tabs.addTab(self._build_voice_group(), "Voz e saída")
        self.options_tabs.addTab(self._build_reading_group(), "Leitura")
        self.options_tabs.addTab(self._build_conversion_group(), "Áudio")
        self.player_panel = PlayerPanel(self.settings, self._set_setting)
        self.options_tabs.addTab(self.player_panel, "Player")
        tab = self.settings.options_tab
        self.options_tabs.setCurrentIndex(tab if isinstance(tab, int) and 0 <= tab < self.options_tabs.count() else 0)
        self.options_tabs.currentChanged.connect(lambda i: self._set_setting("options_tab", i))
        left_layout.addWidget(self.options_tabs)
        # Rolagem so como ultimo recurso em telas muito baixas.
        left = QScrollArea()
        left.setWidget(left_content)
        left.setWidgetResizable(True)
        left.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Nunca mais estreito que o conteudo: sem rolagem horizontal, nada pode ficar cortado.
        left.setMinimumWidth(left_content.minimumSizeHint().width() + left.verticalScrollBar().sizeHint().width())
        left.setSizeAdjustPolicy(QScrollArea.SizeAdjustPolicy.AdjustToContents)

        self.preview = PreviewPanel()
        self.preview.set_speed(self.settings.speed)
        self.preview.reload_requested.connect(self.reload_current_document)
        self.preview.document_changed.connect(self._on_preview_edited)
        self.preview.listen_requested.connect(self._listen_excerpt)
        self.preview.study_think_ms = self.settings.think_pause_s * 1000 if self.settings.study_mode else None
        self.preview.pronunciation_requested.connect(self._fix_pronunciation)
        self.preview.review_requested.connect(self.open_pronunciation_review)
        self.preview.translate_requested.connect(self.translate_current)
        self.preview.restore_requested.connect(self.restore_original)
        self.preview.save_translation_requested.connect(self.save_translation)
        self.preview.text_translator_requested.connect(self.open_text_translator)

        top_widget = QSplitter(Qt.Orientation.Horizontal)
        top_widget.addWidget(left)
        top_widget.addWidget(self.preview)
        top_widget.setStretchFactor(0, 2)
        top_widget.setStretchFactor(1, 5)
        top_widget.setSizes([440, 840])

        log_group = QGroupBox("Registro de atividades")
        log_layout = QVBoxLayout(log_group)
        self.log_panel = LogPanel()
        log_layout.addWidget(self.log_panel)

        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        self.convert_button = QPushButton("Converter documentos")
        self.convert_button.setDefault(True)
        self.convert_button.setMinimumHeight(38)
        self.convert_button.setIcon(self._icon(QStyle.StandardPixmap.SP_MediaPlay))
        theme.set_role(self.convert_button, "primary")
        self.convert_button.clicked.connect(self.start_conversion)
        self.cancel_button = QPushButton("Cancelar")
        self.cancel_button.setMinimumHeight(38)
        self.cancel_button.setIcon(self._icon(QStyle.StandardPixmap.SP_MediaStop))
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_conversion)
        self.resume_button = QPushButton("Retomar")
        self.resume_button.setMinimumHeight(38)
        self.resume_button.setIcon(self._icon(QStyle.StandardPixmap.SP_MediaSeekForward))
        self.resume_button.clicked.connect(self.resume_interrupted)
        self.resume_button.setVisible(False)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setTextVisible(False)
        self.progress_label = QLabel("Pronto.")
        theme.set_role(self.progress_label, "muted")
        progress_box = QVBoxLayout()
        progress_box.addWidget(self.progress_label)
        progress_box.addWidget(self.progress_bar)
        actions_layout.addWidget(self.convert_button)
        actions_layout.addWidget(self.cancel_button)
        actions_layout.addWidget(self.resume_button)
        actions_layout.addLayout(progress_box, 1)

        upper = QWidget()
        upper_layout = QVBoxLayout(upper)
        upper_layout.setContentsMargins(0, 0, 0, 0)
        upper_layout.addWidget(top_widget, 1)
        upper_layout.addWidget(actions)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(upper)
        splitter.addWidget(log_group)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([760, 110])
        root.addWidget(splitter, 1)

        self.setCentralWidget(central)
        self.engine_label = QLabel()
        self.statusBar().addPermanentWidget(self.engine_label)

    def _build_documents_group(self) -> QGroupBox:
        group = QGroupBox("Documentos")
        layout = QVBoxLayout(group)
        # Sem quebra de linha: um QLabel com quebra faz a rolagem usar a altura preferida do painel.
        hint = QLabel("Adicione documentos ou arraste-os para a lista.")
        hint.setToolTip(f"Formatos aceitos: {documents.FORMATS_LABEL}.")
        theme.set_role(hint, "muted")
        layout.addWidget(hint)

        self.doc_list = DocumentList(self.add_documents)
        self.doc_list.setMinimumHeight(56)
        self.doc_list.itemSelectionChanged.connect(self._update_doc_buttons)
        self.doc_list.currentItemChanged.connect(lambda *_: self._show_selected_document())
        layout.addWidget(self.doc_list, 1)

        buttons = QHBoxLayout()
        add_button = QPushButton("Adicionar…")
        add_button.setIcon(self._icon(QStyle.StandardPixmap.SP_FileDialogNewFolder))
        add_button.clicked.connect(self.choose_documents)
        self.remove_button = QPushButton("Remover")
        self.remove_button.setIcon(self._icon(QStyle.StandardPixmap.SP_DialogCancelButton))
        self.remove_button.clicked.connect(self.remove_selected_documents)
        self.clear_button = QPushButton("Limpar")
        self.clear_button.setIcon(self._icon(QStyle.StandardPixmap.SP_TrashIcon))
        self.clear_button.clicked.connect(self.clear_documents)
        self.doc_count = QLabel()
        theme.set_role(self.doc_count, "muted")
        buttons.addWidget(add_button)
        buttons.addWidget(self.remove_button)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        buttons.addWidget(self.doc_count)
        layout.addLayout(buttons)
        self._update_doc_buttons()
        return group

    def _build_voice_group(self) -> QWidget:
        group = QWidget()
        form = QFormLayout(group)

        self.language_combo = self._compact_combo()
        self.language_combo.addItem("Automático", AUTO_LANGUAGE)
        for code, name in LANGUAGES.items():
            self.language_combo.addItem(name, code)
        self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(self.settings.document_language)))
        self.language_combo.setToolTip(
            "Idioma dos documentos. No automático, o DocAudio detecta o idioma de cada um e usa a voz "
            "escolhida para esse idioma."
        )
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        form.addRow("Idioma:", self.language_combo)

        self.voice_combo = self._compact_combo()
        self.voice_combo.currentIndexChanged.connect(self._on_voice_changed)
        form.addRow("Voz:", self.voice_combo)

        speed_row = QHBoxLayout()
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(int(MIN_SPEED * 100), int(MAX_SPEED * 100))
        self.speed_slider.setSingleStep(5)
        self.speed_slider.setPageStep(25)
        self.speed_slider.setValue(round(self.settings.speed * 100))
        self.speed_slider.valueChanged.connect(self._on_speed_changed)
        self.speed_value = QLabel()
        self.speed_value.setMinimumWidth(48)
        speed_row.addWidget(self.speed_slider, 1)
        speed_row.addWidget(self.speed_value)
        form.addRow("Velocidade:", speed_row)
        self._show_speed(self.settings.speed)

        self.sample_button = QPushButton("Ouvir amostra")
        self.sample_button.setIcon(self._icon(QStyle.StandardPixmap.SP_MediaVolume))
        self.sample_button.clicked.connect(self.play_sample)
        form.addRow("", self.sample_button)

        self.output_edit = QLineEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setMinimumWidth(120)
        # Botoes so com icone na mesma linha do caminho: a aba fica uma linha mais baixa.
        choose_output = QPushButton()
        choose_output.setIcon(self._icon(QStyle.StandardPixmap.SP_DirOpenIcon))
        choose_output.setToolTip("Escolher a pasta de saída…")
        choose_output.clicked.connect(self.choose_output_dir)
        open_output = QPushButton()
        open_output.setIcon(self._icon(QStyle.StandardPixmap.SP_DirLinkIcon))
        open_output.setToolTip("Abrir a pasta de saída")
        open_output.clicked.connect(lambda: self._open_folder(self.settings.resolved_output_dir()))
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(choose_output)
        output_row.addWidget(open_output)
        form.addRow("Pasta de saída:", output_row)
        return group

    def _build_reading_group(self) -> QWidget:
        group = QWidget()
        layout = QVBoxLayout(group)
        # Duas colunas para a aba caber inteira mesmo em telas baixas.
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        options = [
            ("read_tables", "Ler tabelas", "Narra o conteúdo das tabelas, linha a linha."),
            ("read_urls", "Ler endereços web", "Narra links e e-mails encontrados no texto."),
            ("read_notes", "Ler notas de rodapé", "Inclui as notas de rodapé na narração."),
            ("read_references", "Ler referências", "Inclui as referências bibliográficas."),
            ("read_code", "Ler código-fonte", "Narra trechos de código; desmarcado, eles são pulados."),
        ]
        checks = []
        for field_name, label, tip in options:
            checkbox = QCheckBox(label)
            checkbox.setToolTip(tip)
            checkbox.setChecked(getattr(self.settings, field_name))
            checkbox.toggled.connect(
                lambda checked, f=field_name, text=label: self._on_reading_rule_changed(f, checked, text)
            )
            checks.append(checkbox)

        accents_check = QCheckBox("Corrigir acentos")
        accents_check.setChecked(self.settings.restore_accents)
        accents_check.setToolTip(
            "Corrige palavras digitadas sem acento. Ex.: \"Informacao\" vira \"Informação\" antes da narração. "
            "Vale para documentos carregados ou recarregados depois."
        )
        accents_check.toggled.connect(lambda on: self._set_setting("restore_accents", on))
        checks.append(accents_check)

        ocr_check = QCheckBox("Ler digitalizados (OCR)")
        ocr_check.setChecked(self.settings.ocr_enabled)
        ocr_check.setToolTip(
            "Reconhece o texto de páginas escaneadas. "
            "Vale para documentos carregados ou recarregados depois da mudança."
        )
        ocr_check.toggled.connect(self._on_ocr_toggled)
        checks.append(ocr_check)
        precise_check = QCheckBox("OCR preciso (lento)")
        precise_check.setChecked(self.settings.ocr_quality == "preciso")
        precise_check.setToolTip(
            "Usa o modelo de reconhecimento mais preciso (cerca de 10x mais lento). "
            "Vale para documentos carregados ou recarregados depois."
        )
        precise_check.toggled.connect(
            lambda on: self._set_setting("ocr_quality", "preciso" if on else "rapido")
        )
        ocr_check.toggled.connect(precise_check.setEnabled)
        precise_check.setEnabled(self.settings.ocr_enabled)
        checks.append(precise_check)

        for index, checkbox in enumerate(checks):
            grid.addWidget(checkbox, index // 2, index % 2)

        study_check = QCheckBox("Modo questões")
        study_check.setChecked(self.settings.study_mode)
        study_check.setToolTip(
            "Depois das alternativas de cada questão, faz uma pausa para você pensar e lê a resposta do "
            "gabarito (que deixa de ser lido no fim). Em \"Pergunta: … Resposta: …\", pausa antes da resposta."
        )
        self.think_spin = QSpinBox()
        self.think_spin.setRange(1, 60)
        self.think_spin.setPrefix("pensar ")
        self.think_spin.setSuffix(" s")
        self.think_spin.setValue(self.settings.think_pause_s)
        self.think_spin.setToolTip("Silêncio antes da resposta, para você responder de cabeça.")
        self.think_spin.setEnabled(self.settings.study_mode)
        study_check.toggled.connect(self.think_spin.setEnabled)
        study_check.toggled.connect(lambda on: self._on_study_changed("study_mode", on))
        self.think_spin.valueChanged.connect(lambda v: self._on_study_changed("think_pause_s", v))
        row = (len(checks) + 1) // 2
        grid.addWidget(study_check, row, 0)
        grid.addWidget(self.think_spin, row, 1)
        layout.addLayout(grid)

        layout.addStretch(1)
        return group

    def _build_conversion_group(self) -> QWidget:
        group = QWidget()
        form = QFormLayout(group)
        self.format_combo = self._compact_combo()
        for value, label in (
            ("mp3", "MP3, um arquivo por capítulo"),
            ("m4a", "Audiolivro M4A (abre no Windows)"),
            ("m4b", "Audiolivro M4B (apps de audiolivro)"),
            ("wav", "WAV sem compressão (não precisa de FFmpeg)"),
        ):
            self.format_combo.addItem(label, value)
        self.format_combo.setCurrentIndex(max(0, self.format_combo.findData(self.settings.output_format)))
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        form.addRow("Formato:", self.format_combo)

        self.announce_check = QCheckBox("Narrar o título no início de cada capítulo")
        self.announce_check.setChecked(self.settings.announce_titles)
        self.announce_check.toggled.connect(lambda on: self._set_setting("announce_titles", on))
        form.addRow(self.announce_check)

        self.pause_spin = QSpinBox()
        self.pause_spin.setRange(200, 3000)
        self.pause_spin.setSingleStep(100)
        self.pause_spin.setSuffix(" ms")
        self.pause_spin.setValue(self.settings.paragraph_pause_ms)
        self.pause_spin.valueChanged.connect(lambda v: self._set_setting("paragraph_pause_ms", v))
        form.addRow("Pausa entre parágrafos:", self.pause_spin)
        self.max_chapter_spin = QSpinBox()
        self.max_chapter_spin.setRange(0, 500)
        self.max_chapter_spin.setSingleStep(5)
        self.max_chapter_spin.setPrefix("acima de ")
        self.max_chapter_spin.setSuffix(" mil caracteres")
        self.max_chapter_spin.setSpecialValueText("não dividir")
        self.max_chapter_spin.setValue(self.settings.max_chapter_chars // 1000)
        self.max_chapter_spin.setToolTip("Vale para documentos carregados ou recarregados depois da mudança.")
        self.max_chapter_spin.valueChanged.connect(self._on_max_chapter_changed)
        form.addRow("Dividir capítulos:", self.max_chapter_spin)
        return group

    def _set_setting(self, name: str, value) -> None:
        setattr(self.settings, name, value)
        self._save_settings()

    def _on_format_changed(self, index: int) -> None:
        self._set_setting("output_format", self.format_combo.itemData(index))
        self.check_dependencies(show_dialog=False)

    # ---- documentos -------------------------------------------------------

    def choose_documents(self) -> None:
        start = self.settings.last_open_dir or str(Path.home())
        files, _ = QFileDialog.getOpenFileNames(
            self, "Selecionar documentos", start, documents.FILE_DIALOG_FILTER
        )
        if files:
            self.settings.last_open_dir = str(Path(files[0]).parent)
            self._save_settings()
            self.add_documents([Path(f) for f in files])

    def document_paths(self) -> list[Path]:
        return [
            Path(self.doc_list.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self.doc_list.count())
        ]

    def add_documents(self, candidates: list[Path]) -> None:
        current = self.document_paths()
        accepted, rejected = documents.split_supported([*current, *candidates])
        new = [p for p in accepted if p not in current]
        for path in new:
            item = QListWidgetItem()
            item.setIcon(self._icon(QStyle.StandardPixmap.SP_FileIcon))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.doc_list.addItem(item)
            self._set_item_text(str(path), path.name)
            log.info("Documento adicionado: %s", path)
        if new:
            self.doc_list.setCurrentRow(self.doc_list.count() - len(new))
            self._remember_documents()
        for path in rejected:
            log.warning("Arquivo ignorado (use %s): %s", documents.FORMATS_LABEL, path)
        if rejected:
            names = "\n".join(p.name for p in rejected[:10])
            QMessageBox.information(
                self,
                "Arquivos ignorados",
                f"Formatos aceitos: {documents.FORMATS_LABEL}. Ignorados:\n\n{names}",
            )
        self._update_doc_buttons()

    # ---- pasta vigiada ----------------------------------------------------------

    def _init_watch(self) -> None:
        self.watcher = FolderWatcher(self)
        self.watcher.files_ready.connect(self._on_watched_files)
        self.watch_label = QLabel()
        theme.set_role(self.watch_label, "muted")
        self.statusBar().addWidget(self.watch_label)
        folder = Path(self.settings.watch_dir) if self.settings.watch_dir else None
        self.watch_action.setEnabled(folder is not None)
        # Sem disparar o toggled: so liga se a pasta ainda existir.
        self.watch_action.blockSignals(True)
        self.watch_action.setChecked(bool(self.settings.watch_enabled and folder and folder.is_dir()))
        self.watch_action.blockSignals(False)
        if self.watch_action.isChecked():
            # Arquivos que chegaram com o programa fechado tambem entram.
            self.watcher.start(folder, include_existing=True)
        self._refresh_watch_label()

    def choose_watch_folder(self) -> None:
        start = self.settings.watch_dir or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Pasta vigiada", start)
        if not folder:
            return
        existing = len(documents.split_supported(p for p in Path(folder).iterdir() if p.is_file())[0])
        include = False
        if existing:
            answer = QMessageBox.question(
                self,
                "Pasta vigiada",
                f"A pasta já tem {existing} documento(s). Converter esses também?\n\n"
                "Se responder Não, só os documentos colocados a partir de agora serão convertidos.",
            )
            include = answer == QMessageBox.StandardButton.Yes
        self.settings.watch_dir = folder
        self.settings.watch_enabled = True
        self._save_settings()
        self.watch_action.setEnabled(True)
        self.watch_action.blockSignals(True)
        self.watch_action.setChecked(True)
        self.watch_action.blockSignals(False)
        self.watcher.start(Path(folder), include_existing=include)
        self._refresh_watch_label()

    def _on_watch_toggled(self, enabled: bool) -> None:
        self._set_setting("watch_enabled", enabled)
        if enabled and self.settings.watch_dir and Path(self.settings.watch_dir).is_dir():
            self.watcher.start(Path(self.settings.watch_dir), include_existing=True)
        else:
            self.watcher.stop()
            log.info("Pasta vigiada desligada.")
        self._refresh_watch_label()

    def _refresh_watch_label(self) -> None:
        active = self.watcher.folder is not None
        self.watch_label.setText(f"Vigiando: {self.watcher.folder.name}" if active else "")
        self.watch_label.setToolTip(str(self.watcher.folder) if active else "")
        self.watch_action.setToolTip(self.settings.watch_dir or "Escolha uma pasta primeiro.")

    def _on_watched_files(self, files: list) -> None:
        self.add_documents(files)
        keys = [str(Path(f).resolve()) for f in files]
        queued = set(self.conversion.keys()) if self.conversion else set()
        items = [self._queue_item(key, replace(self.settings)) for key in keys if key not in queued]
        if items:
            log.info("Convertendo automaticamente: %s", ", ".join(Path(i.key).name for i in items))
            self._enqueue(items)

    def _remember_documents(self) -> None:
        paths_now = [str(p) for p in self.document_paths()]
        if paths_now != self.settings.documents:
            self.settings.documents = paths_now
            self._save_settings()

    def _restore_documents(self) -> None:
        """Reabre a lista da sessao anterior (arquivos que nao existem mais ficam de fora)."""
        existing = [Path(p) for p in self.settings.documents if Path(p).is_file()]
        missing = len(self.settings.documents) - len(existing)
        if existing:
            self.add_documents(existing)
            self.doc_list.setCurrentRow(0)
            log.info("%d documento(s) da sessão anterior reabertos.", len(existing))
        if missing:
            log.warning("%d documento(s) da sessão anterior não foram encontrados.", missing)
            self._remember_documents()

    def remove_selected_documents(self) -> None:
        for item in self.doc_list.selectedItems():
            key = item.data(Qt.ItemDataRole.UserRole)
            log.info("Documento removido: %s", key)
            self._forget(key)
            self.doc_list.takeItem(self.doc_list.row(item))
        self._remember_documents()
        self._update_doc_buttons()
        self._show_selected_document()

    def clear_documents(self) -> None:
        self.doc_list.clear()
        for key in list(self.loaded) + list(self.load_errors):
            self._forget(key)
        log.info("Lista de documentos limpa.")
        self._remember_documents()
        self._update_doc_buttons()
        self._show_selected_document()

    def _forget(self, key: str) -> None:
        self.loaded.pop(key, None)
        self.originals.pop(key, None)
        self.load_errors.pop(key, None)
        self.edited.discard(key)

    def _current_key(self) -> str | None:
        item = self.doc_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _item_for(self, key: str) -> QListWidgetItem | None:
        for i in range(self.doc_list.count()):
            if self.doc_list.item(i).data(Qt.ItemDataRole.UserRole) == key:
                return self.doc_list.item(i)
        return None

    def _set_item_text(self, key: str, base: str | None = None) -> None:
        """Texto do item: descricao do documento mais a situacao na fila de conversao."""
        item = self._item_for(key)
        if item is None:
            return
        if base is not None:
            item.setData(BASE_TEXT_ROLE, base)
        base = item.data(BASE_TEXT_ROLE) or Path(key).name
        status = self.doc_status.get(key)
        if not status and key in self.interrupted:
            status = f"interrompida em {self.interrupted[key].fraction:.0%}"
        item.setText(f"{base} · {status}" if status else base)

    def _set_status(self, key: str, status: str | None) -> None:
        if status:
            self.doc_status[key] = status
        else:
            self.doc_status.pop(key, None)
        self._set_item_text(key)

    def _show_selected_document(self) -> None:
        self._refresh_voices_if_language_changed()
        key = self._current_key()
        if key is None:
            self.preview.set_document(None)
        elif key in self.loaded:
            self.preview.set_document(self.loaded[key])
        elif key in self.load_errors:
            self.preview.set_document(None, self.load_errors[key])
        else:
            self.preview.set_document(None, "Lendo documento…")
            self._load(key)

    def _load(self, key: str) -> None:
        if key in self.loading:
            return
        self.loading.add(key)
        log.info("Lendo %s…", Path(key).name)
        # Copia das configuracoes: a leitura roda em outra thread.
        worker = Worker(load_document, Path(key), replace(self.settings), with_progress=True)
        worker.signals.progress.connect(lambda message, k=key: self._on_load_progress(k, message))
        self._run(
            worker,
            lambda document, k=key: self._on_loaded(k, document),
            lambda message, k=key: self._on_load_failed(k, message),
        )

    def _on_load_progress(self, key: str, message: str) -> None:
        self._set_item_text(key, f"{Path(key).name} — {message.split(':')[-1].strip()}")
        if self._current_key() == key:
            self.preview.set_document(None, message + "…")

    def _on_loaded(self, key: str, document: Document) -> None:
        self.loading.discard(key)
        item = self._item_for(key)
        if item is None:
            return  # removido durante a leitura
        self.loaded[key] = document
        count = len(document.chapters)
        self._set_item_text(key, f"{document.source.name} — {count} capítulo{'s' if count != 1 else ''}")
        if self._current_key() == key:
            self.preview.set_document(document)
            self._refresh_voices_if_language_changed()

    def _on_load_failed(self, key: str, message: str) -> None:
        self.loading.discard(key)
        item = self._item_for(key)
        if item is None:
            return
        self.load_errors[key] = message
        self._set_item_text(key, f"{Path(key).name} — erro na leitura")
        if self._current_key() == key:
            self.preview.set_document(None, message)

    def reload_current_document(self) -> None:
        key = self._current_key()
        if not key:
            return
        if key in self.edited:
            answer = QMessageBox.question(
                self,
                "Recarregar documento",
                "As edições feitas na prévia deste documento serão descartadas. Continuar?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._forget(key)
        self.preview.set_document(None, "Lendo documento…")
        self._load(key)

    def _on_preview_edited(self) -> None:
        key = self._current_key()
        if key:
            self.edited.add(key)

    def _update_doc_buttons(self) -> None:
        count = self.doc_list.count()
        self.remove_button.setEnabled(bool(self.doc_list.selectedItems()))
        self.clear_button.setEnabled(count > 0)
        self.doc_count.setText("1 documento" if count == 1 else f"{count} documentos")

    # ---- voz --------------------------------------------------------------

    def active_language(self) -> str:
        """Idioma da lista de vozes: o escolhido ou, no automatico, o do documento selecionado."""
        if self.settings.document_language in LANGUAGES:
            return self.settings.document_language
        document = self.loaded.get(self._current_key() or "")
        return document.language if document else DEFAULT_LANGUAGE

    def current_voice(self) -> str:
        return self.settings.voice_for(self.active_language())

    def _set_voice_for(self, language: str, voice_id: str) -> None:
        if language == DEFAULT_LANGUAGE:
            self.settings.voice = voice_id
        else:
            self.settings.voices_by_language = {**self.settings.voices_by_language, language: voice_id}
        self._save_settings()

    def _load_voices(self) -> None:
        """Lista so as vozes do idioma ativo; cada idioma lembra a sua voz."""
        language = self.active_language()
        self._voices_language = language
        self.language_combo.setItemText(0, f"Automático ({LANGUAGES[language].lower()})")
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        voices = [v for v in self.provider.list_voices() if language_of_voice(v.language) == language]
        for voice in voices:
            self.voice_combo.addItem(voice.display_name, voice.id)
        wanted = self.settings.voice_for(language)
        index = self.voice_combo.findData(wanted)
        if index < 0 and voices:
            log.warning("Voz '%s' não encontrada para %s; usando %s.", wanted, LANGUAGES[language].lower(), voices[0].id)
            index = 0
            self._set_voice_for(language, voices[0].id)
        self.voice_combo.setCurrentIndex(index)
        self.voice_combo.blockSignals(False)
        has_voice = bool(voices)
        self.voice_combo.setEnabled(has_voice)
        self.sample_button.setEnabled(has_voice)
        if not has_voice:
            self.voice_combo.addItem(f"Nenhuma voz em {LANGUAGES[language].lower()} (Ferramentas > Idiomas e vozes)")
        self._update_engine_status()

    def _refresh_voices_if_language_changed(self) -> None:
        if self.active_language() != getattr(self, "_voices_language", None):
            self._load_voices()

    def _on_voice_changed(self, index: int) -> None:
        voice_id = self.voice_combo.itemData(index)
        language = self.active_language()
        if voice_id and voice_id != self.settings.voice_for(language):
            self._set_voice_for(language, voice_id)
            log.info("Voz para %s: %s", LANGUAGES[language].lower(), voice_id)
        self._update_engine_status()

    def _on_language_changed(self, index: int) -> None:
        choice = self.language_combo.itemData(index)
        self._set_setting("document_language", choice)
        # Documentos ja lidos passam a usar o idioma escolhido (ou voltam ao detectado).
        for document in self.loaded.values():
            if document.translated_from:
                continue  # traducao: o idioma e o de destino
            if choice in LANGUAGES:
                document.language = choice
            else:
                text = " ".join(b.text for c in document.chapters for b in c.blocks if b.type != BlockType.CODE)
                document.language = detect_language(text)
        log.info("Idioma dos documentos: %s", LANGUAGES.get(choice, "automático").lower())
        self.preview.refresh()
        self._load_voices()

    def _show_speed(self, speed: float) -> None:
        self.speed_value.setText(f"{speed:.2f}×".replace(".", ","))

    def _on_speed_changed(self, value: int) -> None:
        self.settings.speed = value / 100
        self._show_speed(self.settings.speed)
        self.preview.set_speed(self.settings.speed)
        self._save_settings()

    def _on_reading_rule_changed(self, field_name: str, enabled: bool, label: str = "") -> None:
        self.preview.commit_edits()
        setattr(self.settings, field_name, enabled)
        self._save_settings()
        for document in self.loaded.values():
            apply_rules(document, self.settings)
        self.preview.refresh()
        log.info("%s: %s", label or field_name, "sim" if enabled else "não")

    def _on_study_changed(self, name: str, value) -> None:
        self._set_setting(name, value)
        self._apply_study_to_preview()
        if name == "study_mode":
            log.info("Modo questões: %s", "sim" if value else "não")

    def _apply_study_to_preview(self) -> None:
        self.preview.study_think_ms = self.settings.think_pause_s * 1000 if self.settings.study_mode else None
        if self.preview.spoken_view.isChecked():
            self.preview.refresh()

    def _on_ocr_toggled(self, enabled: bool) -> None:
        self._set_setting("ocr_enabled", enabled)
        self.check_dependencies(show_dialog=False)
        log.info("OCR de páginas digitalizadas: %s (recarregue o documento para aplicar).", "sim" if enabled else "não")

    def _on_max_chapter_changed(self, thousands: int) -> None:
        self.settings.max_chapter_chars = thousands * 1000
        self._save_settings()

    def _update_engine_status(self) -> None:
        mode = "offline" if self.provider.offline else "online"
        voice = self.voice_combo.currentText() if self.voice_combo.isEnabled() else "sem voz"
        language = LANGUAGES[self.active_language()].lower()
        self.engine_label.setText(f"Voz ativa ({language}): {voice} · {mode}")

    def play_sample(self) -> None:
        self.sample_button.setEnabled(False)
        self.sample_button.setText("Gerando amostra…")
        log.info("Gerando amostra com %s a %.2fx.", self.current_voice(), self.settings.speed)
        self._synthesize_and_play(phrase("sample", self.active_language()), self._reset_sample_button)

    def _synthesize_and_play(self, text: str, reset) -> None:
        """Gera `text` com a voz e a velocidade atuais e toca; `reset()` ao terminar ou falhar."""
        audio_player.stop()
        temp = paths.temp_dir()
        for old in temp.glob("amostra_*.wav"):
            old.unlink(missing_ok=True)
        out = temp / f"amostra_{next(self._sample_counter)}.wav"
        worker = Worker(self.provider.synthesize_to_wav, text, self.current_voice(), self.settings.speed, out)

        def ready(path: Path) -> None:
            reset()
            audio_player.play_wav(path)

        def failed(message: str) -> None:
            reset()
            QMessageBox.warning(self, "Falha ao gerar áudio", message)

        self._run(worker, ready, failed)

    def _listen_excerpt(self, text: str, normalized: bool) -> None:
        if not normalized:
            language = self.active_language()
            text = normalize(text, load_default(language), language)
        self.preview.set_listening(True)
        log.info("Ouvindo trecho da prévia (%d caracteres).", len(text))
        self._synthesize_and_play(text, lambda: self.preview.set_listening(False))

    def _fix_pronunciation(self, term: str) -> bool:
        current = normalize(term, load_default())
        dialog = PronunciationDialog(
            term, current, lambda spoken: self._synthesize_and_play(normalize(spoken), lambda: None), self
        )
        if not dialog.exec():
            return False
        save_user_entry(*dialog.entry())
        self.preview.dictionary_changed()
        self.statusBar().showMessage(
            f"Pronúncia de “{dialog.entry()[0]}” salva; vale nas próximas conversões.", 8000
        )
        return True

    def open_pronunciation_review(self) -> None:
        document = self.preview.document
        if document is None:
            QMessageBox.information(self, "Revisar pronúncia", "Selecione um documento já carregado.")
            return
        self.preview.commit_edits()
        found = review.candidates(document, load_default())
        if not found:
            QMessageBox.information(
                self, "Revisar pronúncia", "Nenhum termo pendente: siglas e termos técnicos já estão revisados."
            )
            return
        dialog = ReviewDialog(
            document.title,
            found,
            on_listen=lambda spoken: self._synthesize_and_play(spoken, lambda: None),
            on_fix=self._fix_pronunciation,
            recompute=lambda: review.candidates(document, load_default()),
            parent=self,
        )
        dialog.exec()
        audio_player.stop()

    def _run(self, worker: Worker, on_finished, on_failed) -> None:
        self._workers.add(worker)

        def done(handler):
            def slot(value) -> None:
                self._workers.discard(worker)
                handler(value)

            return slot

        worker.signals.finished.connect(done(on_finished))
        worker.signals.failed.connect(done(on_failed))
        self.pool.start(worker)

    def _reset_sample_button(self) -> None:
        self.sample_button.setText("Ouvir amostra")
        self.sample_button.setEnabled(self.voice_combo.isEnabled())


    # ---- saida e configuracoes -------------------------------------------

    def choose_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Pasta de saída", str(self.settings.resolved_output_dir())
        )
        if folder:
            self.settings.output_dir = folder
            self._save_settings()
            self._refresh_output_dir()
            log.info("Pasta de saída: %s", folder)
            self.refresh_interrupted(announce=True)

    def _refresh_output_dir(self) -> None:
        self.output_edit.setText(str(self.settings.resolved_output_dir()))

    def _save_settings(self) -> None:
        try:
            self.settings.save()
        except OSError as exc:
            log.error("Não foi possível salvar as configurações: %s", exc)

    def _open_folder(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # ---- conversao -----------------------------------------------------------

    def start_conversion(self) -> None:
        """Converte a lista; com uma conversao em andamento, poe o que falta no fim da fila."""
        keys = [self.doc_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.doc_list.count())]
        keys = [k for k in keys if k not in self.load_errors]
        if self.conversion:
            queued = set(self.conversion.keys())
            keys = [k for k in keys if k not in queued]
            if not keys:
                QMessageBox.information(
                    self, "Fila de conversão", "Todos os documentos da lista já estão na fila. Adicione outros."
                )
                return
        if not keys:
            QMessageBox.information(self, "Converter", "Adicione ao menos um documento legível.")
            return
        if self.settings.output_format != "wav" and not dependencies.find_ffmpeg(self.settings.ffmpeg_path):
            answer = QMessageBox.question(
                self,
                "FFmpeg não encontrado",
                "O FFmpeg é necessário para gerar MP3, M4A ou M4B e não foi encontrado.\n\n"
                "Deseja converter em WAV desta vez?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.format_combo.setCurrentIndex(self.format_combo.findData("wav"))

        self.preview.commit_edits()
        if not self._confirm_long_conversion(keys):
            return
        self._enqueue([self._queue_item(key, replace(self.settings)) for key in keys])

    def _queue_item(self, key: str, settings: Settings, pick_voice: bool = True) -> QueueItem:
        # Copia: a previa continua editavel; as edicoes valem para a proxima conversao.
        document = copy.deepcopy(self.loaded.get(key))
        if document is not None and pick_voice:
            settings = settings.for_language(document.language)
        return QueueItem(key, document, settings, pick_voice)

    def _enqueue(self, items: list[QueueItem]) -> None:
        """Poe na fila em andamento ou comeca uma conversao nova."""
        if self.conversion:
            added = [item for item in items if self.conversion.add(item)]
            for item in added:
                self._set_status(item.key, "na fila")
            if added:
                log.info("%d documento(s) adicionado(s) à fila de conversão.", len(added))
            if len(added) < len(items):
                # A fila acabou de terminar: o resto comeca uma conversao nova logo depois.
                self._pending_items = getattr(self, "_pending_items", []) + items[len(added):]
            return
        self.conversion = ConversionWorker(items, self.provider)
        self.conversion.signals.progress.connect(self._on_conversion_progress)
        self.conversion.signals.document_started.connect(self._on_document_started)
        self.conversion.signals.document_done.connect(self._on_document_converted)
        self.conversion.signals.document_failed.connect(self._on_document_failed)
        self.conversion.signals.finished.connect(self._on_conversion_finished)
        self.conversion_results: list = []
        self.conversion_errors: list[str] = []
        for item in items:
            self._set_status(item.key, "na fila")
        self._set_converting(True)
        log.info("Conversão iniciada: %d documento(s), formato %s.", len(items), items[0].settings.output_format)
        self.pool.start(self.conversion)

    def _confirm_long_conversion(self, keys: list[str]) -> bool:
        """Avisa quando a voz escolhida vai levar muito tempo (ex.: voz clonada)."""
        characters = sum(
            chapter.char_count()
            for key in keys
            if (document := self.loaded.get(key))
            for chapter in document.included_chapters()
        )
        engine = self.provider.engine_name(self.current_voice())
        seconds = estimated_generation_seconds(characters, engine)
        if seconds < LONG_CONVERSION_SECONDS:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Conversão demorada")
        box.setText(
            f"Com a voz {engine.lower()}, gerar este áudio deve levar cerca de {format_duration(seconds)}."
        )
        box.setInformativeText(
            "A conversão roda em segundo plano: você pode continuar usando o DocAudio e será avisado ao terminar. "
            "Se cancelar ou fechar, o que já foi gerado é reaproveitado no botão Retomar. "
            "Vozes Piper ou Kokoro fazem o mesmo em poucos minutos."
        )
        start = box.addButton("Converter assim mesmo", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Voltar", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is start

    def cancel_conversion(self) -> None:
        if self.conversion:
            self.conversion.cancel.set()
            self.cancel_button.setEnabled(False)
            self.progress_label.setText("Cancelando… o que já foi gerado será reaproveitado.")

    def _set_converting(self, active: bool) -> None:
        # Cada documento da fila leva uma copia das configuracoes: o resto da janela
        # continua livre para preparar os proximos documentos.
        self.convert_button.setText("Adicionar à fila" if active else "Converter documentos")
        self.convert_button.setToolTip(
            "Põe no fim da fila os documentos da lista que ainda não estão nela, com a voz e as opções atuais."
            if active else ""
        )
        self.cancel_button.setEnabled(active)
        self.clone_allowed_action.setEnabled(not active)
        self._refresh_resume_button()
        if active:
            self.progress_bar.setValue(0)
            self.progress_label.setText("Preparando…")
            # Referencia para o tempo restante: primeira atualizacao de progresso.
            self._progress_start: tuple[float, float] | None = None

    def _on_conversion_progress(self, index: int, total: int, progress) -> None:
        overall = (index + progress.fraction) / total
        self.progress_bar.setValue(int(overall * 1000))
        prefix = f"Documento {index + 1} de {total} · " if total > 1 else ""
        chapter = f"capítulo {progress.chapter_index} de {progress.chapter_count} · " if progress.chapter_count else ""
        self.progress_label.setText(f"{prefix}{chapter}{progress.message} ({overall:.0%}{self._remaining(overall)})")
        if self.conversion and 0 <= index < len(keys := self.conversion.keys()):
            self._set_status(keys[index], f"convertendo {progress.fraction:.0%}")

    def _remaining(self, overall: float) -> str:
        """Tempo restante pelo ritmo medido desde o inicio (trechos em cache contam como feitos)."""
        now = time.monotonic()
        if self._progress_start is None:
            self._progress_start = (now, overall)
            return ""
        started, base = self._progress_start
        done, elapsed = overall - base, now - started
        if done < 0.01 or elapsed < 15:
            return ""
        return f" · faltam ≈ {format_duration(elapsed / done * (1 - overall))}"

    def _on_document_started(self, key: str) -> None:
        self.interrupted.pop(key, None)
        self._set_status(key, "convertendo")
        # O tempo restante recomeca a cada documento (vozes e tamanhos podem mudar).
        self._progress_start = None

    def _on_document_converted(self, key: str, result) -> None:
        self.conversion_results.append(result)
        self._set_status(key, "concluído")

    def _on_document_failed(self, key: str, message: str) -> None:
        self.conversion_errors.append(f"{Path(key).name}: {message}")
        self._set_status(key, "falhou")

    def _on_conversion_finished(self, cancelled: bool) -> None:
        self.conversion = None
        for key, status in list(self.doc_status.items()):
            if status == "na fila" or status.startswith("convertendo"):
                self._set_status(key, None)
        self._set_converting(False)
        self.refresh_interrupted()
        files = sum(len(r.files) for r in self.conversion_results)
        if cancelled:
            self.progress_label.setText("Conversão cancelada. Use Retomar para continuar de onde parou.")
            return
        if pending := getattr(self, "_pending_items", []):
            # Pedidos que chegaram no instante em que a fila terminou.
            self._pending_items = []
            self._enqueue(pending)
            return
        self.progress_bar.setValue(1000 if not self.conversion_errors else self.progress_bar.value())
        self.progress_label.setText(f"Concluído: {files} arquivo(s) gerado(s).")
        self._notify_finished(files)
        if self.conversion_errors:
            QMessageBox.warning(self, "Conversão com erros", "\n\n".join(self.conversion_errors))
            return
        if self.conversion_results:
            box = QMessageBox(self)
            box.setWindowTitle("Conversão concluída")
            box.setText(f"{files} arquivo(s) de áudio gerado(s).")
            listen_button = box.addButton("Ouvir agora", QMessageBox.ButtonRole.AcceptRole)
            open_button = box.addButton("Abrir pasta", QMessageBox.ButtonRole.ActionRole)
            box.addButton("Fechar", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is open_button:
                self._open_folder(self.conversion_results[0].folder)
            elif box.clickedButton() is listen_button:
                self.open_player(self.conversion_results[0].folder)

    def choose_audiobook(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Escolha a pasta do audiolivro (dentro da pasta de saída)", str(self.settings.resolved_output_dir())
        )
        if folder:
            self.open_player(Path(folder))

    def open_player(self, folder: Path) -> None:
        self.player_panel.pause()
        if self.player_dialog is not None:
            self.player_dialog.close()
        try:
            self.player_dialog = PlayerDialog(folder, dependencies.find_ffmpeg(self.settings.ffmpeg_path), self)
        except PlaybackError as exc:
            QMessageBox.information(self, "Ouvir audiolivro", str(exc))
            return
        self.player_dialog.finished.connect(lambda *_: setattr(self, "player_dialog", None))
        self.player_dialog.show()

    def _notify_finished(self, files: int) -> None:
        """Aviso do Windows quando a janela nao esta em primeiro plano."""
        if self.isActiveWindow():
            return
        QApplication.alert(self)
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        if self.tray is None:
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            self.tray.setToolTip(APP_NAME)
            self.tray.messageClicked.connect(self._bring_to_front)
            self.tray.activated.connect(lambda *_: self._bring_to_front())
        self.tray.show()
        if self.conversion_errors:
            title, text = "Conversão terminou com erros", f"{len(self.conversion_errors)} documento(s) falharam."
        else:
            title, text = "Conversão concluída", f"{files} arquivo(s) de áudio gerado(s)."
        self.tray.showMessage(title, text, QSystemTrayIcon.MessageIcon.Information, 15000)

    def _bring_to_front(self) -> None:
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()
        if self.tray:
            self.tray.hide()

    # ---- conversoes interrompidas ---------------------------------------------

    def refresh_interrupted(self, announce: bool = False) -> None:
        """Procura na pasta de saida conversoes que pararam no meio."""
        # Do mais antigo ao mais recente: com duas conversoes do mesmo arquivo, vale a ultima.
        found = reversed(find_interrupted(self.settings.resolved_output_dir()))
        self.interrupted = {str(item.document): item for item in found}
        for key in [self.doc_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.doc_list.count())]:
            self._set_item_text(key)
        self._refresh_resume_button()
        if announce:
            for item in self.interrupted.values():
                log.info(
                    "Conversão interrompida: %s (%.0f%% pronto, voz %s). Use Retomar para continuar.",
                    item.document.name, item.fraction * 100, self._voice_label(item.voice),
                )

    def _resumable(self) -> list[Interrupted]:
        queued = set(self.conversion.keys()) if self.conversion else set()
        return [item for key, item in self.interrupted.items() if key not in queued]

    def _refresh_resume_button(self) -> None:
        items = self._resumable()
        self.resume_button.setVisible(bool(items))
        if not items:
            return
        self.resume_button.setText(f"Retomar ({len(items)})" if len(items) > 1 else "Retomar")
        self.resume_button.setToolTip(
            "Continua de onde parou, com a mesma voz e velocidade:\n"
            + "\n".join(f"• {i.document.name}: {i.fraction:.0%} pronto, voz {self._voice_label(i.voice)}" for i in items)
        )

    def _voice_label(self, voice_id: str) -> str:
        voice = self.provider.find_voice(voice_id)
        return voice.label if voice else voice_id

    def resume_interrupted(self) -> None:
        items = self._resumable()
        if not items:
            return
        missing = [i for i in items if self.provider.find_voice(i.voice) is None]
        if missing:
            QMessageBox.warning(
                self,
                "Retomar",
                "A voz usada não está disponível nesta máquina; essas conversões não podem continuar:\n\n"
                + "\n".join(f"• {i.document.name} (voz {i.voice})" for i in missing),
            )
            items = [i for i in items if i not in missing]
            if not items:
                return
        self.add_documents([i.document for i in items])
        queue = []
        for interrupted in items:
            queue.append(self._resume_item(interrupted))
            log.info("Retomando %s (%.0f%% pronto).", interrupted.document.name, interrupted.fraction * 100)
        self._enqueue(queue)
        self._refresh_resume_button()

    def _resume_item(self, interrupted: Interrupted) -> QueueItem:
        """Mesma voz, velocidade, formato e texto da conversao original: os trechos prontos sao reaproveitados."""
        key = str(interrupted.document)
        settings = self.settings
        if interrupted.language:
            # Idioma de leitura do arquivo original, e a voz de cada idioma para o modo bilingue.
            settings = replace(
                settings.for_language(interrupted.language),
                document_language=interrupted.translated_from or interrupted.language,
            )
        settings = replace(
            settings, voice=interrupted.voice, speed=interrupted.speed, output_format=interrupted.format
        )
        item = self._queue_item(key, settings, pick_voice=False)
        current = item.document
        state = (interrupted.language, interrupted.translated_from, interrupted.bilingual)
        # Manifesto antigo nao tem idioma: segue com o documento aberto, como antes.
        if interrupted.language and (
            current is None or (current.language, current.translated_from, current.bilingual) != state
        ):
            item.document = None
            item.translate_to = interrupted.language if interrupted.translated_from else ""
            item.bilingual = interrupted.bilingual
        return item

    # ---- trava da clonagem ----------------------------------------------------

    def _init_clone_support(self) -> None:
        """Aplica a trava e, se nunca verificou esta maquina, testa a placa em segundo plano."""
        clone_provider.set_enabled(self.settings.clone_enabled)
        python = clone_provider.clone_python()
        if self.settings.clone_enabled and python and clone_provider.cached_capability(python) is None:
            log.info("Verificando suporte à clonagem de voz nesta máquina…")
            self._run(
                Worker(clone_provider.check_support, probe=True),
                lambda _support: self._refresh_clone_state(reload_voices=True),
                lambda message: log.warning("Verificação da clonagem falhou: %s", message),
            )
        self._refresh_clone_state()

    def _refresh_clone_state(self, reload_voices: bool = False) -> None:
        support = clone_provider.check_support()
        self.clone_action.setEnabled(support.available)
        self.clone_action.setToolTip(support.reason)
        self.clone_action.setText("Criar voz &clonada…" if support.available else "Criar voz clonada (indisponível)")
        if reload_voices:
            self._load_voices()
            self._update_engine_status()

    def _on_clone_allowed_toggled(self, allowed: bool) -> None:
        self._set_setting("clone_enabled", allowed)
        clone_provider.set_enabled(allowed)
        if not allowed:
            clone_provider.shutdown()
        log.info("Clonagem de voz nesta máquina: %s", "permitida" if allowed else "desativada")
        self._init_clone_support()
        self._refresh_clone_state(reload_voices=True)

    def open_clone_dialog(self) -> None:
        from docaudio.ui.clone_dialog import CloneVoiceDialog

        support = clone_provider.check_support()
        if not support.available:
            QMessageBox.information(self, "Clonagem de voz", support.reason)
            return
        dialog = CloneVoiceDialog(self.provider, self)
        dialog.exec()
        if dialog.created:
            self.settings.voice = dialog.created.id
            self._save_settings()
            self._load_voices()
            self._update_engine_status()
            log.info("Voz clonada selecionada: %s", dialog.created.label)

    def _on_theme_toggled(self, name: str, enabled: bool) -> None:
        themes = [n for n in pronunciation.THEMES if (n == name and enabled) or (n != name and n in self.settings.pronunciation_themes)]
        self._set_setting("pronunciation_themes", themes)
        pronunciation.set_themes(themes)
        self.preview.dictionary_changed()
        log.info("Dicionário temático %s: %s", pronunciation.THEMES[name], "ligado" if enabled else "desligado")

    # ---- traducao ------------------------------------------------------------------

    def translate_current(self, target: str, bilingual: bool) -> None:
        key = self._current_key()
        if not key or key not in self.loaded:
            return
        self.preview.commit_edits()
        original = self.originals.get(key) or self.loaded[key]
        source = original.language
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
        self.preview.set_document(None, f"Traduzindo para {LANGUAGES[target].lower()}…")
        self._set_status(key, "traduzindo")
        worker = Worker(translation.translate_document, original, target, bilingual=bilingual, with_progress=True)
        worker.signals.progress.connect(lambda message, k=key: self._on_translate_progress(k, message))
        self._run(
            worker,
            lambda document, k=key, o=original: self._on_translated(k, o, document),
            lambda message, k=key: self._on_translate_failed(k, message),
        )

    def _on_translate_progress(self, key: str, message: str) -> None:
        if self._current_key() == key:
            self.preview.set_document(None, message)

    def _on_translated(self, key: str, original: Document, document: Document) -> None:
        if key not in self.loaded:
            return  # removido durante a traducao
        self.originals[key] = original
        self.loaded[key] = document
        self._set_status(key, f"traduzido p/ {LANGUAGES[document.language].lower()}")
        if self._current_key() == key:
            self.preview.set_document(document)
        self._load_voices()

    def _on_translate_failed(self, key: str, message: str) -> None:
        self._set_status(key, None)
        if self._current_key() == key:
            self.preview.set_document(self.loaded.get(key))
        QMessageBox.warning(self, "Traduzir", message)

    def restore_original(self) -> None:
        key = self._current_key()
        if key in self.originals:
            self.loaded[key] = self.originals.pop(key)
            self._set_status(key, None)
            self.preview.set_document(self.loaded[key])
            self._load_voices()
            log.info("%s: de volta ao original.", Path(key).name)

    def save_translation(self) -> None:
        """Guarda em arquivo o documento traduzido, com as correcoes feitas na previa."""
        key = self._current_key()
        document = self.loaded.get(key) if key else None
        if document is None or not document.translated_from:
            return
        self.preview.commit_edits()
        name = f"{safe_filename(document.title or document.source.stem)} ({document.language}).docx"
        chosen, selected = QFileDialog.getSaveFileName(
            self, "Salvar tradução", str(self.settings.resolved_output_dir() / name), SAVE_FILTER
        )
        if chosen:
            self._write_translation(document, Path(chosen), selected)

    def _write_translation(self, document: Document, path: Path, selected: str = "") -> None:
        if path.suffix.lower() not in (".docx", ".txt"):
            path = path.with_name(path.name + (".txt" if "txt" in selected else ".docx"))
        try:
            text_export.save_text(path, document.title, text_export.document_sections(document))
        except OSError as exc:
            QMessageBox.warning(self, "Salvar tradução", f"Não foi possível salvar {path.name}: {exc}")
            return
        log.info("Tradução salva em %s", path)

    def open_text_translator(self) -> None:
        if self.text_translator is None:
            self.text_translator = TranslateDialog(
                self.settings.resolved_output_dir, self.open_languages, lambda path: self.add_documents([path]), self
            )
        self.text_translator.show()
        self.text_translator.raise_()
        self.text_translator.activateWindow()

    def open_languages(self) -> None:
        def changed() -> None:
            self._load_voices()
            self.check_dependencies(show_dialog=False)

        LanguagesDialog(self.provider, self.settings, changed, self).exec()

    def import_dictionary(self) -> None:
        start = self.settings.last_open_dir or str(Path.home())
        file, _ = QFileDialog.getOpenFileName(
            self, "Importar dicionário de pronúncia", start,
            "Dicionários (*.dic *.pls *.xml *.csv *.tsv *.txt);;NVDA (*.dic);;PLS (*.pls *.xml);;Planilha (*.csv *.tsv *.txt)",
        )
        if not file:
            return
        try:
            result = dictionary_import.parse_file(Path(file))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Importar dicionário", str(exc))
            return
        if not result.entries:
            reasons = "; ".join(why for _, why in result.skipped[:3])
            QMessageBox.information(
                self, "Importar dicionário",
                "Nenhuma pronúncia aproveitável neste arquivo." + (f" ({reasons})" if reasons else ""),
            )
            return
        dialog = ImportDialog(Path(file), result, dictionary_import.plan_import(result.entries), self)
        if not dialog.exec():
            return
        entries = dialog.entries_to_save()
        if entries:
            save_user_entries(entries)
            self.preview.dictionary_changed()
        log.info("Dicionário importado de %s: %d pronúncia(s) gravada(s).", Path(file).name, len(entries))
        self.statusBar().showMessage(f"{len(entries)} pronúncia(s) importada(s) para o seu dicionário.", 8000)

    def edit_dictionary(self) -> None:
        path = ensure_user_dictionary()
        log.info("Dicionário de pronúncia do usuário: %s", path)
        QMessageBox.information(
            self,
            "Dicionário de pronúncia",
            "O dicionário será aberto no editor padrão de CSV.\n\n"
            "Colunas: termo, pronuncia, diferenciar_maiusculas (sim/não).\n"
            "Exemplo: UFRJ,u éfe érre jota,sim\n\n"
            "As mudanças valem na próxima conversão e ao marcar “Ver como será falado”.",
        )
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # ---- dependencias e encerramento -------------------------------------

    def check_dependencies(self, show_dialog: bool) -> None:
        statuses = dependencies.check_all(self.settings, self.provider)
        problems = [s for s in statuses if not s.ok]
        for status in statuses:
            level = logging.INFO if status.ok else (logging.ERROR if status.required else logging.WARNING)
            log.log(level, "Dependência %s: %s", status.name, status.detail)

        if problems:
            blocking = any(s.required for s in problems)
            lines = [f"{s.name}: {s.detail}. {s.hint}".strip() for s in problems]
            self.banner.setText("\n".join(lines))
            theme.set_role(self.banner, "error" if blocking else "warning")
        self.banner.setVisible(bool(problems))

        if show_dialog:
            text = "\n".join(
                f"{'✔' if s.ok else '✖'} {s.name}: {s.detail}" + (f"\n    {s.hint}" if s.hint and not s.ok else "")
                for s in statuses
            )
            QMessageBox.information(self, "Dependências", text)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            f"Sobre o {APP_NAME}",
            f"{APP_NAME} {__version__}\n\nConverte documentos (DOCX, PDF, EPUB, PPTX, TXT e HTML) em áudio "
            "em português brasileiro, com voz offline (Piper).",
        )

    def closeEvent(self, event) -> None:
        if self.conversion:
            answer = QMessageBox.question(
                self,
                "Conversão em andamento",
                "Interromper a conversão e sair? Na próxima vez, use Retomar para continuar de onde parou.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.conversion.cancel.set()
        self.preview.commit_edits()
        self.player_panel.shutdown()
        audio_player.stop()
        if self.player_dialog is not None:
            self.player_dialog.close()
        if self.tray:
            self.tray.hide()
        self.pool.waitForDone(5000)
        self.log_panel.detach()
        super().closeEvent(event)
