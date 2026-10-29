"""Uma janela so: uma segunda abertura entrega os arquivos a primeira e encerra."""

import hashlib
import json
import logging
import os
from collections.abc import Callable

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from docaudio.core import paths

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_MS = 500
ACK_TIMEOUT_MS = 3000


def server_name() -> str:
    """Nome por usuario e pasta de dados: instalacoes separadas nao se confundem."""
    key = f"{os.environ.get('USERNAME', '')}|{paths.data_dir().resolve()}"
    return "DocAudio-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def notify_running(files: list[str]) -> bool:
    """Se ja ha um DocAudio aberto, pede que ele apareca (e abra `files`); True se havia."""
    socket = QLocalSocket()
    socket.connectToServer(server_name())
    if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
        return False
    socket.write((json.dumps({"files": files}) + "\n").encode("utf-8"))
    socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
    # Espera a confirmacao: fechar antes pode descartar a mensagem no pipe do Windows.
    socket.waitForReadyRead(ACK_TIMEOUT_MS)
    socket.disconnectFromServer()
    return True


class InstanceServer(QObject):
    """Escuta as aberturas seguintes e repassa a lista de arquivos a `on_open`."""

    def __init__(self, on_open: Callable[[list[str]], None], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.on_open = on_open
        self.server = QLocalServer(self)
        # Sobra de uma execucao que travou ou foi encerrada a forca.
        QLocalServer.removeServer(server_name())
        if not self.server.listen(server_name()):
            log.warning("Não foi possível garantir janela única: %s", self.server.errorString())
        self.server.newConnection.connect(self._accept)

    def _accept(self) -> None:
        while (socket := self.server.nextPendingConnection()) is not None:
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(socket.deleteLater)
            # A mensagem pode chegar junto com a conexao, antes de ligar o readyRead.
            self._read(socket)

    def _read(self, socket: QLocalSocket) -> None:
        if not socket.canReadLine():
            return
        try:
            files = json.loads(bytes(socket.readLine()).decode("utf-8")).get("files", [])
        except ValueError:
            files = []
        socket.write(b"ok\n")
        socket.flush()
        self.on_open([str(f) for f in files])
