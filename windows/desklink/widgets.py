"""Small shared widgets and the pairing dialogs."""
from __future__ import annotations

import re

import segno
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout,
                               QWidget)

from . import winsys


def soft_wrap(text: str, limit: int) -> str:
    """Trim for display and let very long words (URLs, hashes) wrap instead of widening the window."""
    shown = text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
    return re.sub(r"(\S{22})", "\\1\u200b", shown)


def label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    lbl.setTextFormat(Qt.TextFormat.PlainText)
    if role:
        lbl.setProperty("role", role)
    if wrap:
        lbl.setWordWrap(True)
        lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return lbl


def button(text: str, kind: str | None = None) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if kind:
        btn.setProperty("kind", kind)
    return btn


def flash(btn: QPushButton, text: str = "Copied ✓", ms: int = 1300) -> None:
    original = btn.property("orig_text") or btn.text()
    btn.setProperty("orig_text", original)
    btn.setText(text)
    QTimer.singleShot(ms, btn, lambda: btn.setText(original))  # btn as context: skipped if btn is deleted


class Card(QFrame):
    def __init__(self, title: str | None = None):
        super().__init__()
        self.setObjectName("card")
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(16, 14, 16, 14)
        self.box.setSpacing(8)
        if title:
            self.box.addWidget(label(title.upper(), "section"))


def qr_pixmap(data: str, module_px: int = 6) -> QPixmap:
    matrix = segno.make(data, error="m").matrix
    n, border = len(matrix), 4
    size = (n + 2 * border) * module_px
    img = QImage(size, size, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    black = QColor("#111111")
    for y, row in enumerate(matrix):
        for x, val in enumerate(row):
            if val & 1:
                p.fillRect((x + border) * module_px, (y + border) * module_px, module_px, module_px, black)
    p.end()
    return QPixmap.fromImage(img)


class PairDialog(QDialog):
    copy_link = Signal()
    new_key = Signal()
    fix_firewall = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pair your phone")
        self.setFixedWidth(440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(10)
        lay.addWidget(label("Pair your phone", "title"))
        steps = Card()
        steps.box.addWidget(label("1.  Connect the phone to the same Wi-Fi as this PC.\n"
                                  "2.  Open DeskLink on the phone and tap “Find my PC”.\n"
                                  "3.  Check the code matches, then click Allow.", wrap=True))
        lay.addWidget(steps)
        self.status = label("Waiting for your phone…  (keep this window open)", "muted", wrap=True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.status)
        self.details = label("", "muted", wrap=True)
        self.details.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.details)

        other = Card("Other way")
        row = QHBoxLayout()
        self.qr = QLabel()
        self.qr.setStyleSheet("background: white; border-radius: 8px; padding: 4px;")
        row.addWidget(self.qr, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.addWidget(label("Scan this with Google Lens (or your camera), copy the text, then on the phone tap "
                            "“Paste pairing link”. Or send yourself the link:", "muted", wrap=True))
        copy = button("Copy pairing link")
        copy.clicked.connect(lambda: (self.copy_link.emit(), flash(copy)))
        col.addWidget(copy, 0, Qt.AlignmentFlag.AlignLeft)
        col.addStretch(1)
        row.addLayout(col, 1)
        other.box.addLayout(row)
        lay.addWidget(other)

        help_card = Card("Phone can't find the PC?")
        help_card.box.addWidget(label(
            "• Same Wi-Fi on both (guest networks often block devices from seeing each other).\n"
            "• Windows: Settings → Network & internet → Wi-Fi → your network → set it to Private.\n"
            "• Allow DeskLink through Windows Firewall:", wrap=True))
        fw = button("Allow through firewall (asks for admin)")
        fw.clicked.connect(self.fix_firewall)
        fw.setEnabled(winsys.IS_WINDOWS)
        help_card.box.addWidget(fw, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(help_card)

        bottom = QHBoxLayout()
        reset = button("New key", "ghost")
        reset.setToolTip("Creates a new pairing key. Phones paired before will need to pair again.")
        reset.clicked.connect(self.new_key)
        bottom.addWidget(reset)
        bottom.addStretch(1)
        done = button("Done", "primary")
        done.clicked.connect(self.accept)
        bottom.addWidget(done)
        lay.addLayout(bottom)

    def set_pairing(self, uri: str, name: str, hosts: list[str], port: int) -> None:
        self.qr.setPixmap(qr_pixmap(uri, 3))
        where = ", ".join(hosts) if hosts else "no network found"
        self.details.setText(f"{name} · {where} · port {port}")
        self.set_status("Waiting for your phone…  (keep this window open)", ok=False)

    def set_status(self, text: str, ok: bool) -> None:
        self.status.setText(text)
        self.status.setProperty("role", "" if ok else "muted")
        self.status.setStyleSheet("font-weight: 600; color: #10B981;" if ok else "")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def show_success(self, device: str) -> None:
        self.set_status(f"✓ {device} is connected — you can close this window", ok=True)


class PairRequestDialog(QDialog):
    """“Galaxy A54 wants to pair — code 482 193 — Allow?”"""

    def __init__(self, parent, device: str, code: str):
        super().__init__(parent)
        self.setWindowTitle("Pairing request")
        self.setFixedWidth(380)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(10)
        lay.addWidget(label(f"Pair with {device}?", "title"))
        lay.addWidget(label("Your phone is showing a code. Allow only if it's the same:", "muted", wrap=True))
        code_lbl = label(f"{code[:3]} {code[3:]}")
        code_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        code_lbl.setStyleSheet("font-size: 30pt; font-weight: 700; letter-spacing: 4px; padding: 8px;")
        lay.addWidget(code_lbl)
        row = QHBoxLayout()
        row.addStretch(1)
        deny = button("Deny")
        deny.clicked.connect(self.reject)
        allow = button("Allow", "primary")
        allow.setMinimumWidth(110)
        allow.clicked.connect(self.accept)
        row.addWidget(deny)
        row.addWidget(allow)
        lay.addLayout(row)


class LabeledSlider(QWidget):
    """A slider with its name on the left and value on the right. Double-click the name to reset to 0."""
    changed = Signal()

    def __init__(self, title: str, lo: int, hi: int, suffix: str = "", divisor: float = 1.0):
        super().__init__()
        self.divisor, self.suffix = divisor, suffix
        self.value_lbl = label("", "muted")
        self.value_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.valueChanged.connect(self._on_change)
        top = QHBoxLayout()
        top.addWidget(label(title))
        top.addWidget(self.value_lbl)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addLayout(top)
        lay.addWidget(self.slider)
        self._show_value()

    def _show_value(self) -> None:
        v = self.slider.value() / self.divisor
        self.value_lbl.setText((f"{v:+.1f}" if self.divisor != 1 else f"{int(v):+d}") + self.suffix)

    def _on_change(self) -> None:
        self._show_value()
        self.changed.emit()

    def value(self) -> float:
        return self.slider.value() / self.divisor

    def set_value(self, v: float, silent: bool = False) -> None:
        self.slider.blockSignals(silent)
        self.slider.setValue(round(v * self.divisor))
        self.slider.blockSignals(False)
        self._show_value()

    def mouseDoubleClickEvent(self, _e) -> None:
        self.slider.setValue(0)
