import json

from docaudio.core import dependencies
from docaudio.speech.piper_provider import PiperProvider
from docaudio.storage.settings import Settings


def make_voice(folder, voice_id):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{voice_id}.onnx").write_bytes(b"")
    (folder / f"{voice_id}.onnx.json").write_text(
        json.dumps({"language": {"code": voice_id.split("-")[0]}}), encoding="utf-8"
    )


def test_configured_ffmpeg_has_priority(tmp_path, monkeypatch):
    configured = tmp_path / "ffmpeg.exe"
    configured.write_bytes(b"")
    monkeypatch.setattr(dependencies.shutil, "which", lambda *a, **k: "C:/outro/ffmpeg.exe")
    assert dependencies.find_ffmpeg(str(configured)) == configured


def test_ffmpeg_falls_back_to_path(tmp_path, monkeypatch):
    monkeypatch.setattr(dependencies.paths, "bundled_bin_dir", lambda: tmp_path / "vazio")
    monkeypatch.setattr(dependencies.shutil, "which", lambda *a, **k: "C:/bin/ffmpeg.exe")
    assert dependencies.find_ffmpeg("") is not None


def test_missing_ffmpeg_is_only_a_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(dependencies.paths, "bundled_bin_dir", lambda: tmp_path / "vazio")
    # Tambem cobre o PATH do registro: `which` e chamado com path=...
    monkeypatch.setattr(dependencies.shutil, "which", lambda *a, **k: None)
    status = dependencies.check_ffmpeg(Settings())
    assert not status.ok
    assert not status.required
    assert status.hint


def test_voice_check_ok(tmp_path):
    make_voice(tmp_path, "pt_BR-faber-medium")
    status = dependencies.check_voice(Settings(), PiperProvider([tmp_path]))
    assert status.ok


def test_voice_check_suggests_installed_voices(tmp_path):
    make_voice(tmp_path, "pt_BR-cadu-medium")
    status = dependencies.check_voice(Settings(voice="pt_BR-faber-medium"), PiperProvider([tmp_path]))
    assert not status.ok and status.required
    assert "pt_BR-cadu-medium" in status.hint


def test_voice_check_without_any_voice(tmp_path):
    status = dependencies.check_voice(Settings(), PiperProvider([tmp_path]))
    assert not status.ok
    assert "baixar-voz" in status.hint
