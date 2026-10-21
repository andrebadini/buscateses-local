"""Tests for the shared loopback socket helpers."""

from __future__ import annotations

import socket

import pytest

from buscateses import netutil


def test_bind_socket_does_not_allow_rebinding_the_same_port() -> None:
    first = netutil.bind_socket(netutil.LOOPBACK, 0)
    try:
        port = int(first.getsockname()[1])
        assert netutil.try_bind(netutil.LOOPBACK, port) is None
        assert netutil.port_in_use(netutil.LOOPBACK, port) is True
    finally:
        first.close()
    second = netutil.try_bind(netutil.LOOPBACK, port)
    assert second is not None
    second.close()


def test_find_free_socket_keeps_the_port_reserved() -> None:
    sock = netutil.find_free_socket(49152, attempts=10)
    try:
        port = int(sock.getsockname()[1])
        assert netutil.port_in_use(netutil.LOOPBACK, port) is True
    finally:
        sock.close()
    assert netutil.port_in_use(netutil.LOOPBACK, port) is False


def test_find_free_port_returns_a_bindable_port() -> None:
    port = netutil.find_free_port(49152, attempts=10)
    assert 1 <= port <= 65535
    sock = netutil.bind_socket(netutil.LOOPBACK, port)
    sock.close()


def test_windows_uses_exclusive_address_use(monkeypatch: pytest.MonkeyPatch) -> None:
    options: list[tuple[int, int, int]] = []

    class FakeSocket:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def setsockopt(self, level: int, option: int, value: int) -> None:
            options.append((level, option, value))

        def bind(self, _address) -> None:
            pass

        def close(self) -> None:
            pass

        def getsockname(self):
            return ("127.0.0.1", 50000)

    monkeypatch.setattr(netutil.sys, "platform", "win32")
    monkeypatch.setattr(netutil.socket, "SO_EXCLUSIVEADDRUSE", 1, raising=False)
    monkeypatch.setattr(netutil.socket, "socket", FakeSocket)

    netutil._new_socket()

    assert (socket.SOL_SOCKET, 1, 1) in options or (
        socket.SOL_SOCKET,
        netutil.socket.SO_EXCLUSIVEADDRUSE,
        1,
    ) in options
    assert all(option != socket.SO_REUSEADDR for _level, option, _value in options)
