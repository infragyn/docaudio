import json

from docaudio.storage.settings import DEFAULT_VOICE, MAX_SPEED, MIN_SPEED, Settings


def test_load_returns_defaults_when_file_missing(tmp_path):
    settings = Settings.load(tmp_path / "nao_existe.json")
    assert settings.voice == DEFAULT_VOICE
    assert settings.speed == 1.0


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    Settings(voice="pt_BR-cadu-medium", speed=1.25, output_dir="D:/saida").save(path)
    loaded = Settings.load(path)
    assert loaded.voice == "pt_BR-cadu-medium"
    assert loaded.speed == 1.25
    assert str(loaded.resolved_output_dir()).replace("\\", "/") == "D:/saida"


def test_default_location_uses_data_dir(isolated_data_dir):
    Settings(speed=1.5).save()
    assert (isolated_data_dir / "settings.json").exists()
    assert Settings.load().speed == 1.5


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ isto nao e json", encoding="utf-8")
    assert Settings.load(path) == Settings()


def test_non_object_json_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("[1, 2]", encoding="utf-8")
    assert Settings.load(path) == Settings()


def test_unknown_keys_are_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"voice": "x", "chave_antiga": True}), encoding="utf-8")
    assert Settings.load(path).voice == "x"


def test_speed_is_clamped_and_coerced():
    assert Settings(speed=10).speed == MAX_SPEED
    assert Settings(speed=0.1).speed == MIN_SPEED
    assert Settings(speed="abc").speed == 1.0
