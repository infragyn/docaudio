"""Reproducao simples de amostras WAV."""

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def play_wav(path: Path) -> bool:
    """Toca o arquivo de forma assincrona. Devolve False se nao houver suporte."""
    if sys.platform != "win32":
        log.warning("Reprodução de amostras disponível apenas no Windows.")
        return False
    import winsound

    winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    return True


def stop() -> None:
    if sys.platform == "win32":
        import winsound

        winsound.PlaySound(None, winsound.SND_PURGE)
