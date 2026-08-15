"""Pytest-wide safety rails for the deterministic regression suite."""

from __future__ import annotations

import os
import socket

import pytest


@pytest.fixture(autouse=True)
def block_external_network_in_offline_suite(monkeypatch):
    """Fail closed if an offline regression test attempts a real TCP connect.

    Network clients are unit-tested through injected fakes or mocks. This guard
    is opt-in so explicit online evaluation jobs can still exist, while the
    default CI job cannot consume credentials or become flaky on provider I/O.
    """
    if os.getenv("ALPHASTOCK_OFFLINE_TESTS") != "1":
        yield
        return

    def deny_external_network(sock, address, *_args, **_kwargs):
        # Starlette TestClient and asyncio use a loopback socket internally;
        # it stays inside the test process and is not an external dependency.
        host = address[0] if isinstance(address, tuple) and address else address
        if isinstance(host, str) and (
            host == "localhost" or host == "::1" or host.startswith("127.")
        ):
            return original_connect(sock, address, *_args, **_kwargs)
        raise AssertionError(
            "offline regression tests must not open network connections; "
            "mock the client or run an explicitly labelled online evaluation"
        )

    original_create_connection = socket.create_connection
    original_connect = socket.socket.connect

    def guarded_create_connection(address, *_args, **_kwargs):
        host = address[0] if isinstance(address, tuple) and address else address
        if isinstance(host, str) and (
            host == "localhost" or host == "::1" or host.startswith("127.")
        ):
            return original_create_connection(address, *_args, **_kwargs)
        raise AssertionError(
            "offline regression tests must not open network connections; "
            "mock the client or run an explicitly labelled online evaluation"
        )

    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    monkeypatch.setattr(socket.socket, "connect", deny_external_network)
    yield
