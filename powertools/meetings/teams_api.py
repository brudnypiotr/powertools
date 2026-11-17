"""Teams' own mute button, read through the local API of the new Teams (no admin rights: Teams > Settings >
Privacy > Third-party app API > Manage API, switch it on).

Teams runs a WebSocket server on 127.0.0.1:8124 and sends `meetingUpdate` messages (isMuted, isInMeeting,
canPair). The first time in a call powertools asks to pair: Teams shows "Allow powertools?" once, then sends a
token that is kept in %LOCALAPPDATA%\\powertools\\teams_api.json and used on every later connection.
Muting in Teams does not silence the microphone itself (Teams only stops sending it), so the recorder needs this
to leave out what is said while muted. Plain stdlib WebSocket client: no new packages.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import socket
import struct
import threading
import time
from typing import Callable
from urllib.parse import urlencode

from ..config import data_dir

log = logging.getLogger("powertools.teams_api")
HOST, PORT = "127.0.0.1", 8124
APP = {"protocol-version": "2.0.0", "manufacturer": "powertools", "device": "PC", "app": "powertools",
       "app-version": "1.0"}


class WebSocket:
    """Just enough of RFC 6455 for a local text-message API: client handshake, masked text frames out,
    text frames in (fragmented or not), ping -> pong, close."""

    def __init__(self, sock: socket.socket, rest: bytes = b""):
        self.sock, self.buf = sock, rest
        self.lock = threading.Lock()

    @classmethod
    def connect(cls, host: str, port: int, path: str, timeout: float = 5) -> "WebSocket":
        sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                sock.close()
                raise ConnectionError("the connection closed during the handshake")
            data += chunk
        head, rest = data.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            sock.close()
            raise ConnectionError(head.split(b"\r\n", 1)[0].decode(errors="replace"))
        sock.settimeout(None)
        return cls(sock, rest)

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _frame(self) -> tuple[bool, int, bytes]:
        b0, b1 = self._read(2)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._read(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._read(8))[0]
        mask = self._read(4) if b1 & 0x80 else b""
        data = self._read(n)
        if mask:
            data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
        return bool(b0 & 0x80), b0 & 0x0F, data

    def _send(self, opcode: int, data: bytes) -> None:
        n = len(data)
        head = bytes([0x80 | opcode])
        head += bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 \
            else bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        with self.lock:
            self.sock.sendall(head + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(data)))

    def send(self, text: str) -> None:
        self._send(0x1, text.encode())

    def recv(self) -> str | None:
        """The next text message; None when the server closed the connection."""
        parts: list[bytes] = []
        while True:
            fin, op, data = self._frame()
            if op == 0x8:
                return None
            if op == 0x9:
                self._send(0xA, data)
                continue
            if op in (0x1, 0x2, 0x0):
                parts.append(data)
                if fin:
                    return b"".join(parts).decode(errors="replace")

    def close(self) -> None:
        try:
            self._send(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class TeamsLink:
    """Keeps a connection to Teams and calls on_mute(True/False) when the Teams mute button changes."""

    def __init__(self, on_mute: Callable[[bool], None], token_file=None, host: str = HOST, port: int = PORT,
                 retry: float = 15.0):
        self.on_mute = on_mute
        self.token_file = token_file or data_dir() / "teams_api.json"
        self.host, self.port, self.retry = host, port, retry
        self.connected = False
        self.in_meeting = False
        self.muted: bool | None = None   # None: Teams has not said (no call, or not paired yet)
        self.error = ""
        self._ws: WebSocket | None = None
        self._pair_asked = False
        self._req = 0
        self._stop = threading.Event()

    # ------------------------------------------------------------------ token
    @property
    def token(self) -> str:
        try:
            return json.loads(self.token_file.read_text(encoding="utf-8")).get("token", "")
        except (OSError, ValueError):
            return ""

    def _save_token(self, token: str) -> None:
        tmp = self.token_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"token": token, "paired": time.time()}), encoding="utf-8")
        tmp.replace(self.token_file)
        log.info("paired with Teams")

    def view(self) -> dict:
        return {"connected": self.connected, "paired": bool(self.token), "in_meeting": self.in_meeting,
                "muted": self.muted, "error": self.error}

    # ------------------------------------------------------------------ messages
    def handle(self, msg: dict) -> None:
        if msg.get("tokenRefresh"):
            self._save_token(str(msg["tokenRefresh"]))
            return
        update = msg.get("meetingUpdate")
        if not isinstance(update, dict):
            if msg.get("response"):
                log.info("Teams: %s", msg.get("response"))
            return
        perms = update.get("meetingPermissions") or {}
        state = update.get("meetingState")
        if perms.get("canPair") and state is None and not self._pair_asked and self._ws:
            # in a call, but no meeting state: not paired yet (or the token was revoked); Teams asks the user once
            self._pair_asked = True
            self._send({"action": "pair", "parameters": {}})
        if isinstance(state, dict):
            self.in_meeting = bool(state.get("isInMeeting"))
            muted = bool(state.get("isMuted")) if self.in_meeting else False
            if muted != self.muted:
                self.muted = muted
                self.on_mute(muted)

    def _send(self, body: dict) -> None:
        self._req += 1
        try:
            self._ws.send(json.dumps({**body, "requestId": self._req}))
        except (OSError, AttributeError) as e:
            log.info("cannot send to Teams: %s", e)

    # ------------------------------------------------------------------ connection
    def _session(self) -> None:
        q = {"token": self.token, **APP} if self.token else dict(APP)
        self._ws = WebSocket.connect(self.host, self.port, "/?" + urlencode(q))
        self.connected, self.error = True, ""
        log.info("connected to the Teams local API")
        try:
            while not self._stop.is_set():
                text = self._ws.recv()
                if text is None:
                    break
                try:
                    msg = json.loads(text)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    self.handle(msg)
        finally:
            self._ws.close()
            self._ws = None
            self.connected, self.in_meeting = False, False
            if self.muted:
                self.muted = None
                self.on_mute(False)  # no word from Teams: record the microphone again
            self.muted = None

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self._session()
                except (OSError, ConnectionError) as e:
                    # Teams closed, or the Third-party app API is off: try again later, quietly
                    self.error = f"{type(e).__name__}: {e}"[:200]
                except Exception:
                    log.exception("Teams local API")
                self._stop.wait(self.retry)
        threading.Thread(target=loop, name="teams-api", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._ws:
            self._ws.close()
