"""Minimal Source-RCON client for driving the local test server."""
from __future__ import annotations

import socket
import struct

LOGIN, COMMAND, RESPONSE = 3, 2, 0


class Rcon:
    def __init__(self, host: str = "127.0.0.1", port: int = 25598, password: str = "localtest", timeout: float = 10.0):
        self.timeout = timeout
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self._id = 0
        self._send(LOGIN, password)
        rid, rtype, _ = self._recv()
        if rid == -1:
            raise PermissionError("rcon login refused")

    def _send(self, ptype: int, payload: str) -> int:
        self._id += 1
        body = struct.pack("<ii", self._id, ptype) + payload.encode("utf-8") + b"\x00\x00"
        self.sock.sendall(struct.pack("<i", len(body)) + body)
        return self._id

    def _recv(self) -> tuple[int, int, str]:
        hdr = self._read(4)
        (length,) = struct.unpack("<i", hdr)
        body = self._read(length)
        rid, rtype = struct.unpack("<ii", body[:8])
        return rid, rtype, body[8:-2].decode("utf-8", errors="replace")

    def _read(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("rcon socket closed")
            buf += chunk
        return buf

    def command(self, cmd: str) -> str:
        cid = self._send(COMMAND, cmd)
        rid, rtype, text = self._recv()
        out = [text] if rid == cid else []
        # Long outputs arrive as several packets with the same id; drain briefly.
        self.sock.settimeout(0.15)
        try:
            while True:
                rid, rtype, text = self._recv()
                if rid == cid:
                    out.append(text)
        except TimeoutError:
            pass
        finally:
            self.sock.settimeout(self.timeout)
        return "".join(out)

    def close(self) -> None:
        self.sock.close()

    def __enter__(self) -> Rcon:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
