"""Sending and receiving files over a phone connection (no Qt here except in PhoneConnection's pump)."""
from __future__ import annotations

import hashlib
import io
import mimetypes
import os
import re
import uuid
from pathlib import Path

from . import protocol


def new_id() -> str:
    return uuid.uuid4().hex[: protocol.ID_LEN]


def guess_mime(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def safe_name(name: str) -> str:
    """Strip folders and characters Windows doesn't allow in file names."""
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", os.path.basename(name or "")).strip(" .")
    return base[:150] or "file"


def unique_path(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stem, ext = os.path.splitext(safe_name(name))
    path, n = folder / f"{stem}{ext}", 1
    while path.exists() or path.with_name(path.name + ".part").exists():
        path = folder / f"{stem} ({n}){ext}"
        n += 1
    return path


def human_size(n: int | None) -> str:
    if n is None or n < 0:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


class Outgoing:
    """One file (or in-memory image) being sent to one phone."""

    def __init__(self, offer: dict, source: str | bytes, on_progress=None, on_done=None):
        self.id = offer["id"]
        self.offer = offer
        self.size = offer["size"]
        self._fh = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else open(source, "rb")
        self._sha = hashlib.sha256()
        self.sent = 0
        self.cancelled = False
        self.on_progress = on_progress
        self.on_done = on_done

    def next_chunk(self) -> bytes:
        data = self._fh.read(protocol.CHUNK_SIZE)
        if data:
            self._sha.update(data)
            self.sent += len(data)
        return data

    def done_message(self) -> dict:
        return {"t": "file_done", "id": self.id, "sha256": self._sha.hexdigest(), "size": self.sent}

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


class Incoming:
    """One file arriving from a phone, written to <dest>.part and renamed when complete."""

    def __init__(self, offer: dict, dest: Path):
        self.id = offer["id"]
        self.offer = offer
        self.size = int(offer.get("size") or -1)
        self.dest = dest
        self.part = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.part, "wb")
        self._sha = hashlib.sha256()
        self.received = 0

    def write(self, data: bytes) -> None:
        self._fh.write(data)
        self._sha.update(data)
        self.received += len(data)
        if self.received > protocol.MAX_FILE:
            raise ValueError("file too large")

    def finish(self, sha256: str) -> bool:
        self._fh.close()
        if sha256 != self._sha.hexdigest():
            self.abort()
            return False
        os.replace(self.part, self.dest)
        return True

    def abort(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass
        try:
            self.part.unlink(missing_ok=True)
        except Exception:
            pass
