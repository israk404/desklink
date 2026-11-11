"""Image work for Photo Fix, done with Qt only (no OpenCV / NumPy), so the package stays small."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPolygonF, QTransform

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff", ".gif"}
Quad = list[tuple[float, float]]   # top-left, top-right, bottom-right, bottom-left (image pixels)

QImageReader.setAllocationLimit(1024)   # MB; phone cameras now go past Qt's 256 MB default


def load_image(path: str) -> QImage | None:
    """Reads a photo the right way up (phones store rotation as an EXIF note)."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    img = reader.read()
    if img.isNull():
        return None
    return img.convertToFormat(QImage.Format.Format_RGB32)


def scaled_to(img: QImage, max_side: int) -> tuple[QImage, float]:
    """A copy no bigger than max_side on its longest side, and the scale that was applied."""
    longest = max(img.width(), img.height())
    if longest <= max_side:
        return img, 1.0
    s = max_side / longest
    out = img.scaled(max(1, round(img.width() * s)), max(1, round(img.height() * s)),
                     Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return out, out.width() / img.width()


def default_quad(w: int, h: int, margin: float = 0.12) -> Quad:
    mx, my = w * margin, h * margin
    return [(mx, my), (w - mx, my), (w - mx, h - my), (mx, h - my)]


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def quad_size(quad: Quad) -> tuple[int, int]:
    tl, tr, br, bl = quad
    w = max(_dist(tl, tr), _dist(bl, br))
    h = max(_dist(tl, bl), _dist(tr, br))
    return max(8, round(w)), max(8, round(h))


def warp_quad(img: QImage, quad: Quad) -> QImage:
    """Flatten the four-corner selection into a straight rectangle."""
    w, h = quad_size(quad)
    src = QPolygonF([QPointF(x, y) for x, y in quad])
    dst = QPolygonF([QPointF(0, 0), QPointF(w, 0), QPointF(w, h), QPointF(0, h)])
    transform = QTransform()
    ok = QTransform.quadToQuad(src, dst, transform)
    out = QImage(w, h, QImage.Format.Format_RGB32)
    out.fill(QColor("white"))
    if not ok:   # corners folded over each other: nothing sensible to draw
        return out
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setTransform(transform)
    p.drawImage(0, 0, img)
    p.end()
    return out


def rotate_quarters(img: QImage, turns: int) -> QImage:
    turns %= 4
    if not turns:
        return img
    return img.transformed(QTransform().rotate(90 * turns))


def rotate_fill(img: QImage, angle: float) -> QImage:
    """Small-angle rotation that zooms just enough to leave no empty corners."""
    if abs(angle) < 0.01:
        return img
    w, h = img.width(), img.height()
    a = math.radians(abs(angle))
    zoom = math.cos(a) + max(w / h, h / w) * math.sin(a)
    out = QImage(w, h, QImage.Format.Format_RGB32)
    out.fill(QColor("white"))
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.translate(w / 2, h / 2)
    p.rotate(angle)
    p.scale(zoom, zoom)
    p.translate(-w / 2, -h / 2)
    p.drawImage(0, 0, img)
    p.end()
    return out


def _lut(gain: float, brightness: float, contrast: float) -> bytes:
    factor = 2.0 ** contrast
    table = bytearray(256)
    for i in range(256):
        x = i / 255.0 * gain
        x = (x - 0.5) * factor + 0.5 + 0.4 * brightness
        table[i] = max(0, min(255, int(x * 255.0 + 0.5)))
    return bytes(table)


def apply_adjust(img: QImage, brightness: float, contrast: float, warmth: float) -> QImage:
    """brightness / contrast / warmth are each -1.0 .. 1.0 (0 = unchanged)."""
    if not (brightness or contrast or warmth):
        return img
    img = img.convertToFormat(QImage.Format.Format_RGB32)
    w, h = img.width(), img.height()
    data = bytes(img.constBits())[: w * h * 4]      # B, G, R, x per pixel
    out = bytearray(data)
    out[0::4] = data[0::4].translate(_lut(1.0 - 0.25 * warmth, brightness, contrast))
    out[1::4] = data[1::4].translate(_lut(1.0, brightness, contrast))
    out[2::4] = data[2::4].translate(_lut(1.0 + 0.25 * warmth, brightness, contrast))
    return QImage(bytes(out), w, h, w * 4, QImage.Format.Format_RGB32).copy()


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    return sorted_values[min(len(sorted_values) - 1, int(len(sorted_values) * pct / 100.0))]


def auto_values(img: QImage) -> tuple[int, int, int]:
    """Suggested (brightness, contrast, warmth) slider values, each -100..100."""
    small, _ = scaled_to(img.convertToFormat(QImage.Format.Format_RGB32), 160)
    n = small.width() * small.height()
    data = bytes(small.constBits())[: n * 4]
    blue = [v / 255.0 for v in data[0::4]]
    green = [v / 255.0 for v in data[1::4]]
    red = [v / 255.0 for v in data[2::4]]

    def lum_of(r, g, b):
        return [0.299 * r[i] + 0.587 * g[i] + 0.114 * b[i] for i in range(n)]

    # warmth: make the brightest area (paper, shirt, background) neutral
    lum = lum_of(red, green, blue)
    cut = _percentile(sorted(lum), 95)
    bright = [i for i in range(n) if lum[i] >= cut] or list(range(n))
    r_mean = sum(red[i] for i in bright) / len(bright)
    b_mean = sum(blue[i] for i in bright) / len(bright)
    warmth = 0.0
    if r_mean + b_mean > 1e-3:
        warmth = (b_mean - r_mean) / (0.25 * (r_mean + b_mean))
    warmth = max(-0.5, min(0.5, warmth * 0.7))
    red = [v * (1.0 + 0.25 * warmth) for v in red]
    blue = [v * (1.0 - 0.25 * warmth) for v in blue]

    # contrast + brightness: stretch the tones to nearly the full range
    ordered = sorted(lum_of(red, green, blue))
    lo, hi = _percentile(ordered, 1), _percentile(ordered, 99)
    factor = max(1.0, min(1.8, 0.94 / max(hi - lo, 1e-3)))
    lo2 = (lo - 0.5) * factor + 0.5
    hi2 = (hi - 0.5) * factor + 0.5
    brightness = max(-0.6, min(0.6, ((0.03 - lo2) + (0.97 - hi2)) / 2.0 / 0.4))
    return round(brightness * 100), round(math.log2(factor) * 100), round(warmth * 100)


def process(img: QImage, quad: Quad, turns: int = 0, angle: float = 0.0,
            brightness: float = 0.0, contrast: float = 0.0, warmth: float = 0.0) -> QImage:
    """The whole Photo Fix pipeline: flatten -> rotate -> straighten -> colour."""
    out = warp_quad(img, quad)
    out = rotate_quarters(out, turns)
    out = rotate_fill(out, angle)
    return apply_adjust(out, brightness, contrast, warmth)
