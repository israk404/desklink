"""Send to Phone mode: drop files, they go to the phone, the list shows what happened to each."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QScrollArea, QVBoxLayout, QWidget

from . import core, transfers
from .widgets import button, label, soft_wrap


def file_glyph(mime: str) -> str:
    if mime.startswith("image/"):
        return "🖼"
    if mime.startswith("video/"):
        return "🎬"
    if mime.startswith("audio/"):
        return "🎵"
    if mime == "application/pdf":
        return "📕"
    return "📄"


def file_status(entry: dict) -> str:
    who = entry.get("device") or "phone"
    size = transfers.human_size(entry.get("size"))
    status = entry.get("status")
    if status == "sending":
        total = entry.get("size") or 0
        done = entry.get("progress") or 0
        pct = f"{int(done * 100 / total)}%" if total > 0 else transfers.human_size(done)
        return f"To {who} · Sending {pct} of {size}"
    tail = {"done": "On the phone ✓", "sent": "Sent, waiting for the phone to confirm", "failed": "Failed",
            "cancelled": "Cancelled"}.get(status, "")
    return f"To {who} · {size} · {tail} · {core.time_ago(entry.get('ts'))}"


class FileRow(QFrame):
    def __init__(self, page: "SendPage", entry: dict):
        super().__init__()
        self.setObjectName("row")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 8, 10)
        lay.setSpacing(10)
        icon = QLabel(file_glyph(entry.get("mime", "")))
        icon.setFixedSize(40, 40)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size: 18pt;")
        col = QVBoxLayout()
        col.setSpacing(3)
        name = label(soft_wrap(entry.get("name", "file"), 90), wrap=True)
        name.setMaximumHeight(name.fontMetrics().lineSpacing() + 4)
        self.meta = label(file_status(entry), "muted")
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setRange(0, 1000)
        active = entry.get("status") == "sending"
        self.bar.setVisible(active)
        col.addWidget(name)
        col.addWidget(self.meta)
        col.addWidget(self.bar)
        lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(col, 1)
        eid = entry["id"]
        if active:
            act = button("Cancel", "ghost")
            act.clicked.connect(lambda: page.cancel_file.emit(eid))
        else:
            act = button("Remove", "ghost")
            act.clicked.connect(lambda: page.remove_file.emit(eid))
        lay.addWidget(act, 0, Qt.AlignmentFlag.AlignVCenter)
        self.update_progress(entry)

    def update_progress(self, entry: dict) -> None:
        total = entry.get("size") or 0
        if total > 0:
            self.bar.setValue(int(1000 * (entry.get("progress") or 0) / total))
        self.meta.setText(file_status(entry))


class SendPage(QWidget):
    status = Signal(str)
    pick_files = Signal()
    send_paths = Signal(list)
    cancel_file = Signal(str)
    remove_file = Signal(str)
    clear_list = Signal()

    hint = "Drop files anywhere on this window to send them to your phone."

    def __init__(self):
        super().__init__()
        self._rows: dict[str, FileRow] = {}

        self.drop = QFrame()
        self.drop.setObjectName("drop")
        box = QVBoxLayout(self.drop)
        box.setContentsMargins(24, 30, 24, 30)
        box.setSpacing(10)
        title = label("Drop files here to send them to your phone", "big")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub = label("PDFs, pictures, anything. They show up in DeskLink on the phone, ready to open or print.",
                         "muted", wrap=True)
        self.sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.btn_pick = button("Choose files…", "big")
        box.addWidget(title)
        box.addWidget(self.sub)
        box.addWidget(self.btn_pick, 0, Qt.AlignmentFlag.AlignCenter)

        head = QHBoxLayout()
        head.addWidget(label("SENT FILES", "section"))
        head.addStretch(1)
        self.btn_clear = button("Clear list", "ghost")
        head.addWidget(self.btn_clear)

        self.list_box = QVBoxLayout()
        self.list_box.setContentsMargins(0, 0, 6, 0)
        self.list_box.setSpacing(8)
        self.list_box.addStretch(1)
        inner = QWidget()
        inner.setLayout(self.list_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        self.empty = label("Nothing sent yet.", "empty")

        col = QVBoxLayout()
        col.setSpacing(10)
        col.addWidget(self.drop)
        col.addLayout(head)
        col.addWidget(self.empty)
        col.addWidget(scroll, 1)
        holder = QWidget()
        holder.setLayout(col)
        holder.setMaximumWidth(900)

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 16, 14, 12)
        root.addStretch(1)
        root.addWidget(holder, 6)
        root.addStretch(1)

        self.btn_pick.clicked.connect(self.pick_files)
        self.btn_clear.clicked.connect(self.clear_list)

    def accept_paths(self, paths: list[str]) -> None:
        self.send_paths.emit(paths)

    def set_files(self, entries: list[dict]) -> None:
        while self.list_box.count() > 1:
            item = self.list_box.takeAt(0)
            if item.widget():
                item.widget().hide()          # deleteLater alone leaves the old row painted for a moment
                item.widget().deleteLater()
        self._rows.clear()
        for i, entry in enumerate(entries):
            row = FileRow(self, entry)
            self._rows[entry["id"]] = row
            self.list_box.insertWidget(i, row)
        self.empty.setVisible(not entries)
        self.btn_clear.setVisible(bool(entries))

    def update_file(self, entry: dict) -> None:
        row = self._rows.get(entry["id"])
        if row:
            row.update_progress(entry)
