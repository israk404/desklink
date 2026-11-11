"""A pretend phone that speaks the DeskLink protocol — used by the tests."""
import os, queue, socket, struct, threading, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from desklink import protocol as P


class FakePhone(threading.Thread):
    def __init__(self, key, port, device="Test Phone", host="127.0.0.1"):
        super().__init__(daemon=True)
        self.key, self.port, self.device, self.host = key, port, device, host
        self.inbox = queue.Queue()
        self.closed = threading.Event()
        self.lock = threading.Lock()

    def run(self):
        s = socket.create_connection((self.host, self.port), timeout=5)
        s.settimeout(None)   # the connect timeout must not turn into a read timeout
        self.sock = s
        cn = os.urandom(16)
        s.sendall(P.MAGIC + cn)
        hello = self._read_exact(20)
        assert hello[:4] == P.MAGIC, hello
        self.ch = P.SecureChannel(P.session_key(self.key, cn, hello[4:]), P.DIR_CLIENT, P.DIR_SERVER)
        self.send({"t": "hello", "v": 1, "device": self.device})
        try:
            while True:
                (n,) = struct.unpack(">I", self._read_exact(4))
                plain = self.ch.open_raw(self._read_exact(n))
                if plain[:1] == bytes([P.CHUNK_TAG]):
                    tid, data = P.parse_chunk(plain)
                    self.inbox.put({"t": "__chunk__", "id": tid, "data": data})
                else:
                    import json
                    self.inbox.put(json.loads(plain))
        except Exception as e:
            self.inbox.put({"t": "__closed__", "why": repr(e)})
            self.closed.set()

    def _read_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("eof")
            buf += chunk
        return buf

    def drain(self):
        while not self.inbox.empty():
            self.inbox.get_nowait()

    def send(self, obj):
        self.drain()
        with self.lock:
            self.sock.sendall(self.ch.seal(obj))

    def send_file(self, data, name, mime, kind="file", purpose=None, chunk=P.CHUNK_SIZE, cancel_after=None):
        """Stream a file like the phone app does. Returns the transfer id."""
        import hashlib
        tid = os.urandom(6).hex()
        offer = {"t": "file_offer", "id": tid, "kind": kind, "name": name, "mime": mime, "size": len(data)}
        if purpose:
            offer["purpose"] = purpose
        self.send(offer)
        for i in range(0, len(data), chunk):
            with self.lock:
                self.sock.sendall(self.ch.seal_bytes(P.chunk_frame_plain(tid, data[i:i + chunk])))
            if cancel_after is not None and i // chunk + 1 >= cancel_after:
                with self.lock:
                    self.sock.sendall(self.ch.seal({"t": "file_cancel", "id": tid}))
                return tid
        with self.lock:
            self.sock.sendall(self.ch.seal({"t": "file_done", "id": tid, "sha256": hashlib.sha256(data).hexdigest(),
                                            "size": len(data)}))
        return tid

    def receive_file(self, timeout=20):
        """Collect one incoming transfer (offer, chunks, done). Returns (offer, bytes, done)."""
        offer = self.expect("file_offer", timeout)
        buf = bytearray()
        while True:
            msg = self.inbox.get(timeout=timeout)
            if msg.get("t") == "__chunk__" and msg["id"] == offer["id"]:
                buf += msg["data"]
            elif msg.get("t") == "file_done" and msg["id"] == offer["id"]:
                return offer, bytes(buf), msg
            elif msg.get("t") == "__closed__":
                raise AssertionError("closed during transfer")

    def send_quiet(self, obj):
        with self.lock:
            self.sock.sendall(self.ch.seal(obj))

    def expect(self, t, timeout=5):
        while True:
            msg = self.inbox.get(timeout=timeout)
            if msg.get("t") == t:
                return msg
            if msg.get("t") == "__closed__":
                raise AssertionError(f"closed while waiting for {t}: {msg}")


def discover(key, port=P.DISCOVERY_PORT, host="255.255.255.255"):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(2)
    req = P.discovery_request(key)
    s.sendto(req, (host, port))
    data, addr = s.recvfrom(256)
    return addr[0], P.parse_discovery_response(key, req[5:21], data)
