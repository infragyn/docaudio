"""Aba Player: toca audios e audiolivros de qualquer formato, com controle de velocidade,
marcadores, repeticao de um trecho (A-B) e timer de desligar."""

import bisect
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from docaudio.audio.playback import PlaybackError
from docaudio.audio.stream_player import FILE_DIALOG_FILTER, StreamPlayer, is_audio
from docaudio.storage.settings import Settings
from docaudio.ui import theme
from docaudio.ui.player import format_time, load_bookmarks, load_position, save_bookmarks, save_position

log = logging.getLogger(__name__)

SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)
SKIP_SECONDS = 10
TICK_MS = 200
MIN_LOOP_SECONDS = 0.5
END_OF_CHAPTER = "capitulo"
SLEEP_CHOICES = (("Não", None), ("15 min", 15), ("30 min", 30), ("45 min", 45), ("60 min", 60),
                 ("Fim do capítulo", END_OF_CHAPTER))


@dataclass(frozen=True)
class Entry:
    """Uma linha da lista: capitulo de um arquivo ou arquivo de uma pasta."""

    title: str
    file: Path
    start: float = 0.0


@dataclass(frozen=True)
class Bookmark:
    file: Path
    seconds: float
    name: str = ""


class PlayerPanel(QWidget):
    """`on_setting(nome, valor)` guarda a velocidade e o ultimo arquivo nas configuracoes."""

    def __init__(self, settings: Settings, on_setting: Callable[[str, object], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.on_setting = on_setting
        self.player = StreamPlayer()
        self.player.speed = settings.player_speed if settings.player_speed in SPEEDS else 1.0
        self.entries: list[Entry] = []
        self.files: list[Path] = []
        self._seeking = False
        self._shown_error = ""
        self.bookmarks: list[Bookmark] = []
        # Repeticao A-B: arquivo e pontos; B vazio enquanto so o inicio foi marcado.
        self.loop_file: Path | None = None
        self.loop_a: float | None = None
        self.loop_b: float | None = None
        self._loop_note = ""
        self.clock = time.monotonic
        self._sleep_deadline: float | None = None
        self._sleep_index = -1
        self._sleep_note = ""

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        sources = QHBoxLayout()
        icon = self.style().standardIcon
        self.open_file_button = QPushButton("Abrir arquivo…")
        self.open_file_button.setIcon(icon(QStyle.StandardPixmap.SP_FileIcon))
        self.open_file_button.clicked.connect(lambda: self.choose_file())
        self.open_folder_button = QPushButton("Abrir pasta…")
        self.open_folder_button.setIcon(icon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.open_folder_button.clicked.connect(lambda: self.choose_folder())
        sources.addWidget(self.open_file_button)
        sources.addWidget(self.open_folder_button)
        layout.addLayout(sources)

        self.title_label = QLabel("Nenhum áudio aberto. MP3, M4A, M4B, WAV, FLAC, OGG, OPUS, WMA e outros.")
        theme.set_role(self.title_label, "muted")
        self.title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.title_label)

        self.entry_combo = QComboBox()
        self.entry_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.entry_combo.setMinimumContentsLength(16)
        self.entry_combo.view().setMinimumWidth(320)
        self.entry_combo.setToolTip("Capítulo ou faixa")
        self.entry_combo.activated.connect(self._on_entry_chosen)
        self.entry_combo.setVisible(False)
        layout.addWidget(self.entry_combo)

        bar = QHBoxLayout()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.time_label = QLabel("0:00 / 0:00")
        bar.addWidget(self.slider, 1)
        bar.addWidget(self.time_label)
        layout.addLayout(bar)

        controls = QHBoxLayout()
        self.previous_button = self._button(QStyle.StandardPixmap.SP_MediaSkipBackward, "Capítulo ou faixa anterior", self.previous_entry)
        self.back_button = self._button(QStyle.StandardPixmap.SP_MediaSeekBackward, f"Voltar {SKIP_SECONDS} s", lambda: self.skip(-SKIP_SECONDS))
        self.play_button = self._button(QStyle.StandardPixmap.SP_MediaPlay, "Tocar / pausar", self.toggle)
        self.play_button.setText("Tocar")
        theme.set_role(self.play_button, "primary")
        self.forward_button = self._button(QStyle.StandardPixmap.SP_MediaSeekForward, f"Avançar {SKIP_SECONDS} s", lambda: self.skip(SKIP_SECONDS))
        self.next_button = self._button(QStyle.StandardPixmap.SP_MediaSkipForward, "Próximo capítulo ou faixa", self.next_entry)
        for button in (self.previous_button, self.back_button, self.play_button, self.forward_button, self.next_button):
            controls.addWidget(button)
        controls.addStretch(1)
        controls.addWidget(QLabel("Velocidade:"))
        self.speed_combo = QComboBox()
        for speed in SPEEDS:
            self.speed_combo.addItem(f"{speed:.2f}x".replace(".", ","), speed)
        self.speed_combo.setCurrentIndex(self.speed_combo.findData(self.player.speed))
        self.speed_combo.setToolTip("Muda a velocidade sem mudar o tom da voz")
        self.speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        controls.addWidget(self.speed_combo)
        layout.addLayout(controls)

        tools = QHBoxLayout()
        self.bookmark_button = QPushButton("Marcar este ponto")
        self.bookmark_button.setToolTip("Guarda o momento atual para voltar a ele depois")
        self.bookmark_button.clicked.connect(lambda: self.add_bookmark())
        self.loop_button = QPushButton("Repetir trecho (A–B)")
        self.loop_button.setToolTip("Clique no início do trecho, depois no fim: o trecho se repete até você parar")
        self.loop_button.clicked.connect(lambda: self.toggle_loop())
        tools.addWidget(self.bookmark_button)
        tools.addWidget(self.loop_button)
        tools.addStretch(1)
        tools.addWidget(QLabel("Desligar:"))
        self.sleep_combo = QComboBox()
        for label, value in SLEEP_CHOICES:
            self.sleep_combo.addItem(label, value)
        self.sleep_combo.setToolTip("Pausa o áudio sozinho depois de um tempo ou no fim do capítulo")
        self.sleep_combo.currentIndexChanged.connect(self._on_sleep_changed)
        tools.addWidget(self.sleep_combo)
        layout.addLayout(tools)

        self.tools_status = QLabel()
        theme.set_role(self.tools_status, "muted")
        layout.addWidget(self.tools_status)

        marks_header = QHBoxLayout()
        marks_header.addWidget(QLabel("Marcadores (clique para ir até o ponto):"))
        marks_header.addStretch(1)
        self.remove_bookmark_button = QPushButton("Remover marcador")
        self.remove_bookmark_button.clicked.connect(lambda: self.remove_bookmark())
        marks_header.addWidget(self.remove_bookmark_button)
        layout.addLayout(marks_header)
        self.bookmark_list = QListWidget()
        self.bookmark_list.itemClicked.connect(self._on_bookmark_clicked)
        self.bookmark_list.itemActivated.connect(self._on_bookmark_clicked)
        self.bookmark_list.currentRowChanged.connect(lambda _row: self._update_bookmark_buttons())
        layout.addWidget(self.bookmark_list, 1)

        self.timer = QTimer(self, interval=TICK_MS)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._set_enabled(False)

        last = Path(settings.player_file) if settings.player_file else None
        if last and last.is_file():
            self.open_path(last, quiet=True)

    def _button(self, pixmap, tip: str, slot) -> QPushButton:
        button = QPushButton()
        button.setIcon(self.style().standardIcon(pixmap))
        button.setToolTip(tip)
        button.clicked.connect(lambda: slot())
        return button

    def _set_enabled(self, enabled: bool) -> None:
        for widget in (
            self.previous_button, self.back_button, self.play_button, self.forward_button, self.next_button,
            self.slider, self.entry_combo, self.bookmark_button, self.loop_button,
        ):
            widget.setEnabled(enabled)
        self._update_bookmark_buttons()

    def _show_error(self, message: str) -> None:
        theme.set_role(self.title_label, "error")
        self.title_label.setText(message)
        self.title_label.setToolTip(message)
        log.warning("%s", message)

    # ---- abrir ------------------------------------------------------------------------

    def choose_file(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Abrir áudio ou audiolivro", "", FILE_DIALOG_FILTER)
        if name:
            self.open_path(Path(name))

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Abrir pasta de áudios")
        if folder:
            self.open_path(Path(folder))

    def open_path(self, path: Path, quiet: bool = False) -> bool:
        """Abre um arquivo ou todos os audios de uma pasta; retoma de onde parou."""
        self._remember()
        if path.is_dir():
            self.files = sorted(p for p in path.iterdir() if p.is_file() and is_audio(p))
            if not self.files:
                self._show_error(f"Nenhum áudio em {path.name}.")
                return False
            target = self.files[0]
        else:
            self.files = [path]
            target = path
        return self._open_file(target, quiet=quiet)

    def _open_file(self, file: Path, at: float | None = None, quiet: bool = False) -> bool:
        if at is None:
            at = load_position(file)[1]
        try:
            info = self.player.open(file, at)
        except PlaybackError as exc:
            if not quiet:
                self._show_error(str(exc))
            return False
        if len(self.files) > 1:
            self.entries = [Entry(p.stem, p) for p in self.files]
        else:
            self.entries = [Entry(title, file, start) for title, start in info.chapters]
        self.entry_combo.clear()
        for entry in self.entries:
            self.entry_combo.addItem(entry.title)
        self.entry_combo.setVisible(len(self.entries) > 1)
        theme.set_role(self.title_label, "")
        self.title_label.setText(info.title)
        self.title_label.setToolTip(str(file))
        self.slider.setRange(0, int(info.duration * 10))
        if self.loop_file != file:
            self.clear_loop()
        self._refresh_bookmarks()
        self._set_enabled(True)
        self.on_setting("player_file", str(file))
        self._tick()
        log.info("Player: %s (%s).", file.name, format_time(info.duration))
        return True

    # ---- reproducao -------------------------------------------------------------------

    def toggle(self) -> None:
        if self.player.playing:
            self.pause()
        else:
            self._play()
        self._tick()

    def _play(self) -> None:
        try:
            self.player.play()
        except PlaybackError as exc:
            self._show_error(str(exc))

    def pause(self) -> None:
        if self.player.playing:
            self.player.pause()
        self._remember()
        self._tick()

    def skip(self, seconds: float) -> None:
        self.player.seek(self.player.position + seconds)
        self._user_moved()

    def _current_index(self) -> int:
        if not self.entries or not self.player.info:
            return -1
        if len(self.files) > 1:
            return next((i for i, e in enumerate(self.entries) if e.file == self.player.info.path), -1)
        return max(0, bisect.bisect_right([e.start for e in self.entries], self.player.position + 0.05) - 1)

    def _go_to(self, index: int) -> None:
        entry = self.entries[index]
        if entry.file != self.player.info.path:
            playing = self.player.playing or self.player.finished
            self._remember()
            if self._open_file(entry.file, at=0.0) and playing:
                self._play()
        else:
            self.player.seek(entry.start)
        self._user_moved()

    def previous_entry(self) -> None:
        index = self._current_index()
        # Como nos players comuns: no meio do capitulo, volta ao inicio dele.
        if index >= 0 and self.player.position - self.entries[index].start > 3:
            self.player.seek(self.entries[index].start)
        elif index > 0:
            self._go_to(index - 1)
        else:
            self.player.seek(0.0)
        self._user_moved()

    def next_entry(self) -> None:
        index = self._current_index()
        if 0 <= index < len(self.entries) - 1:
            self._go_to(index + 1)

    def _on_entry_chosen(self, index: int) -> None:
        if 0 <= index < len(self.entries):
            self._go_to(index)

    def _on_slider_released(self) -> None:
        self._seeking = False
        self.player.seek(self.slider.value() / 10)
        self._user_moved()

    def _on_speed_changed(self, _index: int) -> None:
        speed = self.speed_combo.currentData()
        self.player.set_speed(speed)
        self.on_setting("player_speed", speed)

    def _tick(self) -> None:
        position, duration = self.player.position, self.player.duration
        playing = self.player.playing
        self.play_button.setText("Pausar" if playing else "Tocar")
        self.play_button.setIcon(
            self.style().standardIcon(
                QStyle.StandardPixmap.SP_MediaPause if playing else QStyle.StandardPixmap.SP_MediaPlay
            )
        )
        if playing:
            position = self._check_loop(position)
        self._check_sleep(playing)
        if not self._seeking:
            self.slider.setValue(int(position * 10))
        self.time_label.setText(f"{format_time(position)} / {format_time(duration)}")
        index = self._current_index()
        if 0 <= index < self.entry_combo.count() and not self.entry_combo.view().isVisible():
            self.entry_combo.setCurrentIndex(index)
        if self.player.error and self.player.error != self._shown_error:
            self._shown_error = self.player.error
            self._show_error(self.player.error)
        if self.player.finished and not playing:
            self._on_finished()
        self._update_tools_status()

    def _on_finished(self) -> None:
        self.player.finished = False
        file = self.player.info.path
        if self._loop_ready() and self.loop_file == file:
            # B ficou no fim do arquivo: o audio acabou antes do tique que voltaria ao A.
            self.player.seek(self.loop_a)
            self._play()
            return
        save_position(file, 0, 0.0)  # terminou: a proxima vez comeca do inicio
        stop_here = self.sleep_combo.currentData() == END_OF_CHAPTER
        if stop_here:
            self._stop_by_sleep("Pausado no fim do capítulo.")
        if file in self.files and self.files.index(file) + 1 < len(self.files):
            if self._open_file(self.files[self.files.index(file) + 1], at=0.0) and not stop_here:
                self._play()
        else:
            self.player.seek(0.0)

    def _user_moved(self) -> None:
        """Mudanca de lugar feita pela pessoa: o fim de capitulo passa a contar a partir dali."""
        if self.sleep_combo.currentData() == END_OF_CHAPTER:
            self._sleep_index = self._current_index()
        self._tick()

    # ---- marcadores -------------------------------------------------------------------

    def add_bookmark(self) -> None:
        if not self.player.info:
            return
        file, seconds = self.player.info.path, self.player.position
        name, ok = QInputDialog.getText(
            self, "Marcar este ponto", f"Nome do marcador em {format_time(seconds)} (opcional):"
        )
        if not ok:
            return
        marks = load_bookmarks(file) + [(seconds, name.strip())]
        save_bookmarks(file, marks)
        self._refresh_bookmarks()
        added = Bookmark(file, round(seconds, 1), name.strip())
        if added in self.bookmarks:
            self.bookmark_list.setCurrentRow(self.bookmarks.index(added))
        log.info("Marcador em %s de %s.", format_time(seconds), file.name)

    def remove_bookmark(self) -> None:
        row = self.bookmark_list.currentRow()
        if not 0 <= row < len(self.bookmarks):
            return
        mark = self.bookmarks[row]
        save_bookmarks(
            mark.file, [(s, n) for s, n in load_bookmarks(mark.file) if (round(s, 1), n) != (mark.seconds, mark.name)]
        )
        self._refresh_bookmarks()

    def go_to_bookmark(self, mark: Bookmark) -> None:
        if not self.player.info:
            return
        if mark.file != self.player.info.path:
            playing = self.player.playing
            self._remember()
            if not self._open_file(mark.file, at=mark.seconds):
                return
            if playing:
                self._play()
        else:
            self.player.seek(mark.seconds)
        self._user_moved()

    def _on_bookmark_clicked(self, item: QListWidgetItem) -> None:
        row = self.bookmark_list.row(item)
        if 0 <= row < len(self.bookmarks):
            self.go_to_bookmark(self.bookmarks[row])

    def _refresh_bookmarks(self) -> None:
        """Mostra os marcadores do arquivo aberto ou de todos os arquivos da pasta."""
        self.bookmarks = [
            Bookmark(file, round(seconds, 1), name) for file in self.files for seconds, name in load_bookmarks(file)
        ]
        self.bookmark_list.clear()
        for mark in self.bookmarks:
            where = f"{mark.file.stem} · " if len(self.files) > 1 else ""
            label = f"{where}{format_time(mark.seconds)}" + (f" — {mark.name}" if mark.name else "")
            self.bookmark_list.addItem(label)
        if not self.bookmarks:
            placeholder = QListWidgetItem("Nenhum marcador. Use “Marcar este ponto” durante a reprodução.")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.bookmark_list.addItem(placeholder)
        self._update_bookmark_buttons()

    def _update_bookmark_buttons(self) -> None:
        row = self.bookmark_list.currentRow()
        self.remove_bookmark_button.setEnabled(0 <= row < len(self.bookmarks))

    # ---- repeticao A-B ----------------------------------------------------------------

    def _loop_ready(self) -> bool:
        return self.loop_a is not None and self.loop_b is not None

    def toggle_loop(self) -> None:
        """1o clique marca o inicio (A), 2o o fim (B), 3o para de repetir."""
        if not self.player.info:
            return
        position = self.player.position
        self._loop_note = ""
        if self._loop_ready():
            self.clear_loop()
        elif self.loop_a is None:
            self.loop_file, self.loop_a = self.player.info.path, position
            self.loop_button.setText("Marcar fim (B)")
        elif abs(position - self.loop_a) < MIN_LOOP_SECONDS:
            self._loop_note = "O trecho precisa ter pelo menos meio segundo: deixe tocar um pouco e marque o fim"
        else:
            # Quem voltou antes do A e marcou o B ali quis o trecho entre os dois.
            self.loop_a, self.loop_b = sorted((self.loop_a, position))
            self.loop_button.setText("Parar repetição")
            log.info("Repetindo %s–%s.", format_time(self.loop_a), format_time(self.loop_b))
        self._tick()

    def clear_loop(self) -> None:
        self.loop_file = self.loop_a = self.loop_b = None
        self.loop_button.setText("Repetir trecho (A–B)")

    def _check_loop(self, position: float) -> float:
        if self._loop_ready() and self.player.info.path == self.loop_file and position >= self.loop_b:
            self.player.seek(self.loop_a)
            return self.loop_a
        return position

    # ---- timer de desligar ------------------------------------------------------------

    def _on_sleep_changed(self, _index: int) -> None:
        choice = self.sleep_combo.currentData()
        self._sleep_note = ""
        self._sleep_deadline = self.clock() + choice * 60 if isinstance(choice, int) else None
        self._sleep_index = self._current_index()
        self._update_tools_status()

    def _check_sleep(self, playing: bool) -> None:
        choice = self.sleep_combo.currentData()
        if self._sleep_deadline is not None and self.clock() >= self._sleep_deadline:
            self._stop_by_sleep("Pausado pelo timer.")
        elif choice == END_OF_CHAPTER and playing and len(self.files) <= 1 and self.entries:
            index = self._current_index()
            if index != self._sleep_index:
                # Para no comeco do proximo capitulo, que fica pronto para continuar.
                self.player.seek(self.entries[index].start)
                self._stop_by_sleep("Pausado no fim do capítulo.")

    def _stop_by_sleep(self, note: str) -> None:
        # Desarma antes de pausar: a pausa atualiza a tela e voltaria a conferir o timer.
        self.sleep_combo.blockSignals(True)
        self.sleep_combo.setCurrentIndex(0)
        self.sleep_combo.blockSignals(False)
        self._sleep_deadline = None
        self._sleep_note = note
        self.pause()
        log.info("Player: %s", note)

    def _update_tools_status(self) -> None:
        parts = []
        if self._loop_ready():
            parts.append(f"Repetindo {format_time(self.loop_a)} – {format_time(self.loop_b)}")
        elif self.loop_a is not None:
            parts.append(self._loop_note or f"Início do trecho em {format_time(self.loop_a)}; clique de novo no fim")
        if self._sleep_deadline is not None:
            parts.append(f"Desliga em {format_time(max(0.0, self._sleep_deadline - self.clock()))}")
        elif self.sleep_combo.currentData() == END_OF_CHAPTER:
            parts.append("Desliga no fim do capítulo")
        elif self._sleep_note:
            parts.append(self._sleep_note)
        text = " · ".join(parts)
        if text != self.tools_status.text():
            self.tools_status.setText(text)

    def _remember(self) -> None:
        if self.player.info:
            save_position(self.player.info.path, 0, self.player.position)

    def shutdown(self) -> None:
        self.timer.stop()
        self._remember()
        self.player.shutdown()
