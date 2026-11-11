"""DeskLink wire protocol (PC side). Mirrors android/.../Protocol.kt — keep the two in sync.

Transport: plain TCP, every frame encrypted + authenticated with AES-256-GCM.

Handshake
  phone -> PC : b"DLR1" + 16 random bytes (client nonce)
  PC -> phone : b"DLR1" + 16 random bytes (server nonce)
  both derive  session_key = HMAC-SHA256(pair_key, b"desklink-session-v1" + client_nonce + server_nonce)

Frames (both directions): 4-byte big-endian length + AES-GCM(ciphertext || 16-byte tag).
The GCM nonce is never sent: it is 4-byte direction id + 8-byte frame counter, so each side
expects frames strictly in order and replayed / reordered frames fail to decrypt.
Plaintext of every frame is a UTF-8 JSON object with a "t" (type) field.

Discovery (UDP broadcast, port DISCOVERY_PORT), so the phone finds the PC when its IP changes:
  phone -> broadcast : b"DLD1Q" + nonce16 + HMAC(pair_key, b"disc-q" + nonce)[:16]
  PC -> phone        : b"DLD1R" + HMAC(pair_key, b"disc-r" + nonce + port2 + name)[:16] + port2 + name

Pairing (only while "Pair phone" is open on the PC) — ECDH P-256 + a 6-digit code both screens show:
  phone -> broadcast : b"DLD1P" + nonce16            PC -> phone : b"DLD1A" + nonce16 + port2 + name
  phone -> PC (TCP)  : b"DLP1" + u16 len + phone public key (X.509 DER) + u8 len + device name
  PC -> phone        : b"DLP1" + u16 len + PC public key   (len 0 = pairing isn't open)
  both: kenc = HMAC-SHA256(ecdh_secret, b"desklink-pair-v1" + phone_pub + pc_pub); code = first 4 bytes of
        HMAC(kenc, b"code") mod 1e6. A man-in-the-middle would cause different codes on the two screens.
  PC -> phone (after the user clicks Allow): u32 len + nonce12 + AES-GCM(kenc, JSON, aad=b"pair")
        JSON = {"ok": true, "k": pair_key, "p": port, "h": [ips], "n": pc name}   or {"ok": false}
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
from urllib.parse import quote

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = 2
MAGIC = b"DLR1"
PAIR_MAGIC = b"DLP1"
DISC_MAGIC = b"DLD1"
DEFAULT_PORT = 47831
DISCOVERY_PORT = 47832
NONCE_LEN = 16
MAX_FRAME = 8 * 1024 * 1024
MAX_TEXT = 1_000_000  # characters per clipboard item
DIR_CLIENT = 1  # phone -> PC
DIR_SERVER = 2  # PC -> phone


def new_key() -> bytes:
    return os.urandom(32)


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def session_key(pair_key: bytes, client_nonce: bytes, server_nonce: bytes) -> bytes:
    return hmac.new(pair_key, b"desklink-session-v1" + client_nonce + server_nonce, hashlib.sha256).digest()


class SecureChannel:
    """Encrypts outgoing and decrypts incoming frames for one connection."""

    def __init__(self, skey: bytes, send_dir: int, recv_dir: int):
        self._aes = AESGCM(skey)
        self._send_dir = send_dir
        self._recv_dir = recv_dir
        self._send_ctr = 0
        self._recv_ctr = 0

    @staticmethod
    def _nonce(direction: int, counter: int) -> bytes:
        return struct.pack(">IQ", direction, counter)

    def seal(self, obj: dict) -> bytes:
        """Return a complete frame (length prefix + ciphertext) for a JSON object."""
        return self.seal_bytes(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def seal_bytes(self, plain: bytes) -> bytes:
        ct = self._aes.encrypt(self._nonce(self._send_dir, self._send_ctr), plain, None)
        self._send_ctr += 1
        return struct.pack(">I", len(ct)) + ct

    def open_raw(self, ct: bytes) -> bytes:
        """Decrypt one frame body. Raises on tampering, wrong key or out-of-order frames."""
        plain = self._aes.decrypt(self._nonce(self._recv_dir, self._recv_ctr), ct, None)
        self._recv_ctr += 1
        return plain

    def open(self, ct: bytes) -> dict:
        obj = json.loads(self.open_raw(ct).decode("utf-8"))
        if not isinstance(obj, dict):
            raise ValueError("frame is not a JSON object")
        return obj


# ---------------- file transfer ----------------
# A transfer is: JSON {"t":"file_offer", id, name, mime, size, kind, ...}, then binary chunk frames,
# then JSON {"t":"file_done", id, sha256, size}. The receiver answers {"t":"file_ack", id, ok} and either
# side may send {"t":"file_cancel", id}. kind = "file" (shared file) or "clip_image" (clipboard image).
# Chunk frame plaintext: 0x01 + 12-byte ASCII transfer id + data (JSON frames always start with "{").
CHUNK_SIZE = 256 * 1024
CHUNK_TAG = 0x01
ID_LEN = 12
MAX_FILE = 4 * 1024 ** 3      # 4 GB per file
MAX_CLIP_IMAGE = 40 * 1024 ** 2


def chunk_frame_plain(transfer_id: str, data: bytes) -> bytes:
    return bytes([CHUNK_TAG]) + transfer_id.encode("ascii") + data


def parse_chunk(plain: bytes) -> tuple[str, bytes]:
    return plain[1:1 + ID_LEN].decode("ascii", "replace"), plain[1 + ID_LEN:]


def _mac(key: bytes, label: bytes, data: bytes) -> bytes:
    return hmac.new(key, label + data, hashlib.sha256).digest()[:16]


def discovery_request(pair_key: bytes) -> bytes:
    nonce = os.urandom(NONCE_LEN)
    return DISC_MAGIC + b"Q" + nonce + _mac(pair_key, b"disc-q", nonce)


def parse_discovery_request(pair_key: bytes, data: bytes) -> bytes | None:
    """Return the request nonce if this is a valid request from a phone paired with this key."""
    if len(data) != 37 or data[:5] != DISC_MAGIC + b"Q":
        return None
    nonce, mac = data[5:21], data[21:37]
    return nonce if hmac.compare_digest(mac, _mac(pair_key, b"disc-q", nonce)) else None


def discovery_response(pair_key: bytes, nonce: bytes, port: int, name: str) -> bytes:
    body = struct.pack(">H", port) + name.encode("utf-8")[:64]
    return DISC_MAGIC + b"R" + _mac(pair_key, b"disc-r", nonce + body) + body


def parse_discovery_response(pair_key: bytes, nonce: bytes, data: bytes) -> tuple[int, str] | None:
    if len(data) < 23 or data[:5] != DISC_MAGIC + b"R":
        return None
    mac, body = data[5:21], data[21:]
    if not hmac.compare_digest(mac, _mac(pair_key, b"disc-r", nonce + body)):
        return None
    return struct.unpack(">H", body[:2])[0], body[2:].decode("utf-8", "replace")


def pairing_uri(pair_key: bytes, port: int, hosts: list[str], name: str) -> str:
    return (
        f"desklink://pair?v={VERSION}&k={b64u(pair_key)}&p={port}"
        f"&h={','.join(hosts)}&n={quote(name, safe='')}"
    )


# ---------------- pairing ----------------
def pair_discovery_request() -> tuple[bytes, bytes]:
    nonce = os.urandom(NONCE_LEN)
    return DISC_MAGIC + b"P" + nonce, nonce


def parse_pair_discovery_request(data: bytes) -> bytes | None:
    return data[5:21] if len(data) == 21 and data[:5] == DISC_MAGIC + b"P" else None


def pair_discovery_response(nonce: bytes, port: int, name: str) -> bytes:
    return DISC_MAGIC + b"A" + nonce + struct.pack(">H", port) + name.encode("utf-8")[:64]


class PairKeys:
    """The PC's half of one pairing attempt."""

    def __init__(self):
        self._private = ec.generate_private_key(ec.SECP256R1())
        self.public_der = self._private.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)

    def derive(self, phone_pub_der: bytes) -> tuple[bytes, str]:
        """Returns (encryption key for the result, 6-digit confirmation code)."""
        peer = serialization.load_der_public_key(phone_pub_der)
        if not isinstance(peer, ec.EllipticCurvePublicKey) or not isinstance(peer.curve, ec.SECP256R1):
            raise ValueError("unexpected key type")
        shared = self._private.exchange(ec.ECDH(), peer)
        return pair_derive(shared, phone_pub_der, self.public_der)


def pair_derive(shared: bytes, phone_pub: bytes, pc_pub: bytes) -> tuple[bytes, str]:
    kenc = hmac.new(shared, b"desklink-pair-v1" + phone_pub + pc_pub, hashlib.sha256).digest()
    code = int.from_bytes(hmac.new(kenc, b"code", hashlib.sha256).digest()[:4], "big") % 1_000_000
    return kenc, f"{code:06d}"


def pair_seal(kenc: bytes, obj: dict) -> bytes:
    nonce = os.urandom(12)
    body = nonce + AESGCM(kenc).encrypt(nonce, json.dumps(obj).encode("utf-8"), b"pair")
    return struct.pack(">I", len(body)) + body


def pair_open(kenc: bytes, body: bytes) -> dict:
    return json.loads(AESGCM(kenc).decrypt(body[:12], body[12:], b"pair"))
