import socket
import subprocess
import sys
from pathlib import Path


def test_configure_separates_bootstrap_from_runtime(tmp_path):
    from scripts.configure_local import configure
    configure(tmp_path)
    bootstrap=(tmp_path/'bootstrap.env').read_text()
    runtime=(tmp_path/'runtime.env').read_text()
    assert 'EQA_BOOTSTRAP_PASSWORD=' in bootstrap
    assert 'EQA_BOOTSTRAP_PASSWORD' not in runtime
    assert 'EQA_DB_PASSWORD=' in runtime
    configure(tmp_path)
    assert (tmp_path/'runtime.env').read_text()==runtime


def test_web_port_collision_keeps_owner_running():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0)); listener.listen()
        port=listener.getsockname()[1]
        r=subprocess.run([sys.executable,'scripts/serve.py','--port',str(port)],capture_output=True,text=True,timeout=10)
        assert r.returncode != 0
        assert 'EQA_WEB_PORT' in r.stderr
        with socket.create_connection(('127.0.0.1',port),timeout=1): pass


def test_configure_alternate_port_preserves_existing_pair(tmp_path):
    import pytest
    from scripts.configure_local import configure
    configure(tmp_path, port=3317)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert 'EQA_DB_PORT=3317' in (tmp_path/'runtime.env').read_text().splitlines()
    assert 'EQA_DB_PORT=3317' in (tmp_path/'bootstrap.env').read_text().splitlines()
    configure(tmp_path)
    configure(tmp_path, port=3317)
    with pytest.raises(RuntimeError, match='existing'):
        configure(tmp_path, port=3318)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_configure_rejects_shared_mysql_port_before_writing(tmp_path):
    import pytest
    from scripts.configure_local import configure
    with pytest.raises(ValueError, match='3306'):
        configure(tmp_path, port=3306)
    assert list(tmp_path.iterdir()) == []
