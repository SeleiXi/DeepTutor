"""``deeptutor start`` listens on all interfaces unless DEEPTUTOR_BIND_HOST pins it."""

from __future__ import annotations

import pytest

from deeptutor.runtime.launcher import BIND_HOST_ENV, DEFAULT_BIND_HOST, resolve_bind_host


def test_default_bind_host_keeps_lan_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(BIND_HOST_ENV, raising=False)
    assert resolve_bind_host() == DEFAULT_BIND_HOST == "0.0.0.0"


@pytest.mark.parametrize(
    ("raw", "expected"), [("127.0.0.1", "127.0.0.1"), ("  ::1 ", "::1"), ("   ", "0.0.0.0")]
)
def test_bind_host_override(monkeypatch: pytest.MonkeyPatch, raw: str, expected: str) -> None:
    monkeypatch.setenv(BIND_HOST_ENV, raw)
    assert resolve_bind_host() == expected
