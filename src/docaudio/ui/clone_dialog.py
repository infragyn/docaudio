"""Janela "Criar voz clonada": gravar ou escolher uma gravacao, confirmar autorizacao e criar."""

import logging
import time
import wave
from pathlib import Path

import numpy as np
from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from docaudio.core import paths
from docaudio.core.dependencies import find_ffmpeg
from docaudio.speech import clone_provider
from docaudio.speech.base import VoiceInfo
from docaudio.ui import audio_player
from docaudio.ui.workers import Worker

log = logging.getLogger(__name__)

RECORD_RATE = 24000
READING_TEXT = (
    "Olá! Estou gravando esta amostra para criar a minha voz no DocAudio. "
    "Vou ler com calma, no meu ritmo natural, como se estivesse explicando um assunto a um colega. "
    "A tecnologia da informação reúne redes, sistemas, dados e segurança. "
    "Em dois mil e vinte e seis, as provas exigem atenção, revisão e muita prática. "
    "Será que consigo terminar a leitura em menos de trinta segundos?"
)


class Recorder:
    """Gravacao do microfone padrao em memoria, com `sounddevice`."""

    def __init__(self) -> None:
        self._chunks: list[np.ndarray] = []
        self._stream = None
        self.started_at = 0.0

    def start(self) -> None:
        import sounddevice as sd

        self._chunks = []
        self._stream = sd.InputStream(
            samplerate=RECORD_RATE, channels=1, dtype="int16", callback=lambda data, *_: self._chunks.append(data.copy())
        )
        self._stream.start()
        self.started_at = time.monotonic()

    def stop(self, path: Path) -> float:
        self._stream.stop()
        self._stream.close()
        self._stream = None
        samples = np.concatenate(self._chunks) if self._chunks else np.zeros((0, 1), dtype=np.int16)
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(RECORD_RATE)
            handle.writeframes(samples.tobytes())
        return len(samples) / RECORD_RATE

    @property
    def recording(self) -> bool:
        return self._stream is not None


class CloneVoiceDialog(QDialog):
    def __init__(self, provider, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Criar voz clonada")
        self.resize(620, 560)
        self.provider = provider
        self.pool = QThreadPool.globalInstance()
        self.recorder = Recorder()
        self.recording_path: Path | None = None
        self.created: VoiceInfo | None = None
        self._workers: set[Worker] = set()

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Grave de 15 a 30 segundos da sua voz, em local silencioso, lendo o texto abaixo "
            "(ou escolha uma gravação pronta). O modelo de clonagem roda no seu computador: "
            "a gravação não sai da máquina."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        reading = QPlainTextEdit(READING_TEXT)
        reading.setReadOnly(True)
        reading.setMaximumHeight(110)
        layout.addWidget(reading)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Ex.: Minha voz")
        form.addRow("Nome da voz:", self.name_edit)

        record_row = QHBoxLayout()
        self.record_button = QPushButton("● Gravar")
        self.record_button.clicked.connect(self.toggle_recording)
        choose_button = QPushButton("Escolher arquivo…")
        choose_button.clicked.connect(self.choose_file)
        self.play_button = QPushButton("Ouvir gravação")
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(self.play_recording)
        record_row.addWidget(self.record_button)
        record_row.addWidget(choose_button)
        record_row.addWidget(self.play_button)
        record_row.addStretch(1)
        form.addRow("Gravação:", record_row)
        self.recording_label = QLabel("Nenhuma gravação.")
        form.addRow("", self.recording_label)
        layout.addLayout(form)

        self.consent = QCheckBox(
            "Esta gravação é da minha voz, ou de uma pessoa adulta que autorizou o uso "
            "(voz de criança só com autorização dos responsáveis)."
        )
        self.consent.toggled.connect(self._update_buttons)
        layout.addWidget(self.consent)

        self.model_label = QLabel()
        self.model_label.setWordWrap(True)
        self.download_button = QPushButton(f"Baixar modelo de clonagem (~{clone_provider.MODEL_SIZE_GB:.1f} GB)")
        self.download_button.clicked.connect(self.download_model)
        layout.addWidget(self.model_label)
        layout.addWidget(self.download_button)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status, 1)

        self.buttons = QDialogButtonBox()
        self.create_button = self.buttons.addButton("Criar voz e ouvir teste", QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton("Fechar", QDialogButtonBox.ButtonRole.RejectRole)
        self.create_button.clicked.connect(self.create_voice)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.timer = QTimer(self, interval=200)
        self.timer.timeout.connect(self._tick)
        self.name_edit.textChanged.connect(self._update_buttons)
        self._refresh_environment()

    # ---- estado ---------------------------------------------------------

    def _refresh_environment(self) -> None:
        has_env = clone_provider.clone_python() is not None
        ready = clone_provider.model_ready()
        if not has_env:
            self.model_label.setText(
                "⚠ Ambiente de clonagem (.venv-clonagem) não encontrado. Ele precisa ser instalado uma vez "
                "(PyTorch + Chatterbox); veja \"Vozes clonadas\" no README."
            )
        elif not ready:
            self.model_label.setText("O modelo de clonagem ainda não foi baixado.")
        else:
            self.model_label.setText("✔ Modelo de clonagem pronto (Chatterbox Multilingual pt-BR).")
        self.download_button.setVisible(has_env and not ready)
        self._update_buttons()

    def _update_buttons(self) -> None:
        ready = clone_provider.clone_python() is not None and clone_provider.model_ready()
        self.create_button.setEnabled(
            ready and self.consent.isChecked() and bool(self.name_edit.text().strip())
            and self.recording_path is not None and not self.recorder.recording
        )

    def _run(self, worker: Worker, done, failed) -> None:
        self._workers.add(worker)
        worker.signals.finished.connect(lambda value: (self._workers.discard(worker), done(value)))
        worker.signals.failed.connect(lambda message: (self._workers.discard(worker), failed(message)))
        self.pool.start(worker)

    # ---- gravacao -------------------------------------------------------

    def toggle_recording(self) -> None:
        if self.recorder.recording:
            self.timer.stop()
            path = paths.temp_dir() / "gravacao_voz.wav"
            seconds = self.recorder.stop(path)
            self.record_button.setText("● Gravar de novo")
            self._set_recording(path, f"Gravado: {seconds:.0f} s.")
            return
        try:
            self.recorder.start()
        except Exception as exc:
            QMessageBox.warning(self, "Microfone", f"Não foi possível usar o microfone: {exc}")
            return
        audio_player.stop()
        self.record_button.setText("■ Parar")
        self.recording_label.setText("Gravando… 0 s")
        self.timer.start()
        self._update_buttons()

    def _tick(self) -> None:
        seconds = time.monotonic() - self.recorder.started_at
        self.recording_label.setText(f"Gravando… {seconds:.0f} s")
        if seconds >= clone_provider.MAX_REFERENCE_SECONDS:
            self.toggle_recording()

    def choose_file(self) -> None:
        file, _ = QFileDialog.getOpenFileName(
            self, "Escolher gravação", str(Path.home()), "Áudio (*.wav *.mp3 *.m4a *.ogg *.flac *.opus);;Todos (*.*)"
        )
        if file:
            self._set_recording(Path(file), f"Arquivo: {Path(file).name}")

    def _set_recording(self, path: Path, text: str) -> None:
        self.recording_path = path
        self.recording_label.setText(text)
        self.play_button.setEnabled(path.suffix.lower() == ".wav")
        self._update_buttons()

    def play_recording(self) -> None:
        if self.recording_path:
            audio_player.play_wav(self.recording_path)

    # ---- modelo e criacao -----------------------------------------------

    def download_model(self) -> None:
        self.download_button.setEnabled(False)
        self.status.setText("Baixando o modelo de clonagem… isso pode levar vários minutos.")
        self._run(Worker(clone_provider.prepare_model), self._on_downloaded, self._on_failed)

    def _on_downloaded(self, _value) -> None:
        self.status.setText("Modelo baixado.")
        self.download_button.setEnabled(True)
        self._refresh_environment()

    def create_voice(self) -> None:
        try:
            self.created = clone_provider.create_voice(
                self.name_edit.text(), self.recording_path, self.consent.isChecked(), find_ffmpeg()
            )
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Criar voz", str(exc))
            return
        self.create_button.setEnabled(False)
        self.status.setText("Voz criada. Gerando um teste (a primeira vez carrega o modelo na placa de vídeo)…")
        sample = paths.temp_dir() / "teste_voz_clonada.wav"
        text = "Olá! Esta é a minha voz clonada no DocAudio, lendo um trecho de teste."
        self._run(
            Worker(self.provider.synthesize_to_wav, text, self.created.id, 1.0, sample),
            self._on_sample,
            self._on_failed,
        )

    def _on_sample(self, path: Path) -> None:
        self.status.setText(f"Pronto! A voz “{self.created.label}” já aparece na lista de vozes.")
        audio_player.play_wav(path)

    def _on_failed(self, message: str) -> None:
        self.status.setText(f"Erro: {message}")
        self.download_button.setEnabled(True)
        self._update_buttons()

    def reject(self) -> None:
        if self.recorder.recording:
            self.timer.stop()
            self.recorder.stop(paths.temp_dir() / "gravacao_descartada.wav")
        super().reject()
