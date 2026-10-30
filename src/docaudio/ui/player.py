"""Player do audiolivro: capitulos, texto acompanhando a fala e posicao lembrada."""

import bisect
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from docaudio.audio.playback import Player, PlaybackError, decode_to_wav, read_wav
from docaudio.core import paths
from docaudio.core.conversion import WORK_DIR
from docaudio.storage.manifest import Manifest
from docaudio.ui import theme

log = logging.getLogger(__name__)

SKIP_SECONDS = 10
TICK_MS = 100


@dataclass(frozen=True)
class Track:
    title: str
    file: Path
    start_s: float
    duration_s: float | None
    timings: list


def manifest_path(folder: Path) -> Path:
    return folder / WORK_DIR / "manifest.json"


def load_tracks(folder: Path) -> tuple[str, list[Track]]:
    """Capitulos prontos de uma pasta convertida pelo DocAudio."""
    manifest = Manifest.load(manifest_path(folder))
    if manifest is None:
        raise PlaybackError("Esta pasta não tem um audiolivro gerado pelo DocAudio.")
    chapters = [c for c in manifest.chapters if c.status == "concluido"]
    tracks = []
    if manifest.final_output:  # M4B: um arquivo, capitulos por tempo de inicio
        file = folder / manifest.final_output
        for index, chapter in enumerate(chapters):
            following = chapters[index + 1].start_s if index + 1 < len(chapters) else None
            duration = following - chapter.start_s if following is not None else None
            tracks.append(Track(chapter.title, file, chapter.start_s, duration, chapter.timings))
    else:
        tracks = [Track(c.title, folder / c.output, 0.0, None, c.timings) for c in chapters if c.output]
    tracks = [t for t in tracks if t.file.is_file()]
    if not tracks:
        raise PlaybackError("Nenhum capítulo pronto nesta pasta; converta o documento primeiro.")
    return manifest.title, tracks


def positions_file() -> Path:
    return paths.data_dir() / "posicoes_player.json"


def load_position(folder: Path) -> tuple[int, float]:
    try:
        data = json.loads(positions_file().read_text(encoding="utf-8")).get(str(folder), {})
        return int(data.get("capitulo", 0)), float(data.get("segundos", 0.0))
    except (OSError, ValueError, AttributeError):
        return 0, 0.0


def save_position(folder: Path, chapter: int, seconds: float) -> None:
    try:
        data = json.loads(positions_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[str(folder)] = {"capitulo": chapter, "segundos": round(seconds, 1)}
    positions_file().parent.mkdir(parents=True, exist_ok=True)
    positions_file().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def format_time(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


class PlayerDialog(QDialog):
    """Janela independente: pode ficar aberta enquanto o resto do programa e usado."""

    def __init__(self, folder: Path, ffmpeg: Path | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.folder = folder
        self.ffmpeg = ffmpeg
        self.player = Player()
        self.track_index = -1
        self._current_segment = -1
        self._seeking = False
        title, self.tracks = load_tracks(folder)
        self.setWindowTitle(f"Ouvir — {title}")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(980, 620)

        layout = theme.dialog_body(
            self,
            title,
            f"{len(self.tracks)} capítulo(s) · clique numa frase para ouvir a partir dela · "
            "espaço toca/pausa, setas voltam/avançam 10 s",
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay),
        )

        self.chapter_list = QListWidget()
        self.chapter_list.setMinimumWidth(180)
        self.chapter_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chapter_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        for track in self.tracks:
            self.chapter_list.addItem(QListWidgetItem(track.title))
        self.chapter_list.itemActivated.connect(lambda item: self.open_track(self.chapter_list.row(item), play=True))
        self.chapter_list.itemClicked.connect(lambda item: self.open_track(self.chapter_list.row(item), play=True))

        self.text_list = QListWidget()
        self.text_list.setWordWrap(True)
        self.text_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.text_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text_list.setToolTip("Clique numa frase para ouvir a partir dela.")
        self.text_list.itemClicked.connect(self._on_text_clicked)
        self.no_text = QLabel(
            "O texto acompanhando a fala aparece em conversões feitas a partir desta versão do DocAudio."
        )
        self.no_text.setWordWrap(True)
        theme.set_role(self.no_text, "muted")
        text_box = QWidget()
        text_layout = QVBoxLayout(text_box)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.addWidget(self.text_list, 1)
        text_layout.addWidget(self.no_text)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.chapter_list)
        splitter.addWidget(text_box)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([240, 720])
        layout.addWidget(splitter, 1)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setMinimumWidth(110)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        transport, transport_layout = theme.card()
        bar = QHBoxLayout()
        bar.addWidget(self.slider, 1)
        bar.addWidget(self.time_label)
        transport_layout.addLayout(bar)

        controls = QHBoxLayout()
        icon = self.style().standardIcon
        self.prev_button = self._button(icon(QStyle.StandardPixmap.SP_MediaSkipBackward), "Capítulo anterior", self.previous_track)
        self.back_button = self._button(icon(QStyle.StandardPixmap.SP_MediaSeekBackward), f"Voltar {SKIP_SECONDS} s", lambda: self.skip(-SKIP_SECONDS))
        self.play_button = self._button(icon(QStyle.StandardPixmap.SP_MediaPlay), "Tocar / pausar (espaço)", self.toggle)
        self.play_button.setText("Tocar")
        theme.set_role(self.play_button, "primary")
        self.forward_button = self._button(icon(QStyle.StandardPixmap.SP_MediaSeekForward), f"Avançar {SKIP_SECONDS} s", lambda: self.skip(SKIP_SECONDS))
        self.next_button = self._button(icon(QStyle.StandardPixmap.SP_MediaSkipForward), "Próximo capítulo", self.next_track)
        controls.addStretch(1)
        for widget in (self.prev_button, self.back_button, self.play_button, self.forward_button, self.next_button):
            controls.addWidget(widget)
        controls.addStretch(1)
        transport_layout.addLayout(controls)
        layout.addWidget(transport)

        self.timer = QTimer(self, interval=TICK_MS)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        chapter, seconds = load_position(folder)
        self.open_track(min(chapter, len(self.tracks) - 1), play=False, at=seconds)

    def _button(self, icon, tip: str, slot) -> QPushButton:
        button = QPushButton()
        button.setIcon(icon)
        button.setToolTip(tip)
        button.setMinimumHeight(36)
        button.clicked.connect(slot)
        return button

    # ---- capitulos ---------------------------------------------------------------

    def open_track(self, index: int, play: bool, at: float = 0.0) -> None:
        if not 0 <= index < len(self.tracks):
            return
        if index == self.track_index:
            if play and not self.player.playing:
                self._play()
            return
        self._remember()
        track = self.tracks[index]
        target = paths.temp_dir() / "player" / "capitulo.wav"
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.player.stop()
            wav = decode_to_wav(self.ffmpeg, track.file, target, track.start_s, track.duration_s)
            samples, rate = read_wav(wav)
        except PlaybackError as exc:
            QMessageBox.warning(self, "Player", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.player.load(samples, rate)
        self.track_index = index
        self.chapter_list.setCurrentRow(index)
        self._fill_text(track)
        self.slider.setRange(0, int(self.player.duration * 10))
        self.player.seek(at)
        self._tick()
        if play:
            self._play()

    def _fill_text(self, track: Track) -> None:
        self.text_list.clear()
        self._starts = [start for start, _ in track.timings]
        for _, text in track.timings:
            self.text_list.addItem(QListWidgetItem(text))
        self._current_segment = -1
        self.text_list.setVisible(bool(track.timings))
        self.no_text.setVisible(not track.timings)

    def previous_track(self) -> None:
        # Como nos players comuns: no meio do capitulo, volta ao inicio dele.
        if self.player.position > 3:
            self.player.seek(0)
        else:
            self.open_track(self.track_index - 1, play=self.player.playing)

    def next_track(self) -> None:
        self.open_track(self.track_index + 1, play=True)

    # ---- reproducao --------------------------------------------------------------

    def _play(self) -> None:
        try:
            self.player.play()
        except PlaybackError as exc:
            QMessageBox.warning(self, "Player", str(exc))

    def toggle(self) -> None:
        if self.player.playing:
            self.player.pause()
            self._remember()
        else:
            self._play()
        self._tick()

    def skip(self, seconds: float) -> None:
        self.player.seek(self.player.position + seconds)
        self._tick()

    def _on_slider_released(self) -> None:
        self._seeking = False
        self.player.seek(self.slider.value() / 10)
        self._tick()

    def _on_text_clicked(self, item: QListWidgetItem) -> None:
        row = self.text_list.row(item)
        if 0 <= row < len(self._starts):
            self.player.seek(self._starts[row])
            if not self.player.playing:
                self._play()
            self._tick()

    def _tick(self) -> None:
        position, duration = self.player.position, self.player.duration
        playing = self.player.playing
        self.play_button.setText("Pausar" if playing else "Tocar")
        self.play_button.setIcon(
            self.style().standardIcon(
                QStyle.StandardPixmap.SP_MediaPause if playing else QStyle.StandardPixmap.SP_MediaPlay
            )
        )
        if not self._seeking:
            self.slider.setValue(int(position * 10))
        self.time_label.setText(f"{format_time(position)} / {format_time(duration)}")
        self._highlight(position)
        if self.player.finished and not playing:
            self.player.finished = False
            if self.track_index + 1 < len(self.tracks):
                self.next_track()
            else:
                self._remember()

    def _highlight(self, position: float) -> None:
        if not getattr(self, "_starts", None):
            return
        index = max(0, bisect.bisect_right(self._starts, position) - 1)
        if index == self._current_segment:
            return
        # A selecao da lista marca a frase falada (o estilo do tema cobre cores de fundo avulsas).
        self._current_segment = index
        self.text_list.setCurrentRow(index)
        self.text_list.scrollToItem(self.text_list.item(index), QListWidget.ScrollHint.PositionAtCenter)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space:
            self.toggle()
        elif event.key() == Qt.Key.Key_Left:
            self.skip(-SKIP_SECONDS)
        elif event.key() == Qt.Key.Key_Right:
            self.skip(SKIP_SECONDS)
        else:
            super().keyPressEvent(event)

    def _remember(self) -> None:
        if self.track_index >= 0:
            save_position(self.folder, self.track_index, self.player.position)

    def done(self, result: int) -> None:
        self._remember()
        self.timer.stop()
        self.player.stop()
        super().done(result)
