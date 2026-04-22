"""Тесты framed-протокола (length-prefix TCP)."""
import socket
import threading

import pytest

from protocol import Framed, ProtocolError


def _pair():
    """Создаёт пару связанных сокетов через loopback-сервер."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    acc_sock = {}

    def accept():
        s, _ = srv.accept()
        acc_sock['s'] = s

    t = threading.Thread(target=accept, daemon=True)
    t.start()

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(("127.0.0.1", port))
    t.join(timeout=2)
    srv.close()
    return Framed(client), Framed(acc_sock['s'])


def test_roundtrip_simple():
    a, b = _pair()
    try:
        a.send({"hello": "world"})
        assert b.recv_once(timeout=2) == {"hello": "world"}
    finally:
        a.close(); b.close()


def test_two_messages_back_to_back_are_not_coalesced():
    """Главное, ради чего протокол существует: если отправитель пишет быстро,
    приёмник должен получить оба сообщения по отдельности, а не слипшийся
    невалидный JSON."""
    a, b = _pair()
    try:
        a.send({"n": 1})
        a.send({"n": 2})
        m1 = b.recv_once(timeout=2)
        m2 = b.recv_once(timeout=2)
        assert m1 == {"n": 1}
        assert m2 == {"n": 2}
    finally:
        a.close(); b.close()


def test_recv_timeout_returns_none():
    a, b = _pair()
    try:
        assert b.recv_once(timeout=0.1) is None
    finally:
        a.close(); b.close()


def test_peer_close_raises_protocolerror():
    a, b = _pair()
    try:
        a.close()
        with pytest.raises(ProtocolError):
            # заставим recv увидеть закрытие.
            b.recv_once(timeout=1)
    finally:
        b.close()


def test_large_message():
    a, b = _pair()
    try:
        payload = {"data": "x" * 200_000}
        a.send(payload)
        got = b.recv_once(timeout=5)
        assert got == payload
    finally:
        a.close(); b.close()


def test_unicode_safe():
    a, b = _pair()
    try:
        a.send({"msg": "Крейсер A_1 поразил Радиовышку 🚀"})
        assert b.recv_once(timeout=2) == {
            "msg": "Крейсер A_1 поразил Радиовышку 🚀"
        }
    finally:
        a.close(); b.close()
