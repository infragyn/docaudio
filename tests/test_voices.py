import json
import sys

import numpy as np
import pytest

from docaudio.audio.wav import duration_seconds
from docaudio.core import paths
from docaudio.speech import kokoro_provider
from docaudio.speech.piper_provider import VARIANTS, PiperProvider, pitch_shift, variants_of
from docaudio.speech.registry import VoiceRegistry
from docaudio.speech.windows_provider import WindowsProvider, speed_to_rate


def write_voice(folder, voice_id):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{voice_id}.onnx").write_bytes(b"")
    (folder / f"{voice_id}.onnx.json").write_text(json.dumps({"language": {"code": voice_id.split("-")[0]}}))


class Empty:
    """Provedor sem vozes, para isolar o registro do que esta instalado na maquina."""

    def __init__(self, name):
        self.name = name

    def list_voices(self):
        return []

    def find_voice(self, voice_id):
        return None


def test_pitch_shift_shortens_by_factor():
    samples = (np.sin(np.linspace(0, 100, 13500)) * 10000).astype(np.int16)
    shifted = pitch_shift(samples, 1.35)
    assert shifted.dtype == np.int16
    assert len(shifted) == 10000
    assert pitch_shift(samples, 1.0) is samples


def test_variants_only_for_portuguese_medium_voices(tmp_path):
    write_voice(tmp_path, "pt_BR-faber-medium")
    write_voice(tmp_path, "pt_BR-edresson-low")
    write_voice(tmp_path, "en_US-amy-medium")
    voices = PiperProvider([tmp_path]).list_voices()
    ids = [v.id for v in voices]
    assert "pt_BR-faber-medium+infantil" in ids and "pt_BR-faber-medium+feminina" in ids
    assert not any(i.startswith(("pt_BR-edresson-low+", "en_US-amy-medium+")) for i in ids)
    faber = next(v for v in voices if v.id == "pt_BR-faber-medium")
    assert faber.kind == "masculina"
    assert {v.kind for v in variants_of(faber)} == {kind for kind, _ in VARIANTS.values()}


def test_registry_routes_by_prefix_and_groups_by_kind(tmp_path):
    write_voice(tmp_path, "pt_BR-faber-medium")
    registry = VoiceRegistry(piper=PiperProvider([tmp_path]), kokoro=Empty("Kokoro"), windows=Empty("Windows"))
    assert registry.engine_name("pt_BR-faber-medium+infantil") == "Piper"
    assert registry.engine_name("kokoro:pf_dora") == "Kokoro"
    assert registry.engine_name("windows:Microsoft Maria Desktop") == "Windows"
    kinds = [v.kind for v in registry.list_voices()]
    assert kinds == ["feminina (efeito)", "infantil (efeito)", "masculina"]


def test_kokoro_lists_nothing_without_model(tmp_path, monkeypatch):
    monkeypatch.setattr(kokoro_provider, "model_dirs", lambda: [tmp_path / "kokoro"])
    assert kokoro_provider.KokoroProvider().list_voices() == []
    folder = tmp_path / "kokoro"
    folder.mkdir()
    (folder / kokoro_provider.MODEL_FILE).write_bytes(b"")
    (folder / kokoro_provider.VOICES_FILE).write_bytes(b"")
    voices = kokoro_provider.KokoroProvider().list_voices()
    assert [(v.id, v.kind) for v in voices][0] == ("kokoro:pf_dora", "feminina")


def test_windows_speed_to_rate():
    assert speed_to_rate(1.0) == 0
    assert speed_to_rate(3.0) == 10
    assert speed_to_rate(0.5) == -6
    assert speed_to_rate(10) == 10


def test_real_piper_variant_keeps_rhythm(tmp_path):
    provider = PiperProvider([paths.bundled_voices_dir()])
    if not provider.find_voice("pt_BR-faber-medium"):
        pytest.skip("voz pt_BR-faber-medium nao instalada")
    normal = provider.synthesize_to_wav("Teste de narração infantil.", "pt_BR-faber-medium", 1.0, tmp_path / "n.wav")
    child = provider.synthesize_to_wav("Teste de narração infantil.", "pt_BR-faber-medium+infantil", 1.0, tmp_path / "c.wav")
    # Mesmo ritmo: a duracao muda pouco, apesar do tom mais agudo.
    assert duration_seconds(child) == pytest.approx(duration_seconds(normal), rel=0.25)


@pytest.mark.skipif(sys.platform != "win32", reason="vozes SAPI so existem no Windows")
def test_real_windows_voice(tmp_path):
    provider = WindowsProvider()
    voices = provider.list_voices()
    if not voices:
        pytest.skip("nenhuma voz do Windows em português instalada")
    out = provider.synthesize_to_wav("Olá.", voices[0].id, 1.0, tmp_path / "w.wav", trailing_silence_ms=300)
    assert duration_seconds(out) > 0.3
