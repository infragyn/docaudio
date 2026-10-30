"""Idiomas e vozes: o que ja vem pronto em cada idioma e o que pode ser baixado."""

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from docaudio.core import paths, translation
from docaudio.core.dependencies import find_tessdata
from docaudio.core.language import LANGUAGES, language_of_voice
from docaudio.speech import voice_download
from docaudio.storage.settings import Settings
from docaudio.ui import theme
from docaudio.ui.workers import Worker

log = logging.getLogger(__name__)

ENGINE_NOTES = {
    "Kokoro": "incluídas no modelo Kokoro",
    "Windows": "instaladas no Windows",
    "Piper": "Piper",
    "Clonada": "sua voz clonada",
}


class LanguagesDialog(QDialog):
    """`on_changed()` e chamado depois de cada download (para recarregar a lista de vozes)."""

    def __init__(self, provider, settings: Settings, on_changed: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.provider = provider
        self.settings = settings
        self.on_changed = on_changed
        self.pool = QThreadPool.globalInstance()
        self._workers: set[Worker] = set()
        self.setWindowTitle("Idiomas e vozes")
        self.resize(720, 640)
        body = theme.dialog_body(
            self,
            "Idiomas e vozes",
            "O DocAudio lê documentos em português, inglês e espanhol. Cada idioma tem vozes prontas; "
            "vozes extras e o OCR de PDFs digitalizados podem ser baixados aqui (uma vez, depois funciona offline).",
            self.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon),
        )
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setVisible(False)
        body.addWidget(self.status)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(0, 0, 4, 0)
        self.content_layout.setSpacing(12)
        scroll.setWidget(content)
        body.addWidget(scroll, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        close = QPushButton("Concluir")
        theme.set_role(close, "primary")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        body.addLayout(footer)
        self._build()

    # ---- conteudo ---------------------------------------------------------------

    def _build(self) -> None:
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        voices = self.provider.list_voices()
        installed = {v.id for v in voices}
        for language, name in LANGUAGES.items():
            card, layout = theme.card(name)
            ready = [v for v in voices if language_of_voice(v.language) == language and "+" not in v.id]
            by_engine: dict[str, int] = {}
            for voice in ready:
                by_engine[voice.engine] = by_engine.get(voice.engine, 0) + 1
            summary = " · ".join(f"{count} {ENGINE_NOTES.get(engine, engine)}" for engine, count in by_engine.items())
            ready_label = QLabel(f"✔ {len(ready)} voz(es) prontas: {summary}" if ready else "Nenhuma voz pronta ainda.")
            theme.set_role(ready_label, "success" if ready else "warning")
            ready_label.setWordWrap(True)
            layout.addWidget(ready_label)

            grid = QGridLayout()
            grid.setHorizontalSpacing(12)
            row = 0
            for voice_id, label, kind, size in voice_download.PIPER_CATALOG.get(language, []):
                grid.addWidget(QLabel(f"Voz Piper {label}"), row, 0)
                detail = QLabel(f"{kind} · ~{size} MB")
                theme.set_role(detail, "muted")
                grid.addWidget(detail, row, 1)
                grid.addWidget(self._action(voice_id in installed, lambda v=voice_id: self._download_voice(v)), row, 2)
                row += 1
            code = voice_download.TESSERACT_CODES[language]
            has_ocr = find_tessdata(self.settings, code) is not None
            grid.addWidget(QLabel("OCR de PDF digitalizado"), row, 0)
            detail = QLabel(f"Tesseract ({code}) · ~4 MB")
            theme.set_role(detail, "muted")
            grid.addWidget(detail, row, 1)
            grid.addWidget(self._action(has_ocr, lambda lang=language: self._download_ocr(lang)), row, 2)
            grid.setColumnStretch(0, 1)
            layout.addLayout(grid)
            self.content_layout.addWidget(card)

        card, layout = theme.card("Tradução (offline)")
        hint = QLabel(
            "Para ouvir um documento em outro idioma: na prévia, use “Traduzir”. "
            f"Cada sentido é um modelo de ~{translation.PACKAGE_MB} MB."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        for row, (src, dst) in enumerate(translation.pairs()):
            grid.addWidget(QLabel(f"{LANGUAGES[src]} → {LANGUAGES[dst]}"), row, 0)
            grid.addWidget(
                self._action(translation.is_installed(src, dst), lambda a=src, b=dst: self._download_translation(a, b)),
                row, 1,
            )
        grid.setColumnStretch(0, 1)
        layout.addLayout(grid)
        credit = QLabel(translation.CREDIT)
        credit.setWordWrap(True)
        theme.set_role(credit, "muted")
        layout.addWidget(credit)
        self.content_layout.addWidget(card)
        self.content_layout.addStretch(1)

    def _action(self, installed: bool, download) -> QWidget:
        if installed:
            label = QLabel("Instalado")
            theme.set_role(label, "muted")
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return label
        button = QPushButton("Baixar")
        button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown))
        button.clicked.connect(download)
        return button

    # ---- downloads --------------------------------------------------------------

    def _set_status(self, text: str, role: str) -> None:
        self.status.setText(text)
        theme.set_role(self.status, role)
        self.status.setVisible(True)

    def _run(self, title: str, function, *args) -> None:
        if self._workers:
            self._set_status("Espere o download em andamento terminar.", "warning")
            return
        self._set_status(f"Baixando {title}…", "info")
        worker = Worker(function, *args, with_progress=True)
        worker.signals.progress.connect(lambda message: self._set_status(f"Baixando {title}… {message}", "info"))
        self._workers.add(worker)

        def done(_value) -> None:
            self._workers.discard(worker)
            self._set_status(f"{title[:1].upper()}{title[1:]} instalado.", "success")
            log.info("Instalado: %s", title)
            self.on_changed()
            self._build()

        def failed(message: str) -> None:
            self._workers.discard(worker)
            self._set_status(f"Não foi possível baixar {title}: {message}", "error")

        worker.signals.finished.connect(done)
        worker.signals.failed.connect(failed)
        self.pool.start(worker)

    @staticmethod
    def _progress_adapter(progress):
        def report(name: str, done: int, total: int) -> None:
            progress(f"{done // 1_000_000} de {total // 1_000_000} MB" if total else f"{done // 1_000_000} MB")

        return report

    def _download_voice(self, voice_id: str) -> None:
        self._run(
            f"a voz {voice_id}",
            lambda progress: voice_download.download_voice(voice_id, paths.user_voices_dir(), self._progress_adapter(progress)),
        )

    def _download_translation(self, src: str, dst: str) -> None:
        self._run(
            f"a tradução {LANGUAGES[src].lower()} → {LANGUAGES[dst].lower()}",
            lambda progress: translation.download(src, dst, self._progress_adapter(progress)),
        )

    def _download_ocr(self, language: str) -> None:
        self._run(
            f"o OCR em {LANGUAGES[language].lower()}",
            lambda progress: voice_download.download_tessdata(
                language, paths.data_dir() / "tessdata", self._progress_adapter(progress)
            ),
        )
