"""Fila de conversao e retomada de conversoes interrompidas."""

import threading

import pytest

from docaudio.audio.ffmpeg import Cancelled
from docaudio.core.conversion import find_interrupted
from docaudio.ui.conversion_worker import ConversionWorker, QueueItem
from test_conversion import FakeProvider, job, make_document, settings_for


def test_conversao_cancelada_aparece_para_retomar_e_some_ao_terminar(tmp_path):
    (tmp_path / "apostila.pdf").write_bytes(b"%PDF")  # o documento original precisa existir
    document = make_document(tmp_path)
    settings = settings_for(tmp_path, voice="voz-x", speed=1.3)
    cancel = threading.Event()
    with pytest.raises(Cancelled):
        job(document, settings, FakeProvider(cancel_after=1, cancel=cancel), cancel=cancel).run()

    [item] = find_interrupted(settings.resolved_output_dir())
    assert item.document == document.source
    assert (item.voice, item.speed, item.format) == ("voz-x", 1.3, "wav")
    assert 0 < item.fraction < 1

    # Retomar com as mesmas configuracoes reaproveita o trecho pronto e conclui.
    provider = FakeProvider()
    job(document, settings, provider).run()
    assert len(provider.calls) == item.total_segments - item.done_segments
    assert find_interrupted(settings.resolved_output_dir()) == []


def test_documento_apagado_nao_e_oferecido_para_retomar(tmp_path):
    document = make_document(tmp_path)  # apostila.pdf nao existe no disco
    settings = settings_for(tmp_path)
    cancel = threading.Event()
    with pytest.raises(Cancelled):
        job(document, settings, FakeProvider(cancel_after=1, cancel=cancel), cancel=cancel).run()
    assert find_interrupted(settings.resolved_output_dir()) == []


class _QueueProbe:
    """Coleta os sinais da fila sem depender do laco de eventos do Qt."""

    def __init__(self, worker: ConversionWorker) -> None:
        self.started, self.done, self.failed, self.finished = [], [], [], []
        worker.signals.document_started.connect(self.started.append)
        worker.signals.document_done.connect(lambda key, _result: self.done.append(key))
        worker.signals.document_failed.connect(lambda key, message: self.failed.append(key))
        worker.signals.finished.connect(self.finished.append)


def test_fila_aceita_documentos_enquanto_converte(tmp_path):
    first = make_document(tmp_path)
    second = make_document(tmp_path / "b")
    second.title = "Outra apostila"
    settings = settings_for(tmp_path)
    worker = None

    class AddsWhileRunning(FakeProvider):
        def synthesize_to_wav(self, *args, **kwargs):
            if not self.calls:
                # Chega um documento novo enquanto o primeiro ainda esta sendo narrado.
                assert worker.add(QueueItem("b", second, settings))
            return super().synthesize_to_wav(*args, **kwargs)

    worker = ConversionWorker([QueueItem("a", first, settings)], AddsWhileRunning())
    probe = _QueueProbe(worker)
    worker.run()
    assert probe.started == ["a", "b"]
    assert probe.done == ["a", "b"]
    assert probe.finished == [False]
    # Fila encerrada: novos pedidos precisam de outra conversao.
    assert not worker.add(QueueItem("c", first, settings))


def test_cancelar_encerra_a_fila_e_recusa_novos_documentos(tmp_path):
    settings = settings_for(tmp_path)
    worker = None

    class CancelsAtOnce(FakeProvider):
        def synthesize_to_wav(self, *args, **kwargs):
            worker.cancel.set()
            return super().synthesize_to_wav(*args, **kwargs)

    worker = ConversionWorker(
        [QueueItem("a", make_document(tmp_path), settings), QueueItem("b", make_document(tmp_path / "b"), settings)],
        CancelsAtOnce(),
    )
    probe = _QueueProbe(worker)
    worker.run()
    assert probe.started == ["a"]
    assert probe.finished == [True]
    assert not worker.add(QueueItem("c", make_document(tmp_path), settings))
