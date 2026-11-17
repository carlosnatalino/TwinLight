"""Fixtures shared by the client tests."""

from __future__ import annotations

import socket

import pytest


@pytest.fixture
def dead_target() -> str:
    """``host:port`` on loopback where nothing listens, so a connection is
    refused at once. (A bound socket that never listens is no good: macOS
    drops the SYN, and the connection times out only after seconds.)"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"127.0.0.1:{port}"
