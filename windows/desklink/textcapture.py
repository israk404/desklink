"""Text Capture mode: a photo arrives from the phone (or is dropped / pasted), its text is read,
and clicking or dragging over the words copies them."""
from __future__ import annotations

import os

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QPlainTextEdit, QVBoxLayout, QWidget

from . import imaging, ocr
from .widgets import button, flash, label

DISPLAY_MAX = 2400


def join_words(words: list[dict]) -> str:
    """Words in reading order → text, with a new line wherever the photo's line changes."""
    parts: list[str] = []
    last_line = None
    for w in words:
        if last_line is not None:
            parts.append("\n" if w["line"] != last_line else " ")
        parts.append(w["text"])
        last_line = w["line"]
    return "".join(parts)


class OcrCanvas(QWidget):
    """The photo with a box on every word. Click = that word, drag = from the first word to the last,
    double-click = the whole line."""
    picked = Signal(str)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(420, 380)
        self.setMouseTracking(True)
        self.pixmap: QPixmap | None = None
        self.img_w = self.img_h = 1
        self.words: list[dict] = []     # reading order; each has text, x, y, w, h, line
        self.anchor: int | None = None
        self.cursor_idx: int | None = None
        self._dragging = False
        self.message = "Take a photo with your phone, or drop / paste a picture here"

    def set_image(self, pixmap: QPixmap | None, w: int = 1, h: int = 1) -> None:
        self.pixmap, self.img_w, self.img_h = pixmap, max(1, w), max(1, h)
        self.set_words([])

    def set_words(self, words: list[dict]) -> None:
        self.words = words
        self.anchor = self.cursor_idx = None
        self.update()

    def selection(self) -> list[dict]:
        if self.anchor is None or self.cursor_idx is None:
            return []
        a, b = sorted((self.anchor, self.cursor_idx))
        return self.words[a:b + 1]

    def select_all(self) -> None:
        if self.words:
            self.anchor, self.cursor_idx = 0, len(self.words) - 1
            self.update()

    def _geom(self) -> tuple[float, float, float]:
        m = 14
        s = min(max(1, self.width() - 2 * m) / self.img_w, max(1, self.height() - 2 * m) / self.img_h)
        return s, (self.width() - self.img_w * s) / 2.0, (self.height() - self.img_h * s) / 2.0

    def _rect(self, w: dict) -> QRectF:
        s, ox, oy = self._geom()
        return QRectF(w["x"] * s + ox, w["y"] * s + oy, w["w"] * s, w["h"] * s)

    def _word_at(self, pos: QPointF, max_dist: float | None) -> int | None:
        """Index of the word under (or nearest to) pos; None if nothing is within max_dist widget pixels."""
        best, best_d = None, 1e18
        for i, w in enumerate(self.words):
            r = self._rect(w)
            dx = max(r.left() - pos.x(), 0, pos.x() - r.right())
            dy = max(r.top() - pos.y(), 0, pos.y() - r.bottom())
            d = dx * dx + dy * dy * 4      # prefer words on the same line as the pointer
            if d < best_d:
                best, best_d = i, d
        if best is None or (max_dist is not None and best_d > max_dist * max_dist):
            return None
        return best

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(24, 26, 30))
        if self.pixmap is None:
            p.setPen(QPen(QColor(80, 86, 96), 2, Qt.PenStyle.DashLine))
            p.drawRoundedRect(self.rect().adjusted(24, 24, -24, -24), 14, 14)
            p.setPen(QColor(160, 166, 176))
            f = p.font()
            f.setPointSize(14)
            p.setFont(f)
            p.drawText(self.rect().adjusted(40, 40, -40, -40),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
            return
        s, ox, oy = self._geom()
        p.drawPixmap(QRectF(ox, oy, self.img_w * s, self.img_h * s), self.pixmap, QRectF(self.pixmap.rect()))
        chosen = {id(w) for w in self.selection()}
        for w in self.words:
            r = self._rect(w).adjusted(-1.5, -1.5, 1.5, 1.5)
            if id(w) in chosen:
                p.setPen(QPen(QColor(255, 170, 0), 1.6))
                p.setBrush(QColor(255, 170, 0, 95))
            else:
                p.setPen(QPen(QColor(60, 200, 255, 190), 1))
                p.setBrush(QColor(60, 200, 255, 28))
            p.drawRoundedRect(r, 3, 3)

    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton or not self.words:
            return
        hit = self._word_at(e.position(), 26)
        self.anchor = self.cursor_idx = hit
        self._dragging = hit is not None
        self.update()

    def mouseMoveEvent(self, e) -> None:
        if not self._dragging:
            over = self.words and self._word_at(e.position(), 6) is not None
            self.setCursor(Qt.CursorShape.IBeamCursor if over else Qt.CursorShape.ArrowCursor)
            return
        hit = self._word_at(e.position(), None)
        if hit is not None and hit != self.cursor_idx:
            self.cursor_idx = hit
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            if self.selection():
                self.picked.emit(join_words(self.selection()))

    def mouseDoubleClickEvent(self, e) -> None:
        hit = self._word_at(e.position(), 26)
        if hit is None:
            return
        line = self.words[hit]["line"]
        idxs = [i for i, w in enumerate(self.words) if w["line"] == line]
        self.anchor, self.cursor_idx = idxs[0], idxs[-1]
        self._dragging = False
        self.update()
        self.picked.emit(join_words(self.selection()))


class TextCapturePage(QWidget):
    status = Signal(str)
    request_camera = Signal()

    hint = "Click a word to copy it, drag across words to copy a stretch, double-click for a whole line."

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.img: QImage | None = None
        self._job: ocr.OcrJob | None = None
        self._job_id = 0
        self.recognize = None   # tests can swap in their own reader: fn(QImage) -> lines

        self.canvas = OcrCanvas()
        self.canvas.picked.connect(self.copy_text)
        self.btn_camera = button("📷  Open phone camera", "big")
        self.btn_open = button("Open image…")
        self.btn_paste = button("Paste image  (Ctrl+V)")
        self.btn_rotate = button("Rotate ⟳")
        self.btn_copy_all = button("Copy all text  (Ctrl+A)")
        self.info = label("", "status", wrap=True)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlaceholderText("All the text found in the picture shows up here. "
                                     "You can also select and copy from this box.")

        side = QVBoxLayout()
        side.setSpacing(8)
        side.addWidget(self.btn_camera)
        row = QHBoxLayout()
        row.addWidget(self.btn_open)
        row.addWidget(self.btn_paste)
        side.addLayout(row)
        side.addWidget(self.info)
        side.addWidget(label("TEXT IN THE PICTURE", "section"))
        side.addWidget(self.text, 1)
        row2 = QHBoxLayout()
        row2.addWidget(self.btn_rotate)
        row2.addWidget(self.btn_copy_all, 1)
        side.addLayout(row2)
        side_w = QWidget()
        side_w.setLayout(side)
        side_w.setFixedWidth(350)

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(14)
        root.addWidget(self.canvas, 1)
        root.addWidget(side_w)

        self.btn_camera.clicked.connect(self.request_camera)
        self.btn_open.clicked.connect(self.open_dialog)
        self.btn_paste.clicked.connect(self.paste)
        self.btn_rotate.clicked.connect(self.rotate)
        self.btn_copy_all.clicked.connect(self.copy_all)
        self._set_enabled(False)

    def _set_enabled(self, on: bool) -> None:
        self.btn_rotate.setEnabled(on)
        self.btn_copy_all.setEnabled(on and bool(self.canvas.words))

    def set_info(self, text: str) -> None:
        self.info.setText(text)

    # ---- getting a picture
    def open_dialog(self) -> None:
        exts = " ".join("*" + x for x in sorted(imaging.IMAGE_EXTS))
        path, _ = QFileDialog.getOpenFileName(self, "Open image", self.cfg["open_dir"], f"Images ({exts})")
        if path:
            self.load_path(path)

    def accept_paths(self, paths: list[str]) -> None:
        for p in paths:
            if os.path.splitext(p)[1].lower() in imaging.IMAGE_EXTS:
                self.load_path(p)
                return
        self.status.emit("That file isn't a picture.")

    def load_path(self, path: str) -> bool:
        img = imaging.load_image(path)
        if img is None:
            self.set_info("Could not open that picture.")
            return False
        self.load_image(img)
        return True

    def paste(self) -> bool:
        img = QGuiApplication.clipboard().image()
        if img.isNull():
            self.set_info("There is no picture on the clipboard.")
            return False
        self.load_image(img.convertToFormat(QImage.Format.Format_RGB32))
        return True

    def rotate(self) -> None:
        if self.img is not None:
            self.load_image(imaging.rotate_quarters(self.img, 1))

    def load_image(self, img: QImage) -> None:
        self.img = img
        shown, _ = imaging.scaled_to(img, DISPLAY_MAX)
        self.canvas.set_image(QPixmap.fromImage(shown), img.width(), img.height())
        self.text.setPlainText("")
        self._set_enabled(True)
        self.set_info("Reading the text…")
        self._job_id += 1
        job_id = self._job_id
        if self.recognize is not None:       # test hook, runs inline
            try:
                self._on_lines(job_id, self.recognize(img))
            except Exception as exc:
                self._on_failed(job_id, str(exc))
            return
        job = ocr.OcrJob(img, self)
        job.finished_ok.connect(lambda lines: self._on_lines(job_id, lines))
        job.failed.connect(lambda msg: self._on_failed(job_id, msg))
        job.finished.connect(job.deleteLater)
        self._job = job
        job.start()

    def _on_lines(self, job_id: int, lines: list[list[dict]]) -> None:
        if job_id != self._job_id:
            return   # a newer picture replaced this one
        words = []
        for n, line in enumerate(lines):
            for w in line:
                words.append({**w, "line": n})
        self.canvas.set_words(words)
        self.text.setPlainText(join_words(words))
        self._set_enabled(True)
        if words:
            self.set_info(f"Found {len(words)} words on {len(lines)} lines. Click or drag over them to copy.")
        else:
            self.set_info("No text found. If the picture is sideways, press Rotate.")

    def _on_failed(self, job_id: int, message: str) -> None:
        if job_id == self._job_id:
            self.set_info(message)

    # ---- copying
    def copy_text(self, text: str) -> None:
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        flat = " ".join(text.split())
        shown = flat if len(flat) <= 70 else flat[:69] + "…"
        self.set_info(f"Copied: {shown}")
        self.status.emit(f"Copied {len(text.split())} word(s) to the clipboard.")

    def copy_all(self) -> None:
        if self.canvas.words:
            self.canvas.select_all()
            self.copy_text(join_words(self.canvas.words))
            flash(self.btn_copy_all)
