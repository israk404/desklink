"""Photo Fix mode: drop a phone photo of a printed passport photo, drag the four corners, tune, save."""
from __future__ import annotations

import math
import os

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QCheckBox, QFileDialog, QHBoxLayout, QMessageBox, QVBoxLayout, QWidget

from . import imaging
from .widgets import LabeledSlider, button, label

PREVIEW_MAX = 1800   # longest side of the working copy used for the live preview


class CornerCanvas(QWidget):
    """Shows the photo with four draggable corner handles and a magnifier while dragging."""
    quad_changed = Signal()
    HANDLE_R = 9
    GRAB_R = 28

    def __init__(self):
        super().__init__()
        self.setMinimumSize(420, 380)
        self.setMouseTracking(True)
        self.pixmap: QPixmap | None = None
        self.img_w = self.img_h = 1          # full-resolution size
        self.quad: list[list[float]] = []    # full-resolution coordinates
        self._drag: int | str | None = None  # corner index, or "all"
        self._last = (0.0, 0.0)

    def set_image(self, pixmap: QPixmap, full_w: int, full_h: int, quad) -> None:
        self.pixmap = pixmap
        self.img_w, self.img_h = full_w, full_h
        self.quad = [list(p) for p in quad]
        self.update()

    def set_quad(self, quad) -> None:
        self.quad = [list(p) for p in quad]
        self.update()
        self.quad_changed.emit()

    def _geom(self) -> tuple[float, float, float]:
        m = 18
        aw, ah = max(1, self.width() - 2 * m), max(1, self.height() - 2 * m)
        s = min(aw / self.img_w, ah / self.img_h)
        return s, (self.width() - self.img_w * s) / 2.0, (self.height() - self.img_h * s) / 2.0

    def _to_widget(self, p) -> QPointF:
        s, ox, oy = self._geom()
        return QPointF(p[0] * s + ox, p[1] * s + oy)

    def _to_image(self, pos) -> tuple[float, float]:
        s, ox, oy = self._geom()
        return (min(max((pos.x() - ox) / s, 0), self.img_w - 1),
                min(max((pos.y() - oy) / s, 0), self.img_h - 1))

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
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Drop a photo here\n\nor press Ctrl+O to open one")
            return
        s, ox, oy = self._geom()
        target = QRectF(ox, oy, self.img_w * s, self.img_h * s)
        p.drawPixmap(target, self.pixmap, QRectF(self.pixmap.rect()))
        pts = [self._to_widget(q) for q in self.quad]
        poly = QPolygonF(pts)
        outer = QPainterPath()
        outer.addRect(target)
        inner = QPainterPath()
        inner.addPolygon(poly)
        inner.closeSubpath()
        p.fillPath(outer.subtracted(inner), QColor(0, 0, 0, 120))
        p.setPen(QPen(QColor(60, 200, 255), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(poly)
        for i, pt in enumerate(pts):
            p.setPen(QPen(QColor(255, 255, 255), 2))
            p.setBrush(QColor(255, 140, 0) if self._drag == i else QColor(60, 200, 255))
            p.drawEllipse(pt, self.HANDLE_R, self.HANDLE_R)
        if isinstance(self._drag, int):
            self._paint_loupe(p, self._drag)

    def _paint_loupe(self, p: QPainter, idx: int) -> None:
        size, src = 170, 46   # widget px, source px (in the preview pixmap)
        q = self.quad[idx]
        ps = self.pixmap.width() / float(self.img_w)
        src_rect = QRectF(q[0] * ps - src / 2, q[1] * ps - src / 2, src, src)
        wpt = self._to_widget(q)
        x = 14 if wpt.x() > self.width() / 2 else self.width() - size - 14
        y = 14 if wpt.y() > self.height() / 2 else self.height() - size - 14
        dst = QRectF(x, y, size, size)
        p.fillRect(dst, QColor(0, 0, 0))
        p.drawPixmap(dst, self.pixmap, src_rect)
        p.setPen(QPen(QColor(255, 140, 0), 1))
        c = dst.center()
        p.drawLine(QPointF(dst.left(), c.y()), QPointF(dst.right(), c.y()))
        p.drawLine(QPointF(c.x(), dst.top()), QPointF(c.x(), dst.bottom()))
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(dst)

    def _handle_distances(self, pos) -> list[float]:
        out = []
        for q in self.quad:
            w = self._to_widget(q)
            out.append(math.hypot(w.x() - pos.x(), w.y() - pos.y()))
        return out

    def mousePressEvent(self, e) -> None:
        if self.pixmap is None or e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position()
        dists = self._handle_distances(pos)
        i = dists.index(min(dists))
        if dists[i] <= self.GRAB_R:
            self._drag = i
        else:
            poly = QPolygonF([self._to_widget(q) for q in self.quad])
            if poly.containsPoint(pos, Qt.FillRule.OddEvenFill):
                self._drag = "all"
                self._last = self._to_image(pos)
            else:   # click outside the shape: the nearest corner jumps there
                self._drag = i
                self.quad[i] = list(self._to_image(pos))
                self.quad_changed.emit()
        self.update()

    def mouseMoveEvent(self, e) -> None:
        if self.pixmap is None:
            return
        pos = e.position()
        if self._drag is None:
            near = min(self._handle_distances(pos)) <= self.GRAB_R
            self.setCursor(Qt.CursorShape.CrossCursor if near else Qt.CursorShape.ArrowCursor)
            return
        if self._drag == "all":
            now = self._to_image(pos)
            dx, dy = now[0] - self._last[0], now[1] - self._last[1]
            moved = [[q[0] + dx, q[1] + dy] for q in self.quad]
            if all(0 <= q[0] <= self.img_w - 1 and 0 <= q[1] <= self.img_h - 1 for q in moved):
                self.quad = moved
                self._last = now
        else:
            self.quad[self._drag] = list(self._to_image(pos))
        self.update()
        self.quad_changed.emit()

    def mouseReleaseEvent(self, _e) -> None:
        if self._drag is not None:
            self._drag = None
            self.update()
            self.quad_changed.emit()


class PreviewPane(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(260, 280)
        self.pixmap: QPixmap | None = None

    def set_pixmap(self, pm: QPixmap | None) -> None:
        self.pixmap = pm
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(34, 36, 42))
        if self.pixmap is None:
            p.setPen(QColor(130, 136, 146))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Result preview")
            return
        m = 10
        s = min((self.width() - 2 * m) / self.pixmap.width(), (self.height() - 2 * m) / self.pixmap.height())
        w, h = self.pixmap.width() * s, self.pixmap.height() * s
        p.drawPixmap(QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h),
                     self.pixmap, QRectF(self.pixmap.rect()))


class PhotoFixPage(QWidget):
    status = Signal(str)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.img: QImage | None = None     # full-resolution original
        self.small: QImage | None = None   # working copy for the preview
        self.small_scale = 1.0
        self.path = ""
        self.turns = 0

        self.canvas = CornerCanvas()
        self.canvas.quad_changed.connect(self.schedule_preview)
        self.preview = PreviewPane()

        self.btn_open = button("Open…  (Ctrl+O)")
        self.btn_reset_corners = button("Reset corners")
        self.btn_left = button("⟲ Rotate left")
        self.btn_right = button("Rotate right ⟳")
        self.btn_auto = button("Auto fix colours")
        self.btn_reset = button("Reset edits")
        self.btn_save = button("Save as…  (Ctrl+S)", "big")

        self.sl_angle = LabeledSlider("Fine straighten", -100, 100, "°", 10.0)
        self.sl_bright = LabeledSlider("Brightness", -100, 100)
        self.sl_contrast = LabeledSlider("Contrast", -100, 100)
        self.sl_warm = LabeledSlider("Warmth  (blue ↔ yellow)", -100, 100)
        self._sliders = (self.sl_angle, self.sl_bright, self.sl_contrast, self.sl_warm)
        for s in self._sliders:
            s.changed.connect(self.schedule_preview)

        self.chk_auto = QCheckBox("Auto fix colours on every new photo")
        self.chk_auto.setChecked(bool(cfg["auto_fix"]))
        self.chk_auto.toggled.connect(lambda v: cfg.set("auto_fix", bool(v)))
        self.size_lbl = label("", "muted")

        side = QVBoxLayout()
        side.setSpacing(8)
        side.addWidget(self.preview, 1)
        side.addWidget(self.size_lbl)
        rot = QHBoxLayout()
        rot.addWidget(self.btn_left)
        rot.addWidget(self.btn_right)
        side.addLayout(rot)
        side.addWidget(self.sl_angle)
        side.addSpacing(6)
        side.addWidget(self.sl_bright)
        side.addWidget(self.sl_contrast)
        side.addWidget(self.sl_warm)
        fix = QHBoxLayout()
        fix.addWidget(self.btn_auto)
        fix.addWidget(self.btn_reset)
        side.addLayout(fix)
        side.addWidget(self.chk_auto)
        side.addSpacing(6)
        side.addWidget(self.btn_save)
        side_w = QWidget()
        side_w.setLayout(side)
        side_w.setFixedWidth(350)

        left = QVBoxLayout()
        top = QHBoxLayout()
        top.addWidget(self.btn_open)
        top.addWidget(self.btn_reset_corners)
        top.addStretch(1)
        left.addLayout(top)
        left.addWidget(self.canvas, 1)

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(14)
        root.addLayout(left, 1)
        root.addWidget(side_w)

        self.btn_open.clicked.connect(self.open_dialog)
        self.btn_reset_corners.clicked.connect(self.reset_corners)
        self.btn_left.clicked.connect(lambda: self.rotate(-1))
        self.btn_right.clicked.connect(lambda: self.rotate(1))
        self.btn_auto.clicked.connect(self.auto_fix)
        self.btn_reset.clicked.connect(self.reset_edits)
        self.btn_save.clicked.connect(self.save_dialog)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(15)
        self._timer.timeout.connect(self.update_preview)
        self._set_enabled(False)

    hint = "Drop a photo, drag the four blue corners onto the printed photo, then save."

    def _set_enabled(self, on: bool) -> None:
        for w in (self.btn_reset_corners, self.btn_left, self.btn_right, self.btn_auto, self.btn_reset,
                  self.btn_save, *self._sliders):
            w.setEnabled(on)

    # ---- loading
    def open_dialog(self) -> None:
        exts = " ".join("*" + x for x in sorted(imaging.IMAGE_EXTS))
        path, _ = QFileDialog.getOpenFileName(self, "Open photo", self.cfg["open_dir"], f"Images ({exts})")
        if path:
            self.load(path)

    def accept_paths(self, paths: list[str]) -> None:
        for p in paths:
            if os.path.splitext(p)[1].lower() in imaging.IMAGE_EXTS:
                self.load(p)
                return
        self.status.emit("That file isn't a picture.")

    def load(self, path: str) -> bool:
        img = imaging.load_image(path)
        if img is None:
            QMessageBox.warning(self, "DeskLink", "Could not open this file as an image:\n" + path)
            return False
        self.img, self.path, self.turns = img, path, 0
        self.cfg.set("open_dir", os.path.dirname(path))
        self.small, self.small_scale = imaging.scaled_to(img, PREVIEW_MAX)
        self.canvas.set_image(QPixmap.fromImage(self.small), img.width(), img.height(),
                              imaging.default_quad(img.width(), img.height()))
        self._set_enabled(True)
        self.reset_edits()
        if self.chk_auto.isChecked():
            self.auto_fix()
        self.update_preview()
        self.status.emit(f"{os.path.basename(path)} — drag the four corners onto the photo, then Ctrl+S to save.")
        return True

    def reset_corners(self) -> None:
        if self.img is not None:
            self.canvas.set_quad(imaging.default_quad(self.img.width(), self.img.height()))

    # ---- edits
    def rotate(self, step: int) -> None:
        if self.img is not None:
            self.turns = (self.turns + step) % 4
            self.schedule_preview()

    def reset_edits(self) -> None:
        for s in self._sliders:
            s.set_value(0, silent=True)
        self.schedule_preview()

    def _small_quad(self):
        return [(x * self.small_scale, y * self.small_scale) for x, y in self.canvas.quad]

    def auto_fix(self) -> None:
        if self.img is None:
            return
        flat = imaging.process(self.small, self._small_quad(), self.turns, self.sl_angle.value())
        b, c, w = imaging.auto_values(flat)
        self.sl_bright.set_value(b, silent=True)
        self.sl_contrast.set_value(c, silent=True)
        self.sl_warm.set_value(w, silent=True)
        self.schedule_preview()

    def _params(self) -> dict:
        return dict(turns=self.turns, angle=self.sl_angle.value(), brightness=self.sl_bright.value() / 100.0,
                    contrast=self.sl_contrast.value() / 100.0, warmth=self.sl_warm.value() / 100.0)

    def schedule_preview(self) -> None:
        if self.img is not None:
            self._timer.start()

    def update_preview(self) -> None:
        if self.img is None:
            return
        out = imaging.process(self.small, self._small_quad(), **self._params())
        self.preview.set_pixmap(QPixmap.fromImage(out))
        w, h = imaging.quad_size([tuple(q) for q in self.canvas.quad])
        if self.turns % 2:
            w, h = h, w
        self.size_lbl.setText(f"Output size: {w} × {h} px")

    # ---- saving
    def render_full(self) -> QImage:
        return imaging.process(self.img, [tuple(q) for q in self.canvas.quad], **self._params())

    def save_to(self, path: str) -> bool:
        if os.path.splitext(path)[1].lower() in (".jpg", ".jpeg"):
            return self.render_full().save(path, "JPG", 95)
        return self.render_full().save(path)

    def save_dialog(self) -> None:
        if self.img is None:
            return
        folder = self.cfg["save_dir"] or os.path.dirname(self.path)
        stem = os.path.splitext(os.path.basename(self.path))[0]
        path, chosen = QFileDialog.getSaveFileName(self, "Save fixed photo", os.path.join(folder, stem + "_fixed.jpg"),
                                                   "JPEG (*.jpg);;PNG (*.png)")
        if not path:
            return
        if os.path.splitext(path)[1].lower() not in (".jpg", ".jpeg", ".png"):
            path += ".png" if "PNG" in chosen else ".jpg"
        if not self.save_to(path):
            QMessageBox.critical(self, "DeskLink", "Could not save to:\n" + path)
            return
        self.cfg.set("save_dir", os.path.dirname(path))
        self.status.emit("Saved: " + path + "   —   drop the next photo.")
