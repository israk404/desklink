"""Runs the real PC app offscreen for the Kotlin client test (android/protocol-test). Prints what the test needs."""
import hashlib, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["APPDATA"] = tempfile.mkdtemp(prefix="dljvm")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from desklink import app as appmod, core, protocol as P
qapp = QApplication(sys.argv)
ctl = appmod.Controller(qapp, show=False)
ctl.window.text.recognize = lambda img: []          # the Windows text reader isn't part of this test
print("URI", P.pairing_uri(ctl.cfg.key, ctl.cfg["port"], ["127.0.0.1"], ctl.name), flush=True)
folder = tempfile.mkdtemp(prefix="dljvmsend")
outfile = os.path.join(folder, "from-pc.pdf"); blob = b"%PDF-1.4\n" + os.urandom(3_000_000); open(outfile, "wb").write(blob)
print("PCSHA", hashlib.sha256(blob).hexdigest(), flush=True)
print("CAPTURES", core.captures_dir(), flush=True)
done = []
def on_ready(phone):
    if done: return
    done.append(1)
    QTimer.singleShot(1500, ctl.request_camera)
    QTimer.singleShot(3000, lambda: ctl.send_paths([outfile]))
ctl.server.phone_ready.connect(on_ready)
ctl.server.pairing_open = True
def on_pair(phone):
    if "Deny" in phone.device:
        phone.finish_pairing(None); ctl.server.pairing_open = False
    else:
        phone.finish_pairing({"k": P.b64u(ctl.cfg.key), "p": ctl.cfg["port"], "h": ["127.0.0.1"],
                              "n": ctl.name, "test_code": phone.pair_code})
ctl.server.pair_request.connect(on_pair)
QTimer.singleShot(90_000, qapp.quit)
qapp.exec()
