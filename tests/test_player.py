"""Tempos dos trechos no manifesto e leitura do audiolivro pelo player."""

import os

import pytest

from docaudio.audio.playback import Player, read_wav
from docaudio.core.dependencies import find_ffmpeg
from docaudio.storage.manifest import Manifest
from test_conversion import RATE, FakeProvider, job, make_document, settings_for

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_manifesto_guarda_inicio_de_cada_trecho(tmp_path):
    document = make_document(tmp_path)
    settings = settings_for(tmp_path, sentence_pause_ms=0, paragraph_pause_ms=200)
    result = job(document, settings, FakeProvider()).run()
    manifest = Manifest.load(result.folder / ".docaudio" / "manifest.json")
    first = manifest.chapters[0]
    starts = [start for start, _ in first.timings]
    assert len(first.timings) == first.segments
    assert starts[0] == 0 and starts == sorted(starts)
    # Cada trecho falso tem 0,1 s de fala mais a pausa depois dele.
    assert starts[1] == pytest.approx(0.1 + 1.3 * 0.2, abs=0.01)
    assert first.timings[0][1] == "Introdução."

    # Capitulo reaproveitado numa nova conversao mantem os tempos.
    again = job(document, settings, FakeProvider()).run()
    assert again.reused_chapters == 2
    assert Manifest.load(result.folder / ".docaudio" / "manifest.json").chapters[0].timings == first.timings


@pytest.mark.skipif(find_ffmpeg() is None, reason="FFmpeg não instalado")
def test_m4b_guarda_inicio_de_cada_capitulo_e_o_player_separa_os_capitulos(tmp_path):
    from PySide6.QtWidgets import QApplication

    from docaudio.ui.player import load_tracks

    QApplication.instance() or QApplication([])
    document = make_document(tmp_path)
    settings = settings_for(tmp_path)
    settings.output_format = "m4b"
    result = job(document, settings, FakeProvider()).run()
    title, tracks = load_tracks(result.folder)
    assert title == "Apostila"
    assert [t.title for t in tracks] == ["Introdução", "Capítulo: Prática"]
    assert tracks[0].start_s == 0 and tracks[1].start_s > 0
    assert tracks[0].duration_s == pytest.approx(tracks[1].start_s)


def test_player_posicao_e_busca(tmp_path):
    import numpy as np

    player = Player()
    player.load(np.zeros((RATE * 2, 1), dtype=np.int16), RATE)
    assert player.duration == pytest.approx(2.0)
    player.seek(1.5)
    assert player.position == pytest.approx(1.5)
    player.seek(10)
    assert player.position == pytest.approx(2.0)


def test_le_wav_mono_16_bits(tmp_path):
    import wave

    path = tmp_path / "a.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(b"\x01\x00" * RATE)
    samples, rate = read_wav(path)
    assert rate == RATE and samples.shape == (RATE, 1)


def test_capa_desenhada_quando_o_pdf_nao_serve(tmp_path):
    import pymupdf

    from docaudio.audio.cover import SIZE, make_cover
    from docaudio.core.model import Document, DocumentKind

    missing_pdf = Document(tmp_path / "sumiu.pdf", DocumentKind.PDF, "Edital de abertura", author="Banca")
    cover = make_cover(missing_pdf, tmp_path / "capa.jpg")
    assert cover is not None and cover.is_file()
    image = pymupdf.Pixmap(str(cover))
    assert (image.width, image.height) == (SIZE, SIZE)
