# protocol.py
"""
Length-prefixed JSON framing over TCP.

Problem fixed: the original code did socket.send(json.dumps(...).encode()) and
socket.recv(65536) on the other side, assuming each recv() returned exactly one
JSON message. TCP is a byte stream: two fast pushes can coalesce or a single push
can be split. That caused random "invalid JSON" / dropped clients.

This module adds simple framing:

    [4 bytes big-endian length N][N bytes UTF-8 JSON payload]

Use Framed(sock) on both ends. Framed.send(obj) serializes+frames+sends the
whole frame atomically (sendall). Framed.recv_once(timeout) returns one full
decoded message or None on timeout. Partial reads are buffered internally so
callers can poll with a short timeout without losing data mid-message.
"""

import json
import select
import socket
import struct
import threading
import time

HEADER = struct.Struct(">I")
MAX_MSG_SIZE = 16 * 1024 * 1024  # 16 MB safety limit


class ProtocolError(Exception):
    """Raised on malformed frames (oversize / bad JSON / peer closed)."""


class Framed:
    """Length-prefixed JSON framing wrapper around a blocking TCP socket."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self._buf = bytearray()
        self._closed = False
        # sendall() с разных потоков на одном и том же сокете может перемешать
        # байты и сломать фрейминг. Сериализуем send'ы одним локом.
        self._send_lock = threading.Lock()

    def send(self, obj) -> None:
        """Serialize obj to JSON and send as a single framed message."""
        if self._closed:
            raise ProtocolError("socket is closed")
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        frame = HEADER.pack(len(data)) + data
        with self._send_lock:
            self.sock.sendall(frame)

    def recv_once(self, timeout=None):
        """
        Return one full decoded message, or None if nothing was available within
        `timeout` seconds. Raises ProtocolError on peer disconnect or bad frame.

        timeout=None => block indefinitely. Loops recv() calls until a full
        frame is in the buffer or the deadline passes, so large messages
        (>65 KB) aren't returned partially.

        Важно: используем ``select.select()`` вместо ``sock.settimeout()``,
        потому что ``settimeout`` аффектит ВСЕ операции на сокете (в т.ч.
        ``sendall`` из других потоков) и мог спорадически дропать клиентов
        при параллельной отправке state из GM-потока.
        """
        msg = self._try_extract()
        if msg is not None:
            return msg

        deadline = None if timeout is None else time.monotonic() + timeout

        while True:
            if deadline is None:
                remaining = None
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._try_extract()

            # select() не трогает socket.timeout — так что параллельный
            # sendall() из другого потока не получает «короткий» таймаут и
            # не упадёт с socket.timeout → спасает от самопроизвольных
            # дисконнектов клиентов при отправке state из GM-потока.
            try:
                ready, _, _ = select.select([self.sock], [], [], remaining)
            except (OSError, ValueError) as e:
                self._closed = True
                raise ProtocolError(f"select error: {e}") from e
            if not ready:
                return self._try_extract()

            try:
                data = self.sock.recv(65536)
            except ConnectionError as e:
                self._closed = True
                raise ProtocolError(f"connection lost: {e}") from e
            except OSError as e:
                self._closed = True
                raise ProtocolError(f"socket error: {e}") from e

            if not data:
                self._closed = True
                raise ProtocolError("peer closed connection")
            self._buf.extend(data)
            msg = self._try_extract()
            if msg is not None:
                return msg

    def _try_extract(self):
        if len(self._buf) < HEADER.size:
            return None
        (length,) = HEADER.unpack_from(self._buf, 0)
        if length > MAX_MSG_SIZE:
            raise ProtocolError(f"message too large: {length} bytes")
        end = HEADER.size + length
        if len(self._buf) < end:
            return None
        payload = bytes(self._buf[HEADER.size:end])
        del self._buf[:end]
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ProtocolError(f"invalid frame payload: {e}") from e

    def close(self) -> None:
        self._closed = True
        try:
            self.sock.close()
        except Exception:
            pass
