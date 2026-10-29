import json
import wave

import pytest

from docaudio.core import paths
from docaudio.speech.base import SpeechError
from docaudio.speech.piper_provider import PiperProvider, speed_to_length_scale
from docaudio.speech.voice_download import voice_urls


def write_voice(folder, voice_id, language="pt_BR"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{voice_id}.onnx").write_bytes(b"")
    (folder / f"{voice_id}.onnx.json").write_text(json.dumps({"language": {"code": language}}))


def test_speed_to_length_scale():
    assert speed_to_length_scale(1.0) == 1.0
    assert speed_to_length_scale(2.0) == 0.5
    with pytest.raises(ValueError):
        speed_to_length_scale(0)


def test_list_voices_ignores_models_without_config(tmp_path):
    write_voice(tmp_path, "pt_BR-faber-medium")
    (tmp_path / "solto.onnx").write_bytes(b"")
    voices = [v for v in PiperProvider([tmp_path]).list_voices() if "+" not in v.id]
    assert [v.id for v in voices] == ["pt_BR-faber-medium"]
    assert voices[0].label == "Faber (pt_BR, medium)"


def test_list_voices_puts_portuguese_first_and_user_dir_wins(tmp_path):
    user, bundled = tmp_path / "user", tmp_path / "bundled"
    write_voice(bundled, "en_US-amy-medium", "en_US")
    write_voice(bundled, "pt_BR-faber-medium")
    write_voice(user, "pt_BR-faber-medium")
    voices = [v for v in PiperProvider([user, bundled]).list_voices() if "+" not in v.id]
    assert [v.id for v in voices] == ["pt_BR-faber-medium", "en_US-amy-medium"]
    assert voices[0].model_path.parent == user


def test_synthesize_rejects_empty_text_and_missing_voice(tmp_path):
    provider = PiperProvider([tmp_path])
    with pytest.raises(SpeechError):
        provider.synthesize_to_wav("   ", "pt_BR-faber-medium", 1.0, tmp_path / "a.wav")
    with pytest.raises(SpeechError, match="baixar-voz"):
        provider.synthesize_to_wav("Olá", "pt_BR-faber-medium", 1.0, tmp_path / "a.wav")


def test_voice_urls():
    urls = voice_urls("pt_BR-faber-medium")
    assert urls["pt_BR-faber-medium.onnx"].startswith(
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/"
    )
    assert "pt_BR-faber-medium.onnx.json" in urls


@pytest.mark.parametrize("bad", ["faber", "pt_BR-faber", "pt-BR-faber-medium", "pt_BR-faber-ultra"])
def test_voice_urls_rejects_invalid_ids(bad):
    with pytest.raises(ValueError):
        voice_urls(bad)


def test_real_synthesis_when_default_voice_installed(tmp_path):
    provider = PiperProvider([paths.bundled_voices_dir()])
    if not provider.find_voice("pt_BR-faber-medium"):
        pytest.skip("voz pt_BR-faber-medium nao instalada")
    out = provider.synthesize_to_wav("Teste de narração.", "pt_BR-faber-medium", 1.0, tmp_path / "t.wav")
    with wave.open(str(out)) as wav:
        normal = wav.getnframes()
        assert wav.getframerate() > 0
    fast = provider.synthesize_to_wav("Teste de narração.", "pt_BR-faber-medium", 2.0, tmp_path / "f.wav")
    with wave.open(str(fast)) as wav:
        assert wav.getnframes() < normal
    assert not list(tmp_path.glob("*.part"))
