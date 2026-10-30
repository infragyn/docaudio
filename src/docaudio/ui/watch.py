"""Pasta vigiada: documentos novos colocados nela entram sozinhos na fila."""

import json
import logging
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

from docaudio.core import documents, paths

log = logging.getLogger(__name__)

# Espera o arquivo parar de crescer (copia em andamento) antes de pegar.
SETTLE_MS = 2000


def processed_file() -> Path:
    return paths.data_dir() / "pasta_vigiada.json"


def _signature(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


class FolderWatcher(QObject):
    """Emite `files_ready` com os documentos novos ou alterados da pasta."""

    files_ready = Signal(list)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.folder: Path | None = None
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(lambda *_: self._timer.start())
        self._timer = QTimer(self, singleShot=True, interval=SETTLE_MS)
        self._timer.timeout.connect(self.scan)
        self._pending: dict[str, str] = {}
        self._processed = self._load_processed()

    def _load_processed(self) -> dict[str, str]:
        try:
            data = json.loads(processed_file().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_processed(self) -> None:
        processed_file().parent.mkdir(parents=True, exist_ok=True)
        processed_file().write_text(json.dumps(self._processed, ensure_ascii=False, indent=2), encoding="utf-8")

    def start(self, folder: Path, include_existing: bool = False) -> None:
        """Vigia `folder`; arquivos que ja estavam la so entram com `include_existing`."""
        self.stop()
        self.folder = folder
        # Relido aqui: outra sessao (ou outro vigia) pode ter convertido arquivos desde entao.
        self._processed = self._load_processed()
        if not include_existing:
            for path in self._documents():
                self._processed.setdefault(str(path), _signature(path))
            self._save_processed()
        self._watcher.addPath(str(folder))
        log.info("Vigiando a pasta %s", folder)
        self._timer.start()

    def stop(self) -> None:
        if self._watcher.directories():
            self._watcher.removePaths(self._watcher.directories())
        self._timer.stop()
        self._pending.clear()
        self.folder = None

    def _documents(self) -> list[Path]:
        if self.folder is None or not self.folder.is_dir():
            return []
        candidates = [p for p in self.folder.iterdir() if p.is_file() and not p.name.startswith(("~$", "."))]
        return documents.split_supported(candidates)[0]

    def scan(self) -> None:
        """Pega os arquivos novos que nao mudaram desde a ultima olhada."""
        ready, waiting = [], False
        for path in self._documents():
            key = str(path)
            try:
                signature = _signature(path)
            except OSError:
                continue
            if self._processed.get(key) == signature:
                continue
            if self._pending.get(key) == signature:
                ready.append(path)
                self._processed[key] = signature
                self._pending.pop(key, None)
            else:
                self._pending[key] = signature
                waiting = True
        if ready:
            self._save_processed()
            log.info("Pasta vigiada: %d documento(s) novo(s).", len(ready))
            self.files_ready.emit(ready)
        if waiting:
            self._timer.start()
