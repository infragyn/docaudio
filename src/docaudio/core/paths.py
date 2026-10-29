"""Localizacao de recursos, dados do usuario e pasta de saida.

Em desenvolvimento tudo fica dentro do projeto. No executavel empacotado os
recursos vem do pacote e os dados do usuario ficam em %LOCALAPPDATA%.
"""

import os
import sys
from pathlib import Path

from docaudio import APP_NAME


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resources_dir() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS")) / "resources"
    return project_root() / "resources"


def data_dir() -> Path:
    override = os.environ.get("DOCAUDIO_DATA_DIR")
    if override:
        return Path(override)
    if is_frozen():
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
    return project_root() / "data"


def default_output_dir() -> Path:
    if is_frozen():
        return Path.home() / "Documents" / APP_NAME
    return project_root() / "output"


def bundled_bin_dir() -> Path:
    return resources_dir() / "bin"


def bundled_voices_dir() -> Path:
    return resources_dir() / "voices"


def user_voices_dir() -> Path:
    return data_dir() / "voices"


def voice_dirs() -> list[Path]:
    """Pastas onde procurar modelos de voz, da mais especifica para a padrao."""
    return [user_voices_dir(), bundled_voices_dir()]


def logs_dir() -> Path:
    return data_dir() / "logs"


def temp_dir() -> Path:
    return data_dir() / "tmp"


def settings_file() -> Path:
    return data_dir() / "settings.json"
