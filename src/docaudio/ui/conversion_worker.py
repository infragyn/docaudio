"""Fila de conversao em segundo plano."""

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from docaudio.audio.ffmpeg import Cancelled
from docaudio.core.conversion import ConversionError, ConversionJob, Progress
from docaudio.core.loader import load_document
from docaudio.core.model import Document
from docaudio.core.translation import TranslationError, translate_document
from docaudio.extractors.base import ExtractionError
from docaudio.speech.base import SpeechProvider
from docaudio.storage.settings import Settings
from docaudio.text_processing.pronunciation import load_default

log = logging.getLogger(__name__)


@dataclass
class QueueItem:
    """Um documento na fila, com as configuracoes do momento em que entrou nela.

    `document` e uma copia (edicoes na previa depois disso valem para a proxima
    conversao) ou None para ler o arquivo na hora.
    """

    key: str
    document: Document | None
    settings: Settings
    # False quando a voz ja foi escolhida (ex.: retomar com a voz da conversao original).
    pick_voice: bool = True
    # Ao retomar uma traducao: traduz de novo depois de ler o arquivo (com o cache, e imediato).
    translate_to: str = ""
    bilingual: bool = False


class ConversionSignals(QObject):
    # posicao do documento na fila, tamanho atual da fila, progresso do documento
    progress = Signal(int, int, object)
    document_started = Signal(str)
    document_done = Signal(str, object)
    document_failed = Signal(str, str)
    finished = Signal(bool)


class ConversionWorker(QRunnable):
    """Converte a fila em ordem; `add` aceita novos documentos enquanto ela anda."""

    def __init__(self, queue: list[QueueItem], provider: SpeechProvider) -> None:
        super().__init__()
        self.queue = list(queue)
        self.provider = provider
        self.cancel = threading.Event()
        self.signals = ConversionSignals()
        self._lock = threading.Lock()
        self._closed = False
        self.setAutoDelete(False)

    def keys(self) -> list[str]:
        with self._lock:
            return [item.key for item in self.queue]

    def add(self, item: QueueItem) -> bool:
        """Poe no fim da fila; False se a fila ja terminou (comece outra conversao)."""
        with self._lock:
            if self._closed:
                return False
            self.queue.append(item)
            return True

    def _next(self, index: int) -> tuple[QueueItem | None, int]:
        with self._lock:
            if index < len(self.queue) and not self.cancel.is_set():
                return self.queue[index], len(self.queue)
            # Fecha sob a mesma trava: um `add` depois disso devolve False.
            self._closed = True
            return None, len(self.queue)

    def run(self) -> None:
        dictionary = load_default()
        index = 0
        cancelled = False
        while True:
            item, _ = self._next(index)
            if item is None:
                cancelled = self.cancel.is_set()
                break
            self.signals.document_started.emit(item.key)
            try:
                document = item.document or load_document(Path(item.key), item.settings)
                if item.translate_to:
                    document = translate_document(document, item.translate_to, bilingual=item.bilingual)
                settings = item.settings
                if item.document is None and item.pick_voice:
                    # Lido agora: so aqui se sabe o idioma, e com ele a voz.
                    settings = settings.for_language(document.language)

                def report(progress: Progress, index=index) -> None:
                    self.signals.progress.emit(index, len(self.queue), progress)

                job = ConversionJob(document, settings, self.provider, dictionary, report, self.cancel)
                self.signals.document_done.emit(item.key, job.run())
            except Cancelled:
                cancelled = True
                with self._lock:
                    self._closed = True
                break
            except (ExtractionError, ConversionError, TranslationError) as exc:
                log.error("%s", exc)
                self.signals.document_failed.emit(item.key, str(exc))
            except Exception as exc:
                log.exception("Erro inesperado ao converter %s", item.key)
                self.signals.document_failed.emit(item.key, f"Erro inesperado: {exc}")
            index += 1
        self.signals.finished.emit(cancelled)
