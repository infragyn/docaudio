"""Aba Player: formatos, velocidade sem mudar o tom, busca, capitulos e pastas."""

import os
import sys
import threading
import time
import types
import wave

import numpy as np
import pytest

av = pytest.importorskip("av")
from docaudio.audio import stream_player
from docaudio.audio.ffmpeg import encode_m4b
from docaudio.audio.stream_player import RATE, StreamPlayer, is_audio, probe
from docaudio.audio.playback import PlaybackError
from docaudio.core.dependencies import find_ffmpeg
from docaudio.storage.settings import Settings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

TONE_HZ = 440


def tone(seconds=2.0, rate=RATE):
    t = np.arange(int(rate * seconds)) / rate
    return (np.sin(2 * np.pi * TONE_HZ * t) * 12000).astype(np.int16)


def write_wav(path, seconds=2.0, rate=RATE):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(tone(seconds, rate).tobytes())
    return path


def encode(path, codec, seconds=2.0, rate=48000, sample_format="fltp"):
    """Gera um arquivo real com o codec pedido."""
    with av.open(str(path), "w") as container:
        stream = container.add_stream(codec, rate=rate)
        stream.layout = "mono"
        samples = (tone(seconds, rate) / 32768).astype(np.float32).reshape(1, -1)
        frame = av.AudioFrame.from_ndarray(samples, format="fltp", layout="mono")
        frame.sample_rate = rate
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return path


def drain(player, limit=60.0):
    """Faz o papel da placa de som: consome tudo o que o player entrega."""

    class Stop(Exception):
        pass

    player._stop_signal = Stop
    collected = []
    end = time.time() + limit
    while time.time() < end:
        out = np.zeros((4096, 2), dtype=np.int16)
        before = player._played
        try:
            player._callback(out, 4096, None, None)
        except Stop:
            collected.append(out[: player._played - before])
            return np.concatenate(collected)
        collected.append(out[: player._played - before])
        if player._played == before:
            time.sleep(0.005)
    raise AssertionError("o player não terminou")


def peak_hz(samples):
    mono = samples[:, 0].astype(np.float64)
    spectrum = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
    return np.fft.rfftfreq(len(mono), 1 / RATE)[spectrum.argmax()]


@pytest.mark.parametrize(
    ("name", "codec"),
    [("a.mp3", "libmp3lame"), ("a.m4a", "aac"), ("a.m4b", "aac"), ("a.flac", "flac"), ("a.ogg", "libopus"), ("a.opus", "libopus")],
)
def test_toca_cada_formato_inteiro(tmp_path, name, codec):
    path = encode(tmp_path / name, codec)
    player = StreamPlayer()
    player.open(path)
    assert player.duration == pytest.approx(2.0, abs=0.15)
    samples = drain(player)
    assert not player.error
    assert len(samples) / RATE == pytest.approx(2.0, abs=0.15)
    assert peak_hz(samples) == pytest.approx(TONE_HZ, abs=3)
    assert player.finished


def test_decodificadores_de_outros_formatos_estao_embutidos():
    for name in ("vorbis", "wmav2", "wmapro", "alac", "ac3", "ape", "wavpack", "amrnb"):
        assert av.Codec(name, "r")


@pytest.mark.parametrize("speed", [0.5, 1.5, 2.0, 3.0])
def test_velocidade_muda_a_duracao_mas_nao_o_tom(tmp_path, speed):
    player = StreamPlayer()
    player.speed = speed
    player.open(write_wav(tmp_path / "a.wav"))
    samples = drain(player)
    assert len(samples) / RATE == pytest.approx(2.0 / speed, rel=0.05)
    assert peak_hz(samples) == pytest.approx(TONE_HZ, abs=5)
    assert player.position == pytest.approx(2.0, abs=0.1)  # sempre o tempo no arquivo


def test_busca_comeca_do_ponto_pedido(tmp_path):
    player = StreamPlayer()
    player.open(write_wav(tmp_path / "a.wav", seconds=4.0), start=3.0)
    assert len(drain(player)) / RATE == pytest.approx(1.0, abs=0.05)

    player.finished = False
    player.seek(1.0)
    assert player.position == pytest.approx(1.0)
    assert len(drain(player)) / RATE == pytest.approx(3.0, abs=0.05)


def test_mudar_velocidade_continua_do_mesmo_ponto(tmp_path):
    player = StreamPlayer()
    player.open(write_wav(tmp_path / "a.wav", seconds=4.0), start=1.0)
    player.set_speed(2.0)
    assert player.position == pytest.approx(1.0)
    assert len(drain(player)) / RATE == pytest.approx(1.5, rel=0.05)


def test_arquivo_longo_nao_vai_inteiro_para_a_memoria(tmp_path):
    player = StreamPlayer()
    player.open(write_wav(tmp_path / "a.wav", seconds=30.0))
    time.sleep(0.5)
    assert player._buffered <= stream_player.MAX_BUFFER + RATE


def test_arquivo_invalido_explica_o_erro(tmp_path):
    bad = tmp_path / "ruim.mp3"
    bad.write_bytes(b"isto nao e audio")
    with pytest.raises(PlaybackError, match="ruim.mp3"):
        probe(bad)


def test_posicao_alem_do_fim_comeca_do_inicio(tmp_path):
    player = StreamPlayer()
    player.open(write_wav(tmp_path / "a.wav"), start=99.0)
    assert player.position == 0.0


@pytest.mark.skipif(find_ffmpeg() is None, reason="sem FFmpeg")
def test_le_os_capitulos_de_um_m4b(tmp_path):
    a, b = write_wav(tmp_path / "a.wav", 1.0), write_wav(tmp_path / "b.wav", 1.0)
    out = encode_m4b(find_ffmpeg(), [a, b], ["Um", "Dois"], tmp_path / "livro.m4b", {"title": "Livro"}, tmp_path / "w")
    info = probe(out)
    assert info.title == "Livro"
    assert [t for t, _ in info.chapters] == ["Um", "Dois"]
    assert info.chapters[1][1] == pytest.approx(1.0, abs=0.1)


# ---- aba ------------------------------------------------------------------------------


class FakeStream:
    """Placa de som falsa e rapida; nada sai nas caixas de som."""

    def __init__(self, samplerate, channels, dtype, callback):
        self.callback, self.active = callback, False

    def start(self):
        self.active = True

        def run():
            while self.active:
                try:
                    self.callback(np.zeros((8192, 2), dtype=np.int16), 8192, None, None)
                except Stop:
                    break
                time.sleep(0.001)
            self.active = False

        threading.Thread(target=run, daemon=True).start()

    def stop(self):
        self.active = False

    def close(self):
        pass


class Stop(Exception):
    pass


@pytest.fixture
def panel(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(OutputStream=FakeStream, CallbackStop=Stop))
    from docaudio.ui.player_panel import PlayerPanel

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    saved = {}
    widget = PlayerPanel(Settings(player_speed=1.5), saved.__setitem__)
    widget.saved = saved
    widget.app = app
    yield widget
    widget.shutdown()


def pump(panel, until, limit=20.0):
    end = time.time() + limit
    while time.time() < end:
        panel.app.processEvents()
        if until():
            return True
        time.sleep(0.01)
    return False


def test_aba_abre_toca_e_guarda_a_velocidade(panel, tmp_path):
    assert panel.speed_combo.currentData() == 1.5
    assert not panel.play_button.isEnabled()
    path = write_wav(tmp_path / "aula.wav", 1.0)
    assert panel.open_path(path)
    assert panel.saved["player_file"] == str(path)
    assert panel.play_button.isEnabled()
    assert panel.title_label.text() == "aula"

    panel.speed_combo.setCurrentIndex(panel.speed_combo.findData(2.0))
    assert panel.saved["player_speed"] == 2.0
    assert panel.player.speed == 2.0
    panel.toggle()
    assert pump(panel, lambda: panel.player.position == 0.0 and not panel.player.playing)  # terminou e voltou ao inicio


def test_pasta_vira_lista_e_avanca_sozinha(panel, tmp_path):
    write_wav(tmp_path / "02 - dois.wav", 0.3)
    write_wav(tmp_path / "01 - um.wav", 0.3)
    (tmp_path / "notas.txt").write_text("x", encoding="utf-8")
    assert panel.open_path(tmp_path)
    assert [panel.entry_combo.itemText(i) for i in range(panel.entry_combo.count())] == ["01 - um", "02 - dois"]
    assert panel.player.info.path.name == "01 - um.wav"
    panel.toggle()
    assert pump(panel, lambda: panel.player.info.path.name == "02 - dois.wav")
    assert panel.entry_combo.currentIndex() == 1


def test_pasta_sem_audio_avisa(panel, tmp_path):
    assert not panel.open_path(tmp_path)
    assert "Nenhum áudio" in panel.title_label.text()


def test_lembra_onde_parou(panel, tmp_path):
    path = write_wav(tmp_path / "a.wav", 4.0)
    panel.open_path(path)
    panel.player.seek(2.5)
    panel.open_path(write_wav(tmp_path / "b.wav", 1.0))  # guarda a posicao de a.wav
    panel.open_path(path)
    assert panel.player.position == pytest.approx(2.5, abs=0.1)


def test_reconhece_extensoes(tmp_path):
    assert is_audio(tmp_path / "a.M4B") and is_audio(tmp_path / "a.wma") and not is_audio(tmp_path / "a.pdf")
