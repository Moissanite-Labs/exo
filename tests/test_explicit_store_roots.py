"""A store made with `--root` is found by its id afterwards (config `principals.<id>`, `exo.store.principal_root`).

`exo init <id> --root DIR` and `exo import-store --root DIR` record DIR in `$EXO_HOME/config.json`. Every reader that
turns an id into a store honours it: the store commands, `exo memory verify`, learning, handoffs. Before, they all
looked under `$EXO_HOME/principals/<id>`, so the store made with `--root` was never read, written or verified.
"""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from exo.cli import main
from exo.store import PrincipalStore, principal_root


@pytest.fixture
def home(tmp_path):
    return tmp_path / "exo-home"  # EXO_HOME, set by the root conftest


def _config(home):
    return json.loads((home / "config.json").read_text())


def test_principal_root_prefers_the_recorded_root(home, tmp_path):
    from exo.store import explicit_roots
    assert principal_root("pat") == home / "principals" / "pat"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"principals": {"pat": str(tmp_path / "x"), "Bad": "/y", "q": 3,
                                                                 "r": " "}}))
    assert principal_root("pat") == tmp_path / "x"
    assert principal_root("other") == home / "principals" / "other"
    assert explicit_roots() == {"pat": tmp_path / "x"}  # not an id, not a path: ignored
    assert principal_root("pat", tmp_path / "another-home") == tmp_path / "another-home" / "principals" / "pat"
    assert PrincipalStore.for_principal("pat").root == (tmp_path / "x").resolve()
    with pytest.raises(ValueError):
        principal_root("../pat")
    for bad in ("{not json", json.dumps([1]), json.dumps({"principals": ["pat"]})):
        (home / "config.json").write_text(bad)
        assert principal_root("pat") == home / "principals" / "pat"


@pytest.mark.parametrize("bad", ["~nosuchuser-exo-test/x", "/srv/a\0b", "rel/x"], ids=["no-such-user", "nul", "relative"])
def test_a_bad_recorded_root_names_no_store(home, tmp_path, bad):
    """A recorded root that names an unknown user, holds a NUL byte or is relative is skipped. Before, `~nosuchuser`
    raised RuntimeError in the gate and in every store reader, also for other ids, and a relative root was read
    against whatever the working directory was."""
    from exo import gate
    from exo.store import explicit_roots
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"principals": {"ada": bad, "pat": str(tmp_path / "x")}}))
    assert explicit_roots() == {"pat": tmp_path / "x"}
    assert principal_root("ada") == home / "principals" / "ada"
    assert PrincipalStore.for_principal("bob").root == (home / "principals" / "bob").resolve()
    assert gate.classify(("write_file", {"path": "/srv/a.txt"})).category == "local_write"
    assert gate.classify(("write_file", {"path": str(tmp_path / "x" / "policies" / "g.yaml")})).category == "store_write"


def test_store_commands_use_a_store_made_with_root(home, tmp_path, capsys):
    root = tmp_path / "elsewhere" / "ada"
    assert main(["init", "ada", "--root", str(root)]) == 0
    assert _config(home)["principals"] == {"ada": str(root.resolve())} and _config(home)["principal"] == "ada"
    assert main(["keygen", "ada"]) == 0
    assert main(["log", "append", "note", "--author", "ada", "--text", "hello"]) == 0
    capsys.readouterr()
    assert main(["log", "verify"]) == 0
    assert json.loads(capsys.readouterr().out)["checked"] == 1
    assert main(["find", "projects", "-n", "1"]) == 0
    assert capsys.readouterr().out.startswith("work/projects")
    assert main(["lint"]) == 0
    assert main(["log", "tail", "--principal", "ada"]) == 0
    assert "hello" in capsys.readouterr().out
    assert not (home / "principals" / "ada").exists()  # nothing was made under the home
    with pytest.raises(SystemExit, match="not empty"):  # `exo init ada` again targets the same store
        main(["init", "ada"])


def test_root_at_the_default_path_takes_the_id_back(home, tmp_path):
    """`--root` names the store the id means from now on, also when it is the default path."""
    assert main(["init", "ada", "--root", str(tmp_path / "elsewhere" / "ada")]) == 0
    assert main(["init", "ada", "--root", str(home / "principals" / "ada")]) == 0
    assert "ada" not in _config(home).get("principals", {})
    assert principal_root("ada") == home / "principals" / "ada"


def test_import_store_root_is_found_by_id(home, tmp_path, capsys):
    src = PrincipalStore.create(tmp_path / "old-store")
    dest = tmp_path / "fresh" / "bob"
    assert main(["import-store", str(src.root), "--id", "bob", "--root", str(dest)]) == 0
    assert _config(home)["principals"]["bob"] == str(dest.resolve())
    capsys.readouterr()
    assert main(["lint"]) == 0  # the active principal is bob; its store is the --root one
    assert "0 errors" in capsys.readouterr().out
    assert main(["log", "verify"]) == 0
    assert json.loads(capsys.readouterr().out)["checked"] == 1  # the import's own `ingest` event


def _tamper(path):
    db = sqlite3.connect(path)
    db.execute("DROP TRIGGER events_no_update")
    db.execute("UPDATE events SET body = '{\"text\":\"forged\"}' WHERE seq = 1")
    db.commit()
    db.close()


def test_memory_verify_checks_a_store_made_with_root(home, tmp_path, capsys):
    root = tmp_path / "elsewhere" / "ada"
    assert main(["init", "ada", "--root", str(root)]) == 0
    assert main(["keygen", "ada"]) == 0
    assert main(["log", "append", "note", "--author", "ada", "--text", "hello"]) == 0
    capsys.readouterr()
    assert main(["memory", "verify", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ledgers"]["ada"]["ok"] is True
    _tamper(root / "ledger.sqlite")
    assert main(["memory", "verify"]) == 1  # before: "nothing to verify", exit 0
    assert "ledger ada: BROKEN" in capsys.readouterr().out
    assert main(["memory", "verify", "--principal", "ada"]) == 1


def test_memory_verify_keeps_two_stores_of_one_name_apart(home, tmp_path, capsys):
    """A store left at the default path (the old hint said `exo init <id>`) and the recorded root share a folder
    name. Both are checked, and a broken one is never hidden behind the other's result."""
    assert main(["init", "ada"]) == 0
    root = tmp_path / "elsewhere" / "ada"
    assert main(["init", "ada", "--root", str(root)]) == 0
    assert main(["keygen", "ada"]) == 0
    for r in (home / "principals" / "ada", root):
        assert main(["log", "append", "note", "--author", "ada", "--text", "hi", "--root", str(r)]) == 0
    _tamper(home / "principals" / "ada" / "ledger.sqlite")
    capsys.readouterr()
    assert main(["memory", "verify", "--json"]) == 1
    ledgers = json.loads(capsys.readouterr().out)["ledgers"]
    assert {k: v["ok"] for k, v in ledgers.items()} == {"ada": False, str(root.resolve()): True}


def test_learning_resolves_a_store_made_with_root(home, tmp_path):
    from exo.learn.runtime import resolve_store
    root = tmp_path / "elsewhere" / "ada"
    assert main(["init", "ada", "--root", str(root)]) == 0
    engine = SimpleNamespace(home=home, plugins=None)
    assert resolve_store(engine, {"EXO_PRINCIPAL": "ada"}) == root.resolve()
    assert resolve_store(engine, {"EXO_PRINCIPAL": "bob"}) == home / "principals" / "bob"


def test_handoffs_go_to_a_store_made_with_root(home, tmp_path, monkeypatch):
    from exo.handoff import default_handoff_dir
    monkeypatch.delenv("EXO_HANDOFF_DIR", raising=False)
    root = tmp_path / "elsewhere" / "ada"
    assert main(["init", "ada", "--root", str(root)]) == 0
    assert Path(default_handoff_dir(None, "ada")) == root.resolve() / "handoffs"
