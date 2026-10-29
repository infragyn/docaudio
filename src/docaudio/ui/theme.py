"""Tema visual: estilo Fusion com paleta clara ou escura conforme o Windows.

Widgets marcam seu papel com a propriedade dinamica "role" (ex.: "primary",
"warning", "error", "muted", "title"), estilizada aqui; assim as cores ficam
num lugar so e seguem o tema.
"""

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen, QStyleFactory, QWidget


@dataclass(frozen=True)
class Tokens:
    window: str
    surface: str
    surface_alt: str
    border: str
    text: str
    muted: str
    accent: str
    accent_hover: str
    accent_text: str
    selection: str
    warning_bg: str
    warning_text: str
    error_bg: str
    error_text: str
    ocr_highlight: str


LIGHT = Tokens(
    window="#f3f5f8", surface="#ffffff", surface_alt="#f8f9fb", border="#d9dee5", text="#1f2328",
    muted="#5f6b7a", accent="#2563eb", accent_hover="#1d4ed8", accent_text="#ffffff", selection="#dbe7fd",
    warning_bg="#fff4ce", warning_text="#5c4400", error_bg="#fde2e1", error_text="#7a1c16",
    ocr_highlight="#fff1b8",
)
DARK = Tokens(
    window="#1b1d21", surface="#24272c", surface_alt="#2b2f35", border="#3a3f47", text="#e6e8eb",
    muted="#9aa4b1", accent="#4f8cff", accent_hover="#6a9dff", accent_text="#0b1220", selection="#2c4370",
    warning_bg="#4a3b0f", warning_text="#ffe08a", error_bg="#4d1f1c", error_text="#ffb4ab",
    ocr_highlight="#5a4a12",
)

_current = LIGHT


def tokens() -> Tokens:
    return _current


def is_dark(app: QApplication) -> bool:
    try:
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


def _palette(t: Tokens) -> QPalette:
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: t.window,
        QPalette.ColorRole.WindowText: t.text,
        QPalette.ColorRole.Base: t.surface,
        QPalette.ColorRole.AlternateBase: t.surface_alt,
        QPalette.ColorRole.Text: t.text,
        QPalette.ColorRole.Button: t.surface,
        QPalette.ColorRole.ButtonText: t.text,
        QPalette.ColorRole.Highlight: t.accent,
        QPalette.ColorRole.HighlightedText: t.accent_text,
        QPalette.ColorRole.ToolTipBase: t.surface,
        QPalette.ColorRole.ToolTipText: t.text,
        QPalette.ColorRole.PlaceholderText: t.muted,
        QPalette.ColorRole.Link: t.accent,
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(t.muted))
    return palette


def _arrow_images(t: Tokens) -> dict[str, str]:
    """Setas das caixas numericas, desenhadas nas cores do tema (o QSS so aceita arquivos)."""
    from docaudio.core import paths

    folder = paths.temp_dir() / "tema"
    folder.mkdir(parents=True, exist_ok=True)
    images = {}
    for name, color, pointing_up in (
        ("up", t.muted, True), ("down", t.muted, False), ("up_off", t.border, True), ("down_off", t.border, False)
    ):
        pixmap = QPixmap(18, 18)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        tip, base = (4.0, 13.0) if pointing_up else (14.0, 5.0)
        path = QPainterPath(QPointF(9, tip))
        path.lineTo(QPointF(2, base))
        path.lineTo(QPointF(16, base))
        path.closeSubpath()
        painter.drawPath(path)
        painter.end()
        file = folder / f"seta_{name}_{color.lstrip('#')}.png"
        pixmap.save(str(file))
        images[name] = file.as_posix()
    return images


def stylesheet(t: Tokens) -> str:
    arrows = _arrow_images(t)
    return f"""
    QWidget {{ font-size: 10pt; }}
    QMainWindow, QDialog {{ background: {t.window}; }}
    QGroupBox {{
        background: {t.surface}; border: 1px solid {t.border}; border-radius: 10px;
        margin-top: 14px; padding: 12px 10px 10px 10px; font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {t.text};
    }}
    QGroupBox QLabel, QGroupBox QCheckBox {{ font-weight: normal; }}
    QPushButton {{
        background: {t.surface_alt}; border: 1px solid {t.border}; border-radius: 6px;
        padding: 6px 12px; color: {t.text};
    }}
    QPushButton:hover {{ border-color: {t.accent}; }}
    QPushButton:pressed {{ background: {t.selection}; }}
    QPushButton:disabled {{ color: {t.muted}; background: {t.window}; }}
    QPushButton[role="primary"] {{
        background: {t.accent}; color: {t.accent_text}; border: 1px solid {t.accent};
        font-weight: 600; padding: 8px 18px;
    }}
    QPushButton[role="primary"]:hover {{ background: {t.accent_hover}; }}
    QPushButton[role="primary"]:disabled {{ background: {t.border}; border-color: {t.border}; color: {t.muted}; }}
    QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QListWidget, QTextEdit {{
        background: {t.surface}; border: 1px solid {t.border}; border-radius: 6px; padding: 4px 6px;
        selection-background-color: {t.accent}; selection-color: {t.accent_text};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus, QListWidget:focus {{
        border-color: {t.accent};
    }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QSpinBox {{ padding-right: 20px; }}
    QSpinBox::up-button, QSpinBox::down-button {{
        subcontrol-origin: border; width: 18px; border: none; background: transparent;
    }}
    QSpinBox::up-button {{ subcontrol-position: top right; }}
    QSpinBox::down-button {{ subcontrol-position: bottom right; }}
    QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {t.selection}; }}
    QSpinBox::up-arrow {{ image: url("{arrows['up']}"); width: 9px; height: 9px; }}
    QSpinBox::down-arrow {{ image: url("{arrows['down']}"); width: 9px; height: 9px; }}
    QSpinBox::up-arrow:disabled, QSpinBox::up-arrow:off {{ image: url("{arrows['up_off']}"); }}
    QSpinBox::down-arrow:disabled, QSpinBox::down-arrow:off {{ image: url("{arrows['down_off']}"); }}
    QListWidget {{ padding: 4px; outline: none; }}
    QListWidget::item {{ padding: 5px 6px; border-radius: 5px; }}
    QListWidget::item:selected {{ background: {t.selection}; color: {t.text}; }}
    QListWidget::item:hover:!selected {{ background: {t.surface_alt}; }}
    QProgressBar {{
        background: {t.surface_alt}; border: 1px solid {t.border}; border-radius: 6px; height: 10px;
    }}
    QProgressBar::chunk {{ background: {t.accent}; border-radius: 5px; }}
    QSlider::groove:horizontal {{ height: 4px; background: {t.border}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {t.accent}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        background: {t.surface}; border: 2px solid {t.accent}; width: 14px; height: 14px;
        margin: -6px 0; border-radius: 8px;
    }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle {{ background: {t.border}; border-radius: 3px; min-height: 24px; min-width: 24px; }}
    QScrollBar::handle:hover {{ background: {t.muted}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QTabWidget::pane {{
        background: {t.surface}; border: 1px solid {t.border}; border-radius: 10px; top: -1px;
    }}
    QTabWidget > QWidget > QWidget {{ background: {t.surface}; }}
    QTabBar::tab {{
        background: transparent; color: {t.muted}; padding: 6px 12px; margin-right: 2px;
        border: 1px solid transparent; border-top-left-radius: 6px; border-top-right-radius: 6px;
    }}
    QTabBar::tab:selected {{
        background: {t.surface}; color: {t.text}; font-weight: 600;
        border-color: {t.border}; border-bottom-color: {t.surface};
    }}
    QTabBar::tab:hover:!selected {{ color: {t.text}; }}
    QSplitter::handle {{ background: transparent; }}
    QSplitter::handle:hover {{ background: {t.selection}; }}
    QScrollArea {{ border: none; background: transparent; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QStatusBar {{ background: {t.surface}; border-top: 1px solid {t.border}; color: {t.muted}; }}
    QMenuBar {{ background: {t.surface}; border-bottom: 1px solid {t.border}; }}
    QMenuBar::item {{ padding: 4px 10px; border-radius: 4px; }}
    QMenuBar::item:selected {{ background: {t.selection}; }}
    QMenu {{ background: {t.surface}; border: 1px solid {t.border}; padding: 4px; }}
    QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {t.selection}; color: {t.text}; }}
    QToolTip {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border}; padding: 4px; }}
    QLabel[role="warning"] {{
        background: {t.warning_bg}; color: {t.warning_text}; border-radius: 8px; padding: 8px 10px;
    }}
    QLabel[role="error"] {{
        background: {t.error_bg}; color: {t.error_text}; border-radius: 8px; padding: 8px 10px;
    }}
    QLabel[role="muted"] {{ color: {t.muted}; }}
    QLabel[role="title"] {{ font-size: 13pt; font-weight: 600; }}
    """


def set_role(widget: QWidget, role: str) -> None:
    """Muda o papel visual de um widget e reaplica o estilo."""
    widget.setProperty("role", role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def app_icon() -> QIcon:
    """Icone do DocAudio desenhado em codigo: folha de documento com ondas sonoras."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = size / 64
        painter.setBrush(QColor("#2563eb"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(4 * s, 4 * s, 56 * s, 56 * s), 14 * s, 14 * s)
        page = QPainterPath()
        page.addRoundedRect(QRectF(15 * s, 12 * s, 24 * s, 34 * s), 3 * s, 3 * s)
        painter.setBrush(QColor("#ffffff"))
        painter.drawPath(page)
        painter.setPen(QPen(QColor("#2563eb"), max(1.0, 2.2 * s), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for y in (20, 26, 32):
            painter.drawLine(QPointF(20 * s, y * s), QPointF(34 * s, y * s))
        painter.setPen(QPen(QColor("#ffffff"), max(1.0, 3 * s), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for radius in (8, 14):
            painter.drawArc(QRectF((40 - radius) * s, (38 - radius) * s, 2 * radius * s, 2 * radius * s), -45 * 16, 90 * 16)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def splash_screen() -> QSplashScreen:
    """Tela de abertura desenhada com as cores do tema (chame depois de `apply`)."""
    t = _current
    pixmap = QPixmap(420, 160)
    pixmap.fill(QColor(t.border))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.fillRect(QRectF(1, 1, 418, 158), QColor(t.surface))
    painter.drawPixmap(28, 40, app_icon().pixmap(64, 64))
    painter.setPen(QColor(t.text))
    painter.setFont(QFont("Segoe UI", 18, QFont.Weight.DemiBold))
    painter.drawText(QRectF(110, 38, 290, 36), Qt.AlignmentFlag.AlignVCenter, "DocAudio")
    painter.setPen(QColor(t.muted))
    painter.setFont(QFont("Segoe UI", 10))
    painter.drawText(QRectF(110, 74, 290, 24), Qt.AlignmentFlag.AlignVCenter, "Documentos para áudio")
    painter.drawText(QRectF(110, 110, 290, 24), Qt.AlignmentFlag.AlignVCenter, "Carregando vozes…")
    painter.end()
    return QSplashScreen(pixmap)


def apply(app: QApplication) -> Tokens:
    global _current
    _current = DARK if is_dark(app) else LIGHT
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setPalette(_palette(_current))
    app.setStyleSheet(stylesheet(_current))
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    app.setWindowIcon(app_icon())
    return _current
