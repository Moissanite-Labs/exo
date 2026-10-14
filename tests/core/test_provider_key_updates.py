"""Key saves report precedence and preserve concurrent writes without real stores."""
import json
import os
import stat
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from exo import providers_cmd, providers_setup as ps
from exo.core.models import keys
from exo.core.models.router import Router

NEW = "new-test-key-value-012345"
OLD = "old-test-key-value-987654"


@pytest.fixture(autouse=True)
def fake_stores(monkeypatch):
    monkeypatch.setattr(keys, "keychain_get", lambda name: None)
    monkeypatch.setattr(keys, "secret_service_get", lambda name: None)
    monkeypatch.setattr(ps, "_run", lambda *args, **kwargs: pytest.fail("unexpected key-store tool"))


@pytest.mark.parametrize("higher,command", [
    ("keychain", "security delete-generic-password -s exo -a OPENAI_API_KEY"),
    ("secret-service", "secret-tool clear service exo account OPENAI_API_KEY"),
])
def test_file_save_reports_higher_store_without_replacing_it(monkeypatch, higher, command, caplog):
    getter = "keychain_get" if higher == "keychain" else "secret_service_get"
    monkeypatch.setattr(keys, getter, lambda name: OLD if name == "OPENAI_API_KEY" else None)
    out = ps.set_key("openai", NEW, router=Router(env={}, key_sources=True), store="file", env={})
    assert keys.lookup_key("OPENAI_API_KEY", {}) == OLD
    assert keys.file_get("OPENAI_API_KEY") == NEW
    assert out["stored_in"] == "file" and out["effective_source"] == out["shadowed_by"] == higher
    assert not out["shadowed_by_env"]
    assert f"--store {higher}" in out["warning"] and command in out["warning"]
    assert "remove only that item" in out["warning"]
    assert NEW not in json.dumps(out) + caplog.text and OLD not in json.dumps(out) + caplog.text


def test_environment_is_reported_as_effective_without_changing_it(monkeypatch):
    monkeypatch.setattr(keys, "keychain_get", lambda name: OLD)
    env = {"OPENAI_API_KEY": "environment-test-value-12345"}
    before = dict(env)
    out = ps.set_key("openai", NEW, router=Router(env=env), store="file", env=env)
    assert env == before and out["effective_source"] == out["shadowed_by"] == "env"
    assert out["shadowed_by_env"] and "restart Exo" in out["warning"] and "unset" in out["warning"]
    assert NEW not in json.dumps(out) and env["OPENAI_API_KEY"] not in json.dumps(out)


def test_unreadable_saved_store_reports_lower_fallback_without_wrong_removal_steps(monkeypatch):
    ps.write_keys_file("OPENAI_API_KEY", OLD)
    monkeypatch.setattr(ps, "_keychain_set", lambda *args: None)
    out = ps.set_key("openai", NEW, router=Router(env={}, key_sources=True), store="keychain", env={})
    assert out["stored_in"] == "keychain" and out["effective_source"] == "file" and out["shadowed_by"] is None
    assert "not confirmed readable" in out["warning"] and "unlock keychain" in out["warning"]
    assert "secret-tool" not in out["warning"] and keys.file_get("OPENAI_API_KEY") == OLD


def test_effective_source_uses_the_router_environment_and_source_policy(monkeypatch):
    def forbidden(*args):
        pytest.fail("environment-only router inspected a credential store")

    monkeypatch.setattr(keys, "keychain_get", forbidden)
    monkeypatch.setattr(keys, "secret_service_get", forbidden)
    out = ps.set_key("openai", NEW, router=Router(env={}), store="file")
    assert out["effective_source"] is None and "environment credentials only" in out["warning"]
    r = Router(env={"OPENAI_API_KEY": OLD})
    out = ps.set_key("openai", NEW, router=r, store="file")
    assert out["effective_source"] == out["shadowed_by"] == "env" and out["shadowed_by_env"]


def test_cli_shows_effective_source_and_targeted_steps(monkeypatch, capsys):
    monkeypatch.setattr(keys, "keychain_get", lambda name: OLD if name == "OPENAI_API_KEY" else None)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(providers_cmd.getpass, "getpass", lambda prompt: NEW)
    assert providers_cmd.main(["add", "openai", "--store", "file", "--no-test"]) == 0
    text = capsys.readouterr().out
    assert "stored OPENAI_API_KEY in file" in text and "effective source: keychain" in text
    assert "shadowed" in text and "remove only that item" in text
    assert NEW not in text and OLD not in text


def slow_reads(monkeypatch):
    original = ps._keyfile_lines

    def read(*args):
        lines = original(*args)
        time.sleep(0.03)  # force overlapping transactions without the lock
        return lines

    monkeypatch.setattr(ps, "_keyfile_lines", read)


def test_concurrent_thread_saves_keep_every_key(tmp_path, monkeypatch):
    slow_reads(monkeypatch)
    path = tmp_path / "keys" / "keys.env"
    barrier = threading.Barrier(8)

    def save(n):
        barrier.wait(timeout=5)
        ps.write_keys_file(f"KEY_{n}", f"thread-test-value-{n}", path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(save, range(8)))
    assert {f"KEY_{n}=thread-test-value-{n}" for n in range(8)} == set(path.read_text().splitlines())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.with_name("keys.env.lock").stat().st_mode) == 0o600


def test_remove_and_save_share_the_transaction_lock(tmp_path, monkeypatch):
    path = tmp_path / "keys.env"
    ps.write_keys_file("REMOVE", "remove-test-value", path)
    slow_reads(monkeypatch)
    barrier = threading.Barrier(2)

    def mutate(n):
        barrier.wait(timeout=5)
        if n:
            ps.write_keys_file("KEEP", "keep-test-value", path)
        else:
            assert ps.remove_from_keys_file("REMOVE", path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(mutate, range(2)))
    assert path.read_text() == "KEEP=keep-test-value\n"


def test_concurrent_process_saves_keep_every_key(tmp_path):
    path, start = tmp_path / "keys.env", tmp_path / "start"
    worker = '''
import sys, time
from pathlib import Path
from exo import providers_setup as ps
path, start, n = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
read = ps._keyfile_lines
def slow_read(*args):
    lines = read(*args)
    time.sleep(0.05)
    return lines
ps._keyfile_lines = slow_read
while not start.exists():
    time.sleep(0.005)
ps.write_keys_file("PROCESS_" + n, "process-test-value-" + n, path)
'''
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    procs = [subprocess.Popen([sys.executable, "-c", worker, str(path), str(start), str(n)],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for n in range(4)]
    try:
        start.touch()
        for proc in procs:
            stdout, stderr = proc.communicate(timeout=15)
            assert proc.returncode == 0, stderr
            assert not stdout
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    assert {f"PROCESS_{n}=process-test-value-{n}" for n in range(4)} == set(path.read_text().splitlines())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("which", ["keys", "lock"])
def test_symlink_keys_or_lock_cannot_modify_target(tmp_path, which):
    target = tmp_path / "target"
    target.write_text("TARGET=sentinel-test-value\n")
    target.chmod(0o644)
    path = tmp_path / "keys.env"
    link = path if which == "keys" else path.with_name("keys.env.lock")
    link.symlink_to(target)
    with pytest.raises(OSError):
        ps.write_keys_file("KEY", NEW, path)
    assert target.read_text() == "TARGET=sentinel-test-value\n" and stat.S_IMODE(target.stat().st_mode) == 0o644
    assert link.is_symlink()
