import pytest


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Cada teste usa uma pasta de dados propria, sem tocar em data/ do projeto."""
    data = tmp_path / "data"
    monkeypatch.setenv("DOCAUDIO_DATA_DIR", str(data))
    return data
