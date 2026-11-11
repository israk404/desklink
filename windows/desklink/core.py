"""Settings, the sent-files list and small helpers (no Qt here)."""
from __future__ import annotations

import json
import os
import platform
import socket
import sys
import time
from pathlib import Path

from . import protocol


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    path = Path(base) / "DeskLink"
    path.mkdir(parents=True, exist_ok=True)
    return path


def captures_dir() -> Path:
    path = app_dir() / "captures"
    path.mkdir(parents=True, exist_ok=True)
    return path


def now_ms() -> int:
    return int(time.time() * 1000)


def pc_name() -> str:
    return (os.environ.get("COMPUTERNAME") or platform.node() or "My PC")[:40]


def time_ago(ts_ms: int | None) -> str:
    if not ts_ms:
        return ""
    sec = max(0, int(time.time() - ts_ms / 1000))
    if sec < 45:
        return "just now"
    if sec < 3600:
        return f"{max(1, sec // 60)} min ago"
    if sec < 86400:
        return f"{sec // 3600} h ago"
    return time.strftime("%d %b", time.localtime(ts_ms / 1000))


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, path)


class Config:
    DEFAULTS = {
        "port": protocol.DEFAULT_PORT,
        "paired_once": False,
        "mode": 0,               # which of the three modes was open last
        "auto_fix": False,       # Photo Fix: fix colours on every new photo
        "open_dir": "",          # last folder a photo was opened from
        "save_dir": "",          # last folder a fixed photo was saved to
    }

    def __init__(self, path: Path | None = None):
        self.path = path or app_dir() / "config.json"
        data = _read_json(self.path, {})
        self.values = {**self.DEFAULTS, **{k: v for k, v in data.items() if k in self.DEFAULTS}}
        try:
            self.key = protocol.unb64u(data["key"])
            assert len(self.key) == 32
        except Exception:
            self.key = protocol.new_key()
            self.save()

    def __getitem__(self, name):
        return self.values[name]

    def set(self, name, value) -> None:
        self.values[name] = value
        self.save()

    def new_key(self) -> None:
        self.key = protocol.new_key()
        self.save()

    def save(self) -> None:
        _write_json(self.path, {**self.values, "key": protocol.b64u(self.key)})


class FileList:
    """Files sent to the phone from this PC."""
    LIMIT = 60

    def __init__(self, path: Path | None = None):
        self.path = path or app_dir() / "files.json"
        data = _read_json(self.path, [])
        self.entries: list[dict] = [e for e in data if isinstance(e, dict) and "id" in e] if isinstance(data, list) else []
        for e in self.entries:  # anything unfinished when the app closed is gone
            if e.get("status") == "sending":
                e["status"] = "failed"

    def save(self) -> None:
        _write_json(self.path, self.entries[: self.LIMIT])

    def add(self, entry: dict) -> dict:
        self.entries.insert(0, entry)
        del self.entries[self.LIMIT:]
        self.save()
        return entry

    def find(self, entry_id: str) -> dict | None:
        return next((e for e in self.entries if e["id"] == entry_id), None)

    def remove(self, entry_id: str) -> None:
        self.entries = [e for e in self.entries if e["id"] != entry_id]
        self.save()

    def clear_finished(self) -> None:
        self.entries = [e for e in self.entries if e.get("status") == "sending"]
        self.save()


_VIRTUAL_ADAPTERS = ("loopback", "vethernet", "virtualbox", "vmware", "hyper-v", "wsl", "docker", "vbox", "npcap")


def local_ipv4s(limit: int = 5) -> list[str]:
    """IPv4 addresses the phone can try, best first (Wi-Fi/LAN, then Tailscale/VPN)."""
    found: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet is sent; this just picks the default route
        found.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        import psutil
        stats = psutil.net_if_stats()
        for name, addrs in psutil.net_if_addrs().items():
            st = stats.get(name)
            if not st or not st.isup or any(v in name.lower() for v in _VIRTUAL_ADAPTERS):
                continue
            for a in addrs:
                if a.family == socket.AF_INET:
                    found.append(a.address)
    except Exception:
        pass
    out: list[str] = []
    for ip in found:
        if ip.startswith(("127.", "169.254.", "0.")) or ip in out:
            continue
        out.append(ip)
    ordered = sorted(enumerate(out), key=lambda p: (p[1].startswith("100."), p[0]))
    return [ip for _, ip in ordered][:limit]


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))
