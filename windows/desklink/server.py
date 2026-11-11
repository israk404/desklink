"""TCP sync server + UDP discovery responder, running on the Qt event loop (no threads)."""
from __future__ import annotations

import collections
import itertools
import json
import os
import struct

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QAbstractSocket, QHostAddress, QTcpServer, QTcpSocket, QUdpSocket

from . import protocol
from .transfers import Outgoing

HANDSHAKE_TIMEOUT_MS = 15_000
IDLE_TIMEOUT_MS = 75_000  # phone pings every ~20 s
SEND_WATERMARK = 1024 * 1024  # keep at most ~1 MB of file data queued in the socket
_ids = itertools.count(1)


class PhoneConnection(QObject):
    def __init__(self, server: "SyncServer", sock: QTcpSocket):
        super().__init__(server)
        self.server = server
        self.sock = sock
        self.id = next(_ids)
        self.device = "Phone"
        self.ready = False
        self._buf = bytearray()
        self._stage = "handshake"
        self._channel: protocol.SecureChannel | None = None
        self._closed = False
        self.pairing = False      # this connection is a pairing attempt, not a phone session
        self.pair_code = ""
        self._pair_key = b""
        self._pair_done = False
        self._outgoing: collections.deque[Outgoing] = collections.deque()
        self._idle = QTimer(self, singleShot=True, interval=HANDSHAKE_TIMEOUT_MS)
        self._idle.timeout.connect(self.close)
        self._idle.start()
        sock.readyRead.connect(self._on_read)
        sock.disconnected.connect(self._on_disconnected)
        sock.bytesWritten.connect(lambda *_: self._pump())
        sock.errorOccurred.connect(lambda *_: self._on_disconnected())

    @property
    def address(self) -> str:
        return self.sock.peerAddress().toString().replace("::ffff:", "")

    def send(self, obj: dict) -> None:
        if self.ready and not self._closed and self._channel:
            self.sock.write(self._channel.seal(obj))

    def send_file(self, out: Outgoing) -> None:
        """Queue a file: the offer goes out now, data follows as the socket drains."""
        if not self.ready or self._closed:
            if out.on_done:
                out.on_done(out, False)
            return
        self.send(out.offer)
        self._outgoing.append(out)
        self._pump()

    def cancel_file(self, transfer_id: str, tell_phone: bool = True) -> bool:
        for out in self._outgoing:
            if out.id == transfer_id and not out.cancelled:
                out.cancelled = True
                if tell_phone:
                    self.send({"t": "file_cancel", "id": transfer_id})
                return True
        return False

    def _pump(self) -> None:
        while self._outgoing and not self._closed and self.sock.bytesToWrite() < SEND_WATERMARK:
            out = self._outgoing[0]
            if out.cancelled:
                self._outgoing.popleft()
                out.close()
                if out.on_done:
                    out.on_done(out, False)
                continue
            data = out.next_chunk()
            if data:
                self.sock.write(self._channel.seal_bytes(protocol.chunk_frame_plain(out.id, data)))
                if out.on_progress:
                    out.on_progress(out)
                continue
            self.send(out.done_message())
            self._outgoing.popleft()
            out.close()
            if out.on_done:
                out.on_done(out, True)

    def close(self) -> None:
        if not self._closed:
            self.sock.abort()
            self._on_disconnected()

    def _on_disconnected(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._idle.stop()
        was_ready, self.ready = self.ready, False
        while self._outgoing:
            out = self._outgoing.popleft()
            out.close()
            if out.on_done:
                out.on_done(out, False)
        self.server._forget(self, was_ready)
        if self.pairing and not self._pair_done:
            self.server.pair_gone.emit(self)
        self.sock.deleteLater()
        self.deleteLater()

    def _on_read(self) -> None:
        self._buf += self.sock.readAll().data()
        try:
            self._process()
        except Exception as exc:  # bad key, tampering, garbage: drop the connection
            self.server.log.emit(f"Dropped {self.address}: {type(exc).__name__}")
            self.close()

    def finish_pairing(self, payload: dict | None) -> None:
        """Answer a pairing request: payload = pairing details (Allow) or None (Deny)."""
        if self._closed or self._pair_done:
            return
        self._pair_done = True
        result = {"ok": True, **payload} if payload else {"ok": False}
        self.sock.write(protocol.pair_seal(self._pair_key, result))
        self.sock.flush()
        self.sock.disconnectFromHost()

    def _process_pairing(self) -> None:
        if self._stage == "pair-wait":
            self._buf.clear()
            return
        if len(self._buf) < 6:
            return
        (publen,) = struct.unpack(">H", self._buf[4:6])
        if publen < 32 or publen > 512:
            raise ValueError("bad key length")
        if len(self._buf) < 6 + publen + 1:
            return
        namelen = self._buf[6 + publen]
        end = 7 + publen + namelen
        if len(self._buf) < end:
            return
        phone_pub = bytes(self._buf[6:6 + publen])
        self.device = (bytes(self._buf[7 + publen:end]).decode("utf-8", "replace").strip() or "Phone")[:40]
        self._buf.clear()
        if not self.server.pairing_open:
            self._pair_done = True
            self.sock.write(protocol.PAIR_MAGIC + struct.pack(">H", 0))
            self.sock.flush()
            self.sock.disconnectFromHost()
            return
        keys = protocol.PairKeys()
        self._pair_key, self.pair_code = keys.derive(phone_pub)
        self.sock.write(protocol.PAIR_MAGIC + struct.pack(">H", len(keys.public_der)) + keys.public_der)
        self._stage = "pair-wait"
        self._idle.setInterval(120_000)
        self._idle.start()
        self.server.pair_request.emit(self)

    def _process(self) -> None:
        if self._stage == "handshake" and self._buf[:4] == protocol.PAIR_MAGIC:
            self.pairing = True
            self._stage = "pair"
        if self.pairing:
            self._process_pairing()
            return
        if self._stage == "handshake":
            if len(self._buf) < 4 + protocol.NONCE_LEN:
                return
            if self._buf[:4] != protocol.MAGIC:
                raise ValueError("not a DeskLink client")
            client_nonce = bytes(self._buf[4:20])
            del self._buf[:20]
            server_nonce = os.urandom(protocol.NONCE_LEN)
            self.sock.write(protocol.MAGIC + server_nonce)
            skey = protocol.session_key(self.server.key, client_nonce, server_nonce)
            self._channel = protocol.SecureChannel(skey, protocol.DIR_SERVER, protocol.DIR_CLIENT)
            self._stage = "auth"
        while len(self._buf) >= 4 and not self._closed:
            (size,) = struct.unpack(">I", self._buf[:4])
            if size <= 16 or size > protocol.MAX_FRAME:
                raise ValueError("bad frame size")
            if len(self._buf) < 4 + size:
                return
            body = bytes(self._buf[4:4 + size])
            del self._buf[:4 + size]
            plain = self._channel.open_raw(body)
            if plain[:1] == bytes([protocol.CHUNK_TAG]) and self._stage == "ready":
                self._idle.start()
                tid, data = protocol.parse_chunk(plain)
                self.server.phone_chunk.emit(self, tid, data)
                continue
            msg = json.loads(plain.decode("utf-8"))
            if not isinstance(msg, dict):
                raise ValueError("frame is not a JSON object")
            if self._stage == "auth":
                if msg.get("t") != "hello":
                    raise ValueError("expected hello")
                self.device = (str(msg.get("device") or "Phone").strip() or "Phone")[:40]
                self._stage = "ready"
                self.ready = True
                self._idle.setInterval(IDLE_TIMEOUT_MS)
                self._idle.start()
                self.server.phone_ready.emit(self)
                continue
            self._idle.start()
            if msg.get("t") == "ping":
                self.send({"t": "pong"})
            else:
                self.server.phone_message.emit(self, msg)


class SyncServer(QObject):
    phone_ready = Signal(object)            # PhoneConnection
    phone_message = Signal(object, object)  # PhoneConnection, dict
    phone_gone = Signal(object)             # PhoneConnection
    phone_chunk = Signal(object, str, object)  # PhoneConnection, transfer id, bytes
    pair_request = Signal(object)           # PhoneConnection with .device and .pair_code
    pair_gone = Signal(object)              # the phone cancelled a pairing request
    log = Signal(str)

    def __init__(self, key: bytes, port: int, name: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.port = port
        self.name = name
        self.error = ""
        self.pairing_open = False  # True while the "Pair phone" window is open
        self._phones: list[PhoneConnection] = []
        self._tcp = QTcpServer(self)
        self._tcp.newConnection.connect(self._on_new_connection)
        self._udp = QUdpSocket(self)
        self._udp.readyRead.connect(self._on_udp)

    def start(self) -> bool:
        any4 = QHostAddress(QHostAddress.SpecialAddress.AnyIPv4)
        if not self._tcp.listen(any4, self.port):
            self.error = f"Port {self.port} is busy ({self._tcp.errorString()}). Is DeskLink already running?"
            self.log.emit(self.error)
            return False
        flags = QAbstractSocket.BindFlag.ShareAddress | QAbstractSocket.BindFlag.ReuseAddressHint
        if not self._udp.bind(any4, protocol.DISCOVERY_PORT, flags):
            self.log.emit(f"Discovery disabled: {self._udp.errorString()}")
        self.log.emit(f"Listening on port {self.port}")
        return True

    def stop(self) -> None:
        for phone in list(self._phones):
            phone.close()
        self._tcp.close()
        self._udp.close()

    def set_key(self, key: bytes) -> None:
        """Switch to a new pairing key: every phone paired with the old one is disconnected."""
        self.key = key
        for phone in list(self._phones):
            phone.close()

    @property
    def phones(self) -> list[PhoneConnection]:
        return [p for p in self._phones if p.ready]

    def broadcast(self, obj: dict) -> None:
        for phone in self.phones:
            phone.send(obj)

    def _on_new_connection(self) -> None:
        while self._tcp.hasPendingConnections():
            sock = self._tcp.nextPendingConnection()
            sock.setSocketOption(QAbstractSocket.SocketOption.LowDelayOption, 1)
            sock.setSocketOption(QAbstractSocket.SocketOption.KeepAliveOption, 1)
            self._phones.append(PhoneConnection(self, sock))

    def _forget(self, phone: PhoneConnection, was_ready: bool) -> None:
        if phone in self._phones:
            self._phones.remove(phone)
        if was_ready:
            self.phone_gone.emit(phone)

    def _on_udp(self) -> None:
        while self._udp.hasPendingDatagrams():
            dgram = self._udp.receiveDatagram(256)
            data = dgram.data().data()
            pair_nonce = protocol.parse_pair_discovery_request(data)
            if pair_nonce is not None:
                if self.pairing_open:
                    reply = protocol.pair_discovery_response(pair_nonce, self.port, self.name)
                    self._udp.writeDatagram(reply, dgram.senderAddress(), dgram.senderPort())
                continue
            nonce = protocol.parse_discovery_request(self.key, data)
            if nonce is None:
                continue
            reply = protocol.discovery_response(self.key, nonce, self.port, self.name)
            self._udp.writeDatagram(reply, dgram.senderAddress(), dgram.senderPort())
