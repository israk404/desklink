"""Reads text from a picture with the reader built into Windows 10/11 (nothing extra to download)."""
from __future__ import annotations

import sys

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from .imaging import scaled_to

MAX_SIDE = 3200   # big enough for small print, small enough to stay quick


class OcrError(Exception):
    pass


def recognize(img: QImage) -> list[list[dict]]:
    """Returns lines; each line is a list of words {"text", "x", "y", "w", "h"} in image pixels."""
    if sys.platform != "win32":
        raise OcrError("Reading text needs Windows 10 or 11.")
    try:
        from winrt.runtime import ApartmentType, init_apartment
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
    except Exception as exc:
        raise OcrError(f"The Windows text reader could not be loaded ({exc}).") from exc
    try:
        init_apartment(ApartmentType.MULTI_THREADED)
    except Exception:
        pass   # this thread is already set up
    engine = None
    try:
        english = Language("en-US")
        if OcrEngine.is_language_supported(english):
            engine = OcrEngine.try_create_from_language(english)
    except Exception:
        engine = None
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise OcrError("Windows has no text-reading language installed. Open Settings → Time & language → "
                       "Language & region, add English (United States) and tick its text recognition option.")
    limit = MAX_SIDE
    try:
        limit = min(limit, int(OcrEngine.max_image_dimension))
    except Exception:
        pass
    small, scale = scaled_to(img, limit)
    small = small.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)   # bytes are B, G, R, A
    w, h = small.width(), small.height()
    pixels = bytes(small.constBits())[: w * h * 4]
    bitmap = SoftwareBitmap.create_copy_from_buffer(pixels, BitmapPixelFormat.BGRA8, w, h,
                                                    BitmapAlphaMode.PREMULTIPLIED)
    result = engine.recognize_async(bitmap).get()
    lines: list[list[dict]] = []
    for line in result.lines:
        words = []
        for word in line.words:
            r = word.bounding_rect
            words.append({"text": word.text, "x": r.x / scale, "y": r.y / scale,
                          "w": r.width / scale, "h": r.height / scale})
        if words:
            lines.append(words)
    return lines


class OcrJob(QThread):
    """Runs the reader off the UI thread. `finished_ok(lines)` or `failed(message)`."""
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, img: QImage, parent=None):
        super().__init__(parent)
        self._img = img.copy()

    def run(self) -> None:
        try:
            self.finished_ok.emit(recognize(self._img))
        except OcrError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Could not read the text: {exc}")
