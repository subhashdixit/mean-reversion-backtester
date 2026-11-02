"""A starting container may reset a connection before Gunicorn is ready."""

import importlib.util
import io
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "http_smoke", Path(__file__).resolve().parents[1] / "scripts/smoke_test.py"
)
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


def test_connection_reset_retries_then_checks_real_response(monkeypatch):
    """A transient reset must not bypass readiness and backtest checks."""
    calls = []
    responses = iter(
        [
            b'{"status": "ready"}',
            b"date,close\n2020-01-01,100\n",
            b'{"report": {"evaluation_periods": 1}, "curve": [100]}',
        ]
    )

    def request(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionResetError("container starting")
        return io.BytesIO(next(responses))

    monkeypatch.setattr(SMOKE, "urlopen", request)
    monkeypatch.setattr(SMOKE.time, "sleep", lambda _seconds: None)
    SMOKE.check("http://localhost:8000", attempts=2)
    assert len(calls) == 4


def test_connection_reset_retry_is_bounded(monkeypatch):
    """Persistent transport failure still fails the smoke check."""
    calls = []

    def request(*_args, **_kwargs):
        calls.append(1)
        raise ConnectionResetError("still starting")

    monkeypatch.setattr(SMOKE, "urlopen", request)
    monkeypatch.setattr(SMOKE.time, "sleep", lambda _seconds: None)
    with pytest.raises(SystemExit, match="still starting"):
        SMOKE.check("http://localhost:8000", attempts=2)
    assert len(calls) == 2
