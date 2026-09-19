import importlib.util
import socket
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("check_mysql_port", Path(__file__).resolve().parents[1] / "scripts/check_mysql_port.py")


def checker():
    assert spec.origin and Path(spec.origin).is_file(), "MySQL pre-start checker is missing"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_occupied_port_preserves_listener_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv("EQA_DB_PORT", raising=False)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        env = tmp_path / "local.env"
        contents = f"EQA_DB_PORT={port}\nPASSWORD=do-not-print-or-change\n"
        env.write_text(contents)
        with pytest.raises(RuntimeError, match="occupied|unavailable"):
            checker().check([env])
        assert env.read_text() == contents
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass


def test_free_port_and_shell_override(tmp_path, monkeypatch):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = tmp_path / "local.env"
    env.write_text("EQA_DB_PORT=1\n")
    monkeypatch.setenv("EQA_DB_PORT", str(port))
    assert checker().check([env]) == port


def test_mismatched_client_ports_fail(tmp_path, monkeypatch):
    monkeypatch.delenv("EQA_DB_PORT", raising=False)
    a, b = tmp_path / "a.env", tmp_path / "b.env"
    a.write_text("EQA_DB_PORT=3307\n")
    b.write_text("EQA_DB_PORT=3317\n")
    with pytest.raises(RuntimeError, match="agree"):
        checker().check([a, b])


@pytest.mark.parametrize("value", ["0", "65536", "oops", "${OTHER_PORT}"])
def test_invalid_port_fails_without_echoing_input(tmp_path, monkeypatch, value):
    monkeypatch.delenv("EQA_DB_PORT", raising=False)
    env = tmp_path / "local.env"
    env.write_text(f"EQA_DB_PORT={value}\n")
    with pytest.raises(RuntimeError, match="integer"):
        checker().check([env])


def test_missing_env_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("EQA_DB_PORT", raising=False)
    with pytest.raises(RuntimeError, match="Missing"):
        checker().check([tmp_path / "missing.env"])


def test_rejects_bootstrap_forbidden_port(tmp_path, monkeypatch):
    monkeypatch.delenv("EQA_DB_PORT", raising=False)
    env = tmp_path / "local.env"
    env.write_text("EQA_DB_PORT=3306\n")
    with pytest.raises(RuntimeError, match="3306"):
        checker().check([env])
