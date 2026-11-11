"""End-to-end test: the real PC app (offscreen) + a simulated phone over TCP. Saves screenshots.

    python tests/test_headless.py          (set SHOTS=folder to choose where screenshots go)
"""
import hashlib, os, sys, tempfile, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="dltest")
SHOTS = os.environ.get("SHOTS") or tempfile.mkdtemp(prefix="dlshots"); os.makedirs(SHOTS, exist_ok=True)

from PySide6.QtCore import QBuffer, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPolygonF, QTransform
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from desklink import app as appmod, imaging, protocol as P
from fake_phone import FakePhone

qapp = QApplication(sys.argv); qapp.setStyle("Fusion")
ctl = appmod.Controller(qapp, show=False)
win = ctl.window
win.resize(1280, 800); win.show()
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond))); print(("PASS " if cond else "FAIL ") + name, detail, flush=True)


def pump(seconds=0.3, until=None):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        if until and until():
            return True
        time.sleep(0.01)
    return bool(until and until())


def shot(name):
    qapp.processEvents(); win.grab().save(os.path.join(SHOTS, name))


def receive_file(phone, seconds=15):
    """Collect one file the PC sends (offer, chunks, done) while keeping the app's event loop running."""
    state = {"offer": None, "buf": bytearray(), "done": None}
    def step():
        while not phone.inbox.empty():
            m = phone.inbox.get_nowait()
            if m.get("t") == "file_offer" and state["offer"] is None:
                state["offer"] = m
            elif m.get("t") == "__chunk__" and state["offer"] and m["id"] == state["offer"]["id"]:
                state["buf"] += m["data"]
            elif m.get("t") == "file_done" and state["offer"] and m["id"] == state["offer"]["id"]:
                state["done"] = m
        return state["done"] is not None
    pump(seconds, step)
    return state["offer"], bytes(state["buf"]), state["done"]


def wait_msg(phone, kind, seconds=5):
    box = {}
    def got():
        while not phone.inbox.empty():
            m = phone.inbox.get_nowait()
            if m.get("t") == kind:
                box["m"] = m; return True
        return False
    pump(seconds, got)
    return box.get("m")


# ---------------------------------------------------------------- Photo Fix
def tilted_photo(path):
    """A fake phone shot: a blue 'passport photo' lying crooked on a wooden table, a bit dark and yellow."""
    scene = QImage(1200, 1600, QImage.Format.Format_RGB32); scene.fill(QColor(120, 85, 50))
    card = QImage(350, 450, QImage.Format.Format_RGB32); card.fill(QColor(105, 150, 205))
    p = QPainter(card); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(200, 165, 140)); p.drawEllipse(QPointF(175, 190), 85, 110)
    p.setBrush(QColor(35, 30, 30)); p.drawChord(85, 50, 180, 140, 0, 180 * 16)
    p.setBrush(QColor(45, 50, 60)); p.drawEllipse(QPointF(175, 470), 160, 150); p.end()
    quad = [(330, 420), (800, 500), (740, 1120), (250, 1010)]
    t = QTransform()
    QTransform.quadToQuad(QPolygonF([QPointF(0, 0), QPointF(350, 0), QPointF(350, 450), QPointF(0, 450)]),
                          QPolygonF([QPointF(x, y) for x, y in quad]), t)
    p = QPainter(scene); p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setTransform(t); p.drawImage(0, 0, card); p.end()
    scene.save(path, "JPG", 92)
    return quad

src = os.path.join(tempfile.mkdtemp(prefix="dlsrc"), "ছবি phone shot.jpg")   # non-English name on purpose
quad = tilted_photo(src)
win.set_mode(0)
check("photo loads (unicode path)", win.photo.load(src))
check("no auto-detection: corners start as a plain inset rectangle",
      win.photo.canvas.quad == [list(p) for p in imaging.default_quad(1200, 1600)])
shot("1_photo_fix_loaded.png")
win.photo.canvas.set_quad(quad)
win.photo.auto_fix(); win.photo.sl_angle.set_value(0.4); pump(0.2)
b, c, w = (s.slider.value() for s in (win.photo.sl_bright, win.photo.sl_contrast, win.photo.sl_warm))
check("auto fix moved the sliders", (b, c, w) != (0, 0, 0), f"b={b} c={c} w={w}")
shot("2_photo_fix_corners_set.png")
out = os.path.join(os.path.dirname(src), "ফল fixed.jpg")
check("saves a JPEG", win.photo.save_to(out) and os.path.getsize(out) > 5000)
saved = imaging.load_image(out)
ratio = saved.width() / saved.height()
check("result is the flattened photo, portrait shaped", 0.65 < ratio < 0.9, f"{saved.width()}x{saved.height()}")
mid = QColor(saved.pixel(saved.width() // 2, int(saved.height() * 0.42)))
corner = QColor(saved.pixel(12, 12))
check("face in the middle, blue background in the corner, no table left",
      mid.red() > mid.blue() and corner.blue() > corner.red() + 30, f"mid={mid.getRgb()[:3]} corner={corner.getRgb()[:3]}")
win.photo.rotate(1); pump(0.1)
check("rotate swaps the output size", "×" in win.photo.size_lbl.text() and
      win.photo.size_lbl.text().split()[2] == str(saved.height()), win.photo.size_lbl.text())
win.photo.rotate(-1)

# ---------------------------------------------------------------- phone link
key, port = ctl.cfg.key, ctl.cfg["port"]
check("server is listening", ctl.listening, ctl.server.error)
win.set_mode(1)
ctl.request_camera(); pump(0.1)
check("camera button without a phone explains what to do", "No phone connected" in win.text.info.text())
phone = FakePhone(key, port, "Galaxy A54"); phone.start()
st = wait_msg(phone, "state")
check("phone connects and gets the PC name", st and st.get("pc_name") == ctl.name)
pump(0.2)
check("top bar shows the phone", "Galaxy A54 connected" in win.phone_lbl.text(), win.phone_lbl.text())

# ---------------------------------------------------------------- Text Capture
def text_photo():
    img = QImage(1400, 900, QImage.Format.Format_RGB32); img.fill(QColor(246, 244, 238))
    p = QPainter(img); p.setPen(QColor(25, 25, 30)); f = QFont(); f.setPixelSize(54); p.setFont(f)
    lines = ["Invoice number 4471", "Ship to Mirpur 10 Dhaka", "Total 2350 taka"]
    boxes = []
    fm = p.fontMetrics()
    for n, line in enumerate(lines):
        x, y = 90, 200 + n * 150
        row = []
        for word in line.split():
            wdt = fm.horizontalAdvance(word)
            p.drawText(x, y, word)
            row.append({"text": word, "x": x, "y": y - fm.ascent(), "w": wdt, "h": fm.height()})
            x += wdt + fm.horizontalAdvance(" ")
        boxes.append(row)
    p.end()
    return img, boxes

photo, boxes = text_photo()
win.text.recognize = lambda img: boxes        # stand-in for the Windows reader, which only exists on Windows
ctl.request_camera()
cam = wait_msg(phone, "camera")
check("'Open phone camera' reaches the phone", cam is not None)
phone.send_quiet({"t": "camera_ack", "state": "opened"}); pump(0.3)
check("PC says the camera is open", "camera is open" in win.text.info.text(), win.text.info.text())
buf = QBuffer(); buf.open(QBuffer.OpenModeFlag.WriteOnly); photo.save(buf, "JPG", 90)
jpeg = bytes(buf.data())
win.set_mode(2)                                # be somewhere else: the photo should bring Text Capture forward
tid = phone.send_file(jpeg, "capture.jpg", "image/jpeg", kind="capture")
ack = wait_msg(phone, "file_ack", 8)
check("photo from the phone is received intact", ack and ack.get("ok") is True)
pump(0.3)
check("Text Capture opens with the photo", win.mode() == 1 and win.text.img is not None and win.text.img.width() == 1400)
check("words are boxed", len(win.text.canvas.words) == 11, str(len(win.text.canvas.words)))
check("full text panel", win.text.text.toPlainText() == "Invoice number 4471\nShip to Mirpur 10 Dhaka\nTotal 2350 taka",
      repr(win.text.text.toPlainText()))
canvas = win.text.canvas
clip = QGuiApplication.clipboard()

def centre(i):
    return canvas._rect(canvas.words[i]).center().toPoint()

QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre(2)); pump(0.1)
check("click copies one word", clip.text() == "4471", repr(clip.text()))
QTest.mousePress(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre(5))
QTest.mouseMove(canvas, centre(7)); QTest.mouseMove(canvas, centre(9))
QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre(9)); pump(0.1)
check("drag copies a stretch across lines", clip.text() == "Mirpur 10 Dhaka\nTotal 2350", repr(clip.text()))
shot("3_text_capture_drag_selection.png")
QTest.mouseDClick(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre(4)); pump(0.1)
check("double-click copies the line", clip.text() == "Ship to Mirpur 10 Dhaka", repr(clip.text()))
win.text.copy_all(); pump(0.1)
check("copy all", clip.text().count("\n") == 2 and clip.text().endswith("taka"))
QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, canvas.rect().bottomRight() - QPoint(6, 6))
check("clicking empty space clears the selection", canvas.selection() == [])
tid2 = phone.send_file(b"x" * 5000, "notes.txt", "text/plain", kind="file")
cancel = wait_msg(phone, "file_cancel", 4)
check("anything that isn't a camera photo is refused", cancel and cancel.get("id") == tid2)

# ---------------------------------------------------------------- Send to Phone
win.set_mode(2)
pdf = os.path.join(os.path.dirname(src), "Admit card রোল.pdf")
blob = b"%PDF-1.4\n" + os.urandom(700_000)
open(pdf, "wb").write(blob)
phone.drain()
win.send.accept_paths([pdf, os.path.dirname(pdf)])          # a file and a folder, as a drop would deliver
footer_after_drop = win.footer.text()
offer, data, done = receive_file(phone)
check("dropped PDF arrives on the phone", offer and done and offer["name"] == "Admit card রোল.pdf" and offer["mime"] == "application/pdf"
      and data == blob and done["sha256"] == hashlib.sha256(blob).hexdigest(), offer["name"])
pump(0.3)
entry = ctl.files.entries[0]
check("list shows it as sent, waiting for confirmation", entry["status"] == "sent", entry["status"])
check("drop is acknowledged in the status line", "Sending 1 file" in footer_after_drop, footer_after_drop)
phone.send_quiet({"t": "file_ack", "id": offer["id"], "ok": True}); pump(0.4)
check("after the phone confirms: On the phone ✓", entry["status"] == "done" and
      "On the phone" in win.send._rows[entry["id"]].meta.text())
shot("4_send_to_phone.png")
import socket; phone.sock.shutdown(socket.SHUT_RDWR); pump(0.8)
check("phone leaving shows in the top bar", "No phone connected" in win.phone_lbl.text(), win.phone_lbl.text())
win.send.accept_paths([pdf]); pump(0.1)
check("sending with no phone is refused with a reason", "No phone connected" in win.footer.text())

# wrong key must be dropped
bad = FakePhone(os.urandom(32), port, "Stranger"); bad.start(); pump(1.0)
check("a phone with the wrong key is rejected", bad.closed.is_set() and not ctl.server.phones)

ctl.show_pairing(); pump(0.3)
ctl._pair_dialog.grab().save(os.path.join(SHOTS, "5_pair_dialog.png"))

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed. Screenshots: {SHOTS}")
ctl.server.stop()
sys.exit(1 if failed else 0)
