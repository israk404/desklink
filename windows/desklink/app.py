"""DeskLink for Windows — the window with its three modes, and the link to the phone."""
from __future__ import annotations

import getpass
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel, QStackedWidget,
                               QVBoxLayout, QWidget)

from . import core, protocol, theme, transfers, winsys
from .photofix import PhotoFixPage
from .sendpage import SendPage
from .server import PhoneConnection, SyncServer
from .textcapture import TextCapturePage
from .transfers import Incoming, Outgoing
from .widgets import PairDialog, PairRequestDialog, button, label

MODES = ("Photo Fix", "Text Capture", "Send to Phone")
KEEP_CAPTURES = 10


class MainWindow(QWidget):
    def __init__(self, cfg: core.Config):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("DeskLink")
        self.setAcceptDrops(True)
        self.resize(1240, 800)
        self.on_close = None

        self.photo = PhotoFixPage(cfg)
        self.text = TextCapturePage(cfg)
        self.send = SendPage()
        self.pages = (self.photo, self.text, self.send)

        bar = QFrame()
        bar.setObjectName("topbar")
        top = QHBoxLayout(bar)
        top.setContentsMargins(14, 8, 14, 8)
        top.setSpacing(6)
        logo = QLabel()
        logo.setPixmap(theme.logo_pixmap(28))
        top.addWidget(logo)
        name = label("DeskLink", "big")
        top.addWidget(name)
        top.addSpacing(18)
        self.mode_buttons = QButtonGroup(self)
        self.mode_buttons.setExclusive(True)
        for i, title in enumerate(MODES):
            b = button(title, "mode")
            b.setCheckable(True)
            b.setToolTip(f"Ctrl+{i + 1}")
            self.mode_buttons.addButton(b, i)
            top.addWidget(b)
        top.addStretch(1)
        self.dot = QLabel()
        self.phone_lbl = label("", "muted")
        self.btn_pair = button("Pair phone")
        top.addWidget(self.dot)
        top.addWidget(self.phone_lbl)
        top.addSpacing(8)
        top.addWidget(self.btn_pair)

        self.stack = QStackedWidget()
        for page in self.pages:
            self.stack.addWidget(page)
            page.status.connect(self.set_footer)
        self.footer = label("", "muted")
        self.footer.setContentsMargins(16, 4, 16, 8)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(bar)
        root.addWidget(self.stack, 1)
        root.addWidget(self.footer)

        self.mode_buttons.idClicked.connect(self.set_mode)
        for i in range(len(MODES)):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda i=i: self.set_mode(i))
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self._open)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self._save)
        QShortcut(QKeySequence("Ctrl+V"), self, activated=self._paste)
        QShortcut(QKeySequence("Ctrl+A"), self, activated=self._copy_all)
        self.mode_changed = None
        self.set_mode(0)

    # ---- modes
    def mode(self) -> int:
        return self.stack.currentIndex()

    def set_mode(self, index: int) -> None:
        index = max(0, min(len(MODES) - 1, int(index)))
        self.stack.setCurrentIndex(index)
        self.mode_buttons.button(index).setChecked(True)
        self.set_footer(self.pages[index].hint)
        if self.mode_changed:
            self.mode_changed(index)

    def set_footer(self, text: str) -> None:
        self.footer.setText(text)

    def set_phone(self, names: list[str], hint: str = "") -> None:
        pal = theme.palette()
        if names:
            self.dot.setPixmap(theme.dot_pixmap(pal["ok"]))
            self.phone_lbl.setText(", ".join(names) + " connected")
        else:
            self.dot.setPixmap(theme.dot_pixmap(pal["off"]))
            self.phone_lbl.setText(hint or "No phone connected")

    # ---- shortcuts go to whichever mode is open
    def _open(self) -> None:
        page = self.stack.currentWidget()
        if page is self.send:
            self.send.pick_files.emit()
        else:
            page.open_dialog()

    def _save(self) -> None:
        if self.stack.currentWidget() is self.photo:
            self.photo.save_dialog()

    def _paste(self) -> None:
        if self.stack.currentWidget() is self.text:
            self.text.paste()

    def _copy_all(self) -> None:
        if self.stack.currentWidget() is self.text:
            self.text.copy_all()

    # ---- drag and drop
    @staticmethod
    def _paths(mime) -> list[str]:
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile() and u.toLocalFile()]

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls() and self._paths(e.mimeData()):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        paths = self._paths(e.mimeData())
        if paths:
            e.acceptProposedAction()
            self.stack.currentWidget().accept_paths(paths)

    def closeEvent(self, event) -> None:
        if self.on_close:
            self.on_close()
        super().closeEvent(event)


class Controller(QObject):
    def __init__(self, app: QApplication, show: bool = True):
        super().__init__()
        self.app = app
        self.cfg = core.Config()
        self.files = core.FileList()
        self.name = core.pc_name()
        self._incoming: dict[str, tuple[Incoming, PhoneConnection]] = {}
        self._progress_ui: dict[str, float] = {}
        self._pair_dialog: PairDialog | None = None
        self._pair_requests: dict[int, PairRequestDialog] = {}

        self.server = SyncServer(self.cfg.key, self.cfg["port"], self.name, self)
        self.server.phone_ready.connect(self._on_phone_ready)
        self.server.phone_message.connect(self._on_phone_message)
        self.server.phone_chunk.connect(self._on_chunk)
        self.server.phone_gone.connect(self._on_phone_gone)
        self.server.log.connect(lambda m: print("[DeskLink]", m, flush=True))
        self.server.pair_request.connect(self._on_pair_request)
        self.server.pair_gone.connect(self._on_pair_gone)

        w = self.window = MainWindow(self.cfg)
        w.on_close = self.quit
        w.btn_pair.clicked.connect(self.show_pairing)
        w.text.request_camera.connect(self.request_camera)
        w.send.pick_files.connect(self.pick_files)
        w.send.send_paths.connect(self.send_paths)
        w.send.cancel_file.connect(self._cancel_file)
        w.send.remove_file.connect(self._remove_file)
        w.send.clear_list.connect(self._clear_files)
        w.set_mode(self.cfg["mode"])
        w.mode_changed = lambda i: self.cfg.set("mode", i)

        self.apply_theme()
        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(lambda *_: self.apply_theme())
        except Exception:
            pass

        self.listening = self.server.start()
        w.send.set_files(self.files.entries)
        self._update_status()
        if not self.listening:
            w.set_footer(self.server.error)
        if show:
            w.showMaximized()
            if not self.cfg["paired_once"]:
                QTimer.singleShot(500, self.show_pairing)

    # ---------------- phone link ----------------
    def _on_phone_ready(self, phone: PhoneConnection) -> None:
        phone.send({"t": "state", "v": protocol.VERSION, "pc_name": self.name})
        if not self.cfg["paired_once"]:
            self.cfg.set("paired_once", True)
        if self._pair_dialog and self._pair_dialog.isVisible():
            self._pair_dialog.show_success(phone.device)
        self._update_status()

    def _on_phone_gone(self, phone: PhoneConnection) -> None:
        for tid, (inc, owner) in list(self._incoming.items()):
            if owner is phone:
                inc.abort()
                del self._incoming[tid]
        self._update_status()

    def _update_status(self) -> None:
        names = [p.device for p in self.server.phones]
        hint = "No phone connected" if self.cfg["paired_once"] else "No phone paired yet"
        self.window.set_phone(names, hint)

    def _on_phone_message(self, phone: PhoneConnection, msg: dict) -> None:
        kind = msg.get("t")
        if kind == "file_offer":
            self._on_file_offer(phone, msg)
        elif kind == "file_done":
            self._on_file_done(phone, msg)
        elif kind == "file_cancel":
            tid = str(msg.get("id"))
            if tid in self._incoming:
                self._incoming.pop(tid)[0].abort()
            else:
                phone.cancel_file(tid, tell_phone=False)
                self._set_file_status(tid, "cancelled")
        elif kind == "file_ack":
            entry = self.files.find(str(msg.get("id")))
            if entry:
                self._set_file_status(entry["id"], "done" if msg.get("ok") else "failed")
        elif kind == "camera_ack":
            state = msg.get("state")
            self.window.text.set_info({
                "opened": f"The camera is open on {phone.device} — take the photo.",
                "notify": f"Tap the DeskLink notification on {phone.device} to open the camera.",
                "cancelled": "No photo was taken.",
            }.get(state, "Waiting for the phone…"))

    # ---------------- Text Capture: photo from the phone ----------------
    def request_camera(self) -> None:
        phones = self.server.phones
        if not phones:
            self.window.text.set_info("No phone connected. Open DeskLink on your phone (same Wi-Fi), "
                                      "or press “Pair phone” if you haven't paired yet.")
            return
        phones[0].send({"t": "camera"})
        self.window.text.set_info(f"Asking {phones[0].device} to open its camera…")

    def _on_file_offer(self, phone: PhoneConnection, msg: dict) -> None:
        tid = str(msg.get("id") or "")[: protocol.ID_LEN]
        size = int(msg.get("size") or -1)
        if len(tid) != protocol.ID_LEN or tid in self._incoming:
            return
        if msg.get("kind") != "capture" or size > protocol.MAX_CLIP_IMAGE:
            phone.send({"t": "file_cancel", "id": tid, "reason": "not accepted"})
            return
        ext = os.path.splitext(transfers.safe_name(msg.get("name") or "photo.jpg"))[1].lower() or ".jpg"
        dest = core.captures_dir() / f"capture-{time.strftime('%Y%m%d-%H%M%S')}-{tid[:4]}{ext}"
        try:
            self._incoming[tid] = (Incoming(msg, dest), phone)
        except OSError as exc:
            phone.send({"t": "file_cancel", "id": tid, "reason": str(exc)})
            return
        self.window.text.set_info("Receiving the photo…")

    def _on_chunk(self, phone: PhoneConnection, tid: str, data: bytes) -> None:
        pair = self._incoming.get(tid)
        if not pair:
            return
        try:
            pair[0].write(data)
        except Exception as exc:
            pair[0].abort()
            del self._incoming[tid]
            phone.send({"t": "file_cancel", "id": tid, "reason": str(exc)})

    def _on_file_done(self, phone: PhoneConnection, msg: dict) -> None:
        pair = self._incoming.pop(str(msg.get("id")), None)
        if not pair:
            return
        inc = pair[0]
        ok = inc.finish(str(msg.get("sha256") or ""))
        phone.send({"t": "file_ack", "id": inc.id, "ok": ok})
        if not ok:
            self.window.text.set_info("The photo arrived damaged. Please take it again.")
            return
        self.window.set_mode(1)
        self.window.text.load_path(str(inc.dest))
        self.show_window()
        old = sorted(core.captures_dir().glob("capture-*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in old[KEEP_CAPTURES:]:
            try:
                stale.unlink()
            except OSError:
                pass

    # ---------------- Send to Phone ----------------
    def pick_files(self) -> None:
        start = str(Path.home() / "Documents") if (Path.home() / "Documents").exists() else str(Path.home())
        paths, _ = QFileDialog.getOpenFileNames(self.window, "Send to phone", start)
        if paths:
            self.send_paths(paths)

    def send_paths(self, paths: list[str]) -> None:
        phones = self.server.phones
        if not phones:
            self.window.set_footer("No phone connected. Open DeskLink on your phone first (same Wi-Fi).")
            return
        files = [p for p in paths if os.path.isfile(p)]
        if len(files) < len(paths):
            self.window.set_footer("Folders can't be sent. Zip the folder first, then send the .zip.")
        for path in files:
            size = os.path.getsize(path)
            name = os.path.basename(path)
            mime = transfers.guess_mime(name)
            for phone in phones:
                tid = transfers.new_id()
                self.files.add({"id": tid, "name": name, "size": size, "dir": "out", "device": phone.device,
                                "mime": mime, "ts": core.now_ms(), "status": "sending", "progress": 0, "path": path})
                offer = {"t": "file_offer", "id": tid, "kind": "file", "name": name, "mime": mime, "size": size}
                try:
                    out = Outgoing(offer, path, on_progress=self._on_out_progress, on_done=self._on_out_done)
                except OSError as exc:
                    self._set_file_status(tid, "failed")
                    self.window.set_footer(f"Couldn't read {name}: {exc}")
                    continue
                phone.send_file(out)
        if files:
            n = "1 file" if len(files) == 1 else f"{len(files)} files"
            self.window.set_footer(f"Sending {n} to {phones[0].device}…")
        self.window.send.set_files(self.files.entries)

    def _on_out_progress(self, out: Outgoing) -> None:
        entry = self.files.find(out.id)
        if entry:
            entry["progress"] = out.sent
            now = time.monotonic()
            if now - self._progress_ui.get(out.id, 0) > 0.15:
                self._progress_ui[out.id] = now
                self.window.send.update_file(entry)

    def _on_out_done(self, out: Outgoing, ok: bool) -> None:
        entry = self.files.find(out.id)
        if entry and entry.get("status") == "sending":
            entry["progress"] = out.sent
            self._set_file_status(out.id, "sent" if ok else ("cancelled" if out.cancelled else "failed"))

    def _set_file_status(self, tid: str, status: str) -> None:
        entry = self.files.find(tid)
        if not entry:
            return
        entry["status"] = status
        self.files.save()
        self.window.send.set_files(self.files.entries)

    def _cancel_file(self, tid: str) -> None:
        for phone in self.server.phones:
            phone.cancel_file(tid)
        self._set_file_status(tid, "cancelled")

    def _remove_file(self, tid: str) -> None:
        if (self.files.find(tid) or {}).get("status") == "sending":
            self._cancel_file(tid)
        self.files.remove(tid)
        self.window.send.set_files(self.files.entries)

    def _clear_files(self) -> None:
        self.files.clear_finished()
        self.window.send.set_files(self.files.entries)

    # ---------------- pairing ----------------
    def _pairing_uri(self) -> tuple[str, list[str]]:
        hosts = core.local_ipv4s()
        return protocol.pairing_uri(self.cfg.key, self.cfg["port"], hosts, self.name), hosts

    def show_pairing(self) -> None:
        if not self._pair_dialog:
            self._pair_dialog = PairDialog(self.window)
            self._pair_dialog.copy_link.connect(
                lambda: QGuiApplication.clipboard().setText(self._pairing_uri()[0]))
            self._pair_dialog.new_key.connect(self._regenerate_key)
            self._pair_dialog.fix_firewall.connect(winsys.allow_through_firewall)
            self._pair_dialog.finished.connect(lambda *_: setattr(self.server, "pairing_open", False))
        uri, hosts = self._pairing_uri()
        self._pair_dialog.set_pairing(uri, self.name, hosts, self.cfg["port"])
        self.server.pairing_open = True
        self._pair_dialog.show()
        self._pair_dialog.raise_()
        self._pair_dialog.activateWindow()

    def _on_pair_request(self, phone: PhoneConnection) -> None:
        parent = self._pair_dialog if self._pair_dialog and self._pair_dialog.isVisible() else self.window
        dlg = PairRequestDialog(parent, phone.device, phone.pair_code)
        self._pair_requests[phone.id] = dlg
        if self._pair_dialog:
            self._pair_dialog.set_status(f"{phone.device} wants to pair — check the code", ok=False)

        def done(result: int) -> None:
            self._pair_requests.pop(phone.id, None)
            if result == 1:   # QDialog.Accepted
                phone.finish_pairing({"k": protocol.b64u(self.cfg.key), "p": self.cfg["port"],
                                      "h": core.local_ipv4s(), "n": self.name})
                if self._pair_dialog:
                    self._pair_dialog.set_status(f"Paired with {phone.device} — connecting…", ok=True)
            else:
                phone.finish_pairing(None)
                if self._pair_dialog:
                    self._pair_dialog.set_status("Pairing denied. Waiting for your phone…", ok=False)

        dlg.finished.connect(done)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_pair_gone(self, phone: PhoneConnection) -> None:
        dlg = self._pair_requests.pop(phone.id, None)
        if dlg:
            dlg.blockSignals(True)
            dlg.close()
        if self._pair_dialog and self._pair_dialog.isVisible():
            self._pair_dialog.set_status("The phone cancelled. Waiting for your phone…", ok=False)

    def _regenerate_key(self) -> None:
        self.cfg.new_key()
        self.cfg.set("paired_once", False)
        self.server.set_key(self.cfg.key)
        uri, hosts = self._pairing_uri()
        self._pair_dialog.set_pairing(uri, self.name, hosts, self.cfg["port"])
        self._update_status()

    # ---------------- window ----------------
    def show_window(self) -> None:
        w = self.window
        if w.isMinimized():
            w.setWindowState((w.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
        w.show()
        w.raise_()
        w.activateWindow()

    def apply_theme(self) -> None:
        self.app.setStyleSheet(theme.stylesheet(theme.palette()))
        self._update_status()

    def quit(self) -> None:
        self.server.stop()
        self.app.quit()


def _set_app_id() -> None:
    if winsys.IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DeskLink.Desktop")
        except Exception:
            pass


def main() -> int:
    _set_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("DeskLink")
    app.setStyle("Fusion")
    app.setWindowIcon(theme.app_icon())
    if winsys.IS_WINDOWS:
        f = app.font()
        f.setFamily("Segoe UI")
        f.setPointSizeF(10)
        app.setFont(f)

    # single instance: a second launch just brings the first window forward
    instance_name = f"DeskLink-{getpass.getuser()}"
    probe = QLocalSocket()
    probe.connectToServer(instance_name)
    if probe.waitForConnected(300):
        probe.write(b"show")
        probe.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer(instance_name)
    instance = QLocalServer()
    instance.listen(instance_name)

    ctl = Controller(app)
    instance.newConnection.connect(lambda: (instance.nextPendingConnection(), ctl.show_window()))
    # a picture dropped onto DeskLink.exe opens in Photo Fix
    args = [a for a in sys.argv[1:] if os.path.isfile(a)]
    if args:
        ctl.window.set_mode(0)
        ctl.window.photo.load(args[0])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
