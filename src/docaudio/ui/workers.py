"""Execucao de tarefas fora da thread da interface."""

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal

from docaudio.core.translation import TranslationError
from docaudio.extractors.base import ExtractionError
from docaudio.speech.base import SpeechError

log = logging.getLogger(__name__)


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(str)


class Worker(QRunnable):
    """Executa uma funcao no QThreadPool e devolve o resultado por sinais.

    Com `with_progress=True`, a funcao recebe `progress=<callable(str)>` ligado ao
    sinal `progress`.
    """

    def __init__(self, fn: Callable[..., Any], *args: Any, with_progress: bool = False, **kwargs: Any) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        if with_progress:
            self.kwargs["progress"] = self.signals.progress.emit
        # O Python controla o tempo de vida; quem inicia guarda a referencia ate o fim.
        self.setAutoDelete(False)

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except (SpeechError, ExtractionError, TranslationError) as exc:
            log.error("%s", exc)
            self.signals.failed.emit(str(exc))
        except Exception as exc:
            log.exception("Erro inesperado em tarefa de fundo")
            self.signals.failed.emit(f"Erro inesperado: {exc}")
        else:
            self.signals.finished.emit(result)
