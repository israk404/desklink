"""Colors, stylesheet and the painted app/tray icons (no image files needed at runtime)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QGuiApplication, QIcon, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap)

LIGHT = {
    "bg": "#F4F5F8", "card": "#FFFFFF", "border": "#E3E6EC", "text": "#14171F", "muted": "#687082",
    "btn": "#F1F3F7", "btn_hover": "#E6E9F0", "accent": "#0F766E", "accent_hover": "#115E59",
    "accent_text": "#0F766E", "pc": "#4F46E5", "pc_bg": "#EEF0FF", "phone": "#047857", "phone_bg": "#E3F6EE",
    "ok": "#10B981", "off": "#A0A7B4", "warn": "#D97706", "track": "#D5D9E2", "disabled": "#E6E8EE",
    "toast": "#FFFFFF",
}
DARK = {
    "bg": "#0F1116", "card": "#181B22", "border": "#272B35", "text": "#E7E9EE", "muted": "#99A1B2",
    "btn": "#222631", "btn_hover": "#2B303C", "accent": "#0D9488", "accent_hover": "#14B8A6",
    "accent_text": "#5EEAD4", "pc": "#A5A8FF", "pc_bg": "#262A4A", "phone": "#6EE7B7", "phone_bg": "#173328",
    "ok": "#34D399", "off": "#5B6272", "warn": "#FBBF24", "track": "#3A3F4C", "disabled": "#262A33",
    "toast": "#1E222B",
}


def is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False


def palette() -> dict:
    return DARK if is_dark() else LIGHT


def stylesheet(p: dict) -> str:
    return f"""
    QWidget#root, QDialog {{ background: {p['bg']}; }}
    QWidget {{ color: {p['text']}; }}
    QLabel {{ background: transparent; }}
    QLabel[role="muted"] {{ color: {p['muted']}; }}
    QLabel[role="title"] {{ font-size: 15pt; font-weight: 600; }}
    QLabel[role="section"] {{ color: {p['muted']}; font-size: 9pt; font-weight: 600; letter-spacing: 0.5px; }}
    QLabel[role="clip"] {{ font-size: 10.5pt; }}
    QLabel[role="empty"] {{ color: {p['muted']}; font-style: italic; }}
    QFrame#card {{ background: {p['card']}; border: 1px solid {p['border']}; border-radius: 12px; }}
    QFrame#row {{ background: {p['card']}; border: 1px solid {p['border']}; border-radius: 10px; }}
    QFrame#toast {{ background: {p['toast']}; border: 1px solid {p['border']}; border-radius: 14px; }}
    QLabel#pill {{ border-radius: 11px; padding: 2px 10px; background: {p['btn']}; }}
    QLabel[badge="pc"] {{ background: {p['pc_bg']}; color: {p['pc']}; border-radius: 6px;
                          padding: 1px 6px; font-size: 8pt; font-weight: 700; }}
    QLabel[badge="phone"] {{ background: {p['phone_bg']}; color: {p['phone']}; border-radius: 6px;
                             padding: 1px 6px; font-size: 8pt; font-weight: 700; }}
    QPushButton {{ background: {p['btn']}; border: 1px solid {p['border']}; border-radius: 8px;
                   padding: 6px 12px; }}
    QPushButton:hover {{ background: {p['btn_hover']}; }}
    QPushButton:disabled {{ color: {p['muted']}; }}
    QPushButton[kind="primary"] {{ background: {p['accent']}; color: white; border: none; font-weight: 600; }}
    QPushButton[kind="primary"]:hover {{ background: {p['accent_hover']}; }}
    QPushButton[kind="primary"]:disabled {{ background: {p['disabled']}; color: {p['muted']}; }}
    QPushButton[kind="ghost"] {{ background: transparent; border: none; color: {p['accent_text']};
                                 padding: 4px 8px; font-weight: 600; }}
    QPushButton[kind="ghost"]:hover {{ background: {p['btn']}; }}
    QPushButton[kind="ghost"]:disabled {{ color: {p['muted']}; }}
    QPlainTextEdit {{ background: {p['bg']}; border: 1px solid {p['border']}; border-radius: 10px; padding: 6px 8px;
                      selection-background-color: {p['accent']}; selection-color: white; }}
    QPlainTextEdit:focus {{ border: 1px solid {p['accent']}; }}
    QProgressBar {{ background: {p['track']}; border: none; border-radius: 3px; }}
    QProgressBar::chunk {{ background: {p['accent']}; border-radius: 3px; }}
    QComboBox {{ background: {p['btn']}; border: 1px solid {p['border']}; border-radius: 8px; padding: 4px 10px; }}
    QComboBox QAbstractItemView {{ background: {p['card']}; border: 1px solid {p['border']};
                                   selection-background-color: {p['accent']}; selection-color: white; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {p['track']}; border-radius: 4px; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QMenu {{ background: {p['card']}; border: 1px solid {p['border']}; padding: 4px; }}
    QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {p['accent']}; color: white; }}
    QMenu::separator {{ height: 1px; background: {p['border']}; margin: 4px 8px; }}
    QFrame#topbar {{ background: {p['card']}; border: none; border-bottom: 1px solid {p['border']}; }}
    QFrame#drop {{ background: {p['card']}; border: 2px dashed {p['track']}; border-radius: 16px; }}
    QFrame#drop[active="true"] {{ border-color: {p['accent']}; }}
    QPushButton[kind="mode"] {{ background: transparent; border: none; border-radius: 10px; padding: 8px 18px;
                                font-size: 10.5pt; font-weight: 600; color: {p['muted']}; }}
    QPushButton[kind="mode"]:hover {{ background: {p['btn']}; }}
    QPushButton[kind="mode"]:checked {{ background: {p['accent']}; color: white; }}
    QPushButton[kind="big"] {{ background: {p['accent']}; color: white; border: none; font-weight: 600;
                               font-size: 11pt; padding: 11px 16px; border-radius: 10px; }}
    QPushButton[kind="big"]:hover {{ background: {p['accent_hover']}; }}
    QPushButton[kind="big"]:disabled {{ background: {p['disabled']}; color: {p['muted']}; }}
    QSlider::groove:horizontal {{ height: 6px; background: {p['track']}; border-radius: 3px; }}
    QSlider::sub-page:horizontal {{ background: {p['track']}; border-radius: 3px; }}
    QSlider::handle:horizontal {{ width: 16px; margin: -6px 0; border-radius: 8px; background: {p['accent']}; }}
    QSlider::handle:horizontal:disabled {{ background: {p['off']}; }}
    QCheckBox {{ spacing: 8px; }}
    QLabel[role="status"] {{ color: {p['accent_text']}; font-weight: 600; }}
    QLabel[role="big"] {{ font-size: 13pt; font-weight: 600; }}
    QToolTip {{ background: {p['card']}; color: {p['text']}; border: 1px solid {p['border']}; }}
    """


def paint_logo(painter: QPainter, rect: QRectF) -> None:
    """DeskLink mark: a sheet with two-way arrows on a teal→blue tile."""
    s = rect.width() / 100.0
    painter.save()
    painter.translate(rect.topLeft())
    painter.scale(s, s)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    grad = QLinearGradient(QPointF(0, 0), QPointF(100, 100))
    grad.setColorAt(0, QColor("#0D9488"))
    grad.setColorAt(1, QColor("#2563EB"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(grad))
    painter.drawRoundedRect(QRectF(4, 4, 92, 92), 24, 24)
    painter.setBrush(QColor("white"))
    painter.drawRoundedRect(QRectF(25, 22, 50, 62), 9, 9)
    painter.setBrush(QColor("#99F6E4"))
    painter.drawRoundedRect(QRectF(37, 15, 26, 13), 5, 5)
    pen = QPen(QColor("#0F766E"), 6.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for y, left_to_right in ((44, True), (63, False)):
        a, b = (35, 64) if left_to_right else (65, 36)
        painter.drawLine(QPointF(a, y), QPointF(b, y))
        d = -8 if left_to_right else 8
        path = QPainterPath(QPointF(b + d, y - 8))
        path.lineTo(QPointF(b, y))
        path.lineTo(QPointF(b + d, y + 8))
        painter.drawPath(path)
    painter.restore()


def logo_pixmap(size: int, status: str | None = None) -> QPixmap:
    """status: None, "on" (green dot) or "off" (grey dot) — used for the tray icon."""
    dpr = 2.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    paint_logo(p, QRectF(0, 0, size, size))
    if status:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = size * 0.36
        p.setPen(QPen(QColor("white"), size * 0.07))
        p.setBrush(QColor("#10B981" if status == "on" else "#9AA1AE"))
        p.drawEllipse(QRectF(size - r - 0.5, size - r - 0.5, r, r))
    p.end()
    return pm


def app_icon(status: str | None = None) -> QIcon:
    icon = QIcon()
    for sz in (16, 20, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(logo_pixmap(sz, status))
    return icon


def dot_pixmap(color: str, size: int = 10) -> QPixmap:
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(1, 1, size - 2, size - 2))
    p.end()
    return pm
