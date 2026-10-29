import json
import sys
import wave

import pytest

from docaudio.audio.wav import duration_seconds
from docaudio.speech import clone_provider
from docaudio.speech.base import SpeechError
from docaudio.speech.registry import VoiceRegistry

FAKE_WORKER = '''
import json, sys, wave
sys.stdout.write(json.dumps({"event": "started"}) + "\\n"); sys.stdout.flush()
for line in sys.stdin:
    req = json.loads(line)
    if req["cmd"] == "quit":
        print(json.dumps({"ok": True}), flush=True); break
    if req["cmd"] == "load":
        print(json.dumps({"ok": True, "device": "cpu"}), flush=True)
    elif req["cmd"] == "synth":
        if "erro" in req["text"]:
            print(json.dumps({"ok": False, "error": "falha simulada"}), flush=True); continue
        with wave.open(req["out"], "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
            w.writeframes(b"\\x01\\x00" * 2400)
        print(json.dumps({"ok": True, "sample_rate": 24000}), flush=True)
'''


def write_wav(path, seconds, rate=24000):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


SUPPORTED = clone_provider.CloneSupport(True, "ok")


@pytest.fixture
def supported(monkeypatch):
    monkeypatch.setattr(clone_provider, "check_support", lambda probe=False: SUPPORTED)


@pytest.fixture
def fake_environment(tmp_path, monkeypatch, supported):
    """Ambiente de clonagem falso: o 'python' e o do teste e o auxiliar gera 0,1 s por frase."""
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE_WORKER, encoding="utf-8")
    monkeypatch.setenv("DOCAUDIO_CLONE_PYTHON", sys.executable)
    monkeypatch.setattr(clone_provider, "worker_script", lambda: script)
    monkeypatch.setattr(clone_provider, "model_ready", lambda: True)
    worker = clone_provider.CloneWorker()
    monkeypatch.setattr(clone_provider, "_worker", worker)
    yield
    worker.stop()


def test_slugify():
    assert clone_provider.slugify("Minha Voz Ção!") == "minha-voz-cao"
    assert clone_provider.slugify("!!!") == "voz"


def test_create_voice_requires_consent_name_and_duration(tmp_path):
    recording = write_wav(tmp_path / "a.wav", 12)
    with pytest.raises(ValueError, match="autorizou"):
        clone_provider.create_voice("Minha voz", recording, consent=False, ffmpeg=None)
    with pytest.raises(ValueError, match="nome"):
        clone_provider.create_voice("  ", recording, consent=True, ffmpeg=None)
    short = write_wav(tmp_path / "curta.wav", 2)
    with pytest.raises(ValueError, match="pelo menos"):
        clone_provider.create_voice("Curta", short, consent=True, ffmpeg=None)
    assert not (clone_provider.voices_dir() / "curta").exists()
    with pytest.raises(ValueError, match="WAV"):
        clone_provider.create_voice("Mp3", tmp_path / "x.mp3", consent=True, ffmpeg=None)


def test_create_list_and_delete_voice(tmp_path, supported):
    voice = clone_provider.create_voice("Minha voz", write_wav(tmp_path / "a.wav", 12), True, ffmpeg=None)
    assert voice.id == "clone:minha-voz"
    assert voice.kind == "sua voz" and voice.engine == "Clonada"
    with pytest.raises(ValueError, match="Já existe"):
        clone_provider.create_voice("Minha voz", write_wav(tmp_path / "b.wav", 12), True, ffmpeg=None)
    registry = VoiceRegistry()
    assert registry.list_voices()[0].id == "clone:minha-voz"  # vozes proprias primeiro
    assert registry.engine_name("clone:minha-voz") == "Clonada"
    clone_provider.delete_voice(voice.id)
    assert clone_provider.ClonedVoiceProvider().list_voices() == []


def test_synthesis_through_worker(tmp_path, fake_environment):
    voice = clone_provider.create_voice("Teste", write_wav(tmp_path / "a.wav", 10), True, ffmpeg=None)
    out = clone_provider.ClonedVoiceProvider().synthesize_to_wav(
        "Primeira frase. Segunda frase.", voice.id, 1.0, tmp_path / "out.wav",
        sentence_pause_ms=200, trailing_silence_ms=500,
    )
    # duas frases de 0,1 s + pausa de 0,2 s + silencio final de 0,5 s
    assert duration_seconds(out) == pytest.approx(0.9, abs=0.01)
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".wav") == ["a.wav", "out.wav"]


def test_worker_errors_become_speech_errors(tmp_path, fake_environment):
    voice = clone_provider.create_voice("Teste", write_wav(tmp_path / "a.wav", 10), True, ffmpeg=None)
    with pytest.raises(SpeechError, match="falha simulada"):
        clone_provider.ClonedVoiceProvider().synthesize_to_wav("Isto dá erro.", voice.id, 1.0, tmp_path / "o.wav")


def test_missing_environment_locks_cloning(tmp_path, monkeypatch):
    """Maquina sem o ambiente: nada aparece e nenhum processo e iniciado."""
    monkeypatch.setattr(clone_provider, "clone_python", lambda: None)
    started = []
    monkeypatch.setattr(clone_provider.CloneWorker, "_start", lambda self: started.append(True))
    clone_provider.create_voice("Teste", write_wav(tmp_path / "a.wav", 10), True, ffmpeg=None)
    assert clone_provider.ClonedVoiceProvider().list_voices() == []
    with pytest.raises(SpeechError, match="não instalado"):
        clone_provider.ClonedVoiceProvider().synthesize_to_wav("Olá.", "clone:teste", 1.0, tmp_path / "o.wav")
    assert started == []


def fake_python(tmp_path):
    python = tmp_path / "python.exe"
    python.write_bytes(b"")
    return python


@pytest.mark.parametrize(
    "capability, available, reason",
    [
        ({"cuda": False, "gpu": "", "vram_gb": 0}, False, "Requer placa NVIDIA"),
        ({"cuda": True, "gpu": "GT 1030", "vram_gb": 2.0}, False, "GT 1030"),
        ({"cuda": True, "gpu": "RTX 4050", "vram_gb": 6.4}, True, "Pronta"),
    ],
)
def test_support_uses_cached_hardware_check(tmp_path, monkeypatch, capability, available, reason):
    python = fake_python(tmp_path)
    monkeypatch.setattr(clone_provider, "clone_python", lambda: python)
    monkeypatch.setattr(clone_provider, "model_ready", lambda: True)
    clone_provider.capability_file().parent.mkdir(parents=True, exist_ok=True)
    data = {**capability, "python": clone_provider._python_signature(python)}
    clone_provider.capability_file().write_text(json.dumps(data), encoding="utf-8")
    support = clone_provider.check_support()
    assert support.available is available
    assert reason in support.reason


def test_support_without_check_or_when_disabled(tmp_path, monkeypatch):
    python = fake_python(tmp_path)
    monkeypatch.setattr(clone_provider, "clone_python", lambda: python)
    assert not clone_provider.check_support().available  # ainda nao verificada: nao tenta
    assert "Verificando" in clone_provider.check_support().reason
    clone_provider.set_enabled(False)
    try:
        assert "desativada" in clone_provider.check_support().reason
    finally:
        clone_provider.set_enabled(True)


def test_model_missing_is_flagged(tmp_path, monkeypatch):
    python = fake_python(tmp_path)
    monkeypatch.setattr(clone_provider, "clone_python", lambda: python)
    monkeypatch.setattr(clone_provider, "model_ready", lambda: False)
    clone_provider.capability_file().parent.mkdir(parents=True, exist_ok=True)
    data = {"cuda": True, "gpu": "RTX", "vram_gb": 8, "python": clone_provider._python_signature(python)}
    clone_provider.capability_file().write_text(json.dumps(data), encoding="utf-8")
    support = clone_provider.check_support()
    assert support.available and support.needs_model
