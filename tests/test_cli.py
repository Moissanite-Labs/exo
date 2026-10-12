import json

from exo.cli import main
from exo.store import PrincipalStore


def test_init_creates_valid_store(tmp_path, capsys):
    assert main(["init", "newbie"]) == 0
    root = tmp_path / "exo-home" / "principals" / "newbie"
    store = PrincipalStore(root)
    assert store.exists() and store.lint().ok
    assert (root / "ledger.sqlite").exists()
    assert "0 errors" in capsys.readouterr().out


def test_init_explicit_root_and_template(tmp_path):
    assert main(["init", "a", "--root", str(tmp_path / "a")]) == 0
    assert main(["init", "b", "--root", str(tmp_path / "b"), "--from-template", str(tmp_path / "a")]) == 0
    assert PrincipalStore(tmp_path / "b").lint().ok


def test_query_commands(tmp_path, monkeypatch, capsys):
    main(["init", "p"])
    monkeypatch.setenv("EXO_PRINCIPAL", "p")
    capsys.readouterr()
    assert main(["find", "projects", "-n", "1"]) == 0
    assert capsys.readouterr().out.startswith("work/projects")
    assert main(["get", "work/projects", "--body-only"]) == 0
    assert "example entity node" in capsys.readouterr().out
    assert main(["ctx", "projects", "--budget", "200"]) == 0
    assert len(capsys.readouterr().out) <= 200 * 4 + 1
    assert main(["links", "work/projects"]) == 0
    assert "-see-also-> systems/environment" in capsys.readouterr().out
    assert main(["lint"]) == 0
    assert main(["graph"]) == 0
    assert main(["index"]) == 0


def test_keygen_and_log(tmp_path, monkeypatch, capsys):
    main(["init", "p"])
    monkeypatch.setenv("EXO_PRINCIPAL", "p")
    assert main(["keygen", "agent-one"]) == 0
    assert main(["log", "append", "utterance", "--author", "agent-one", "--text", "hi"]) == 0
    first = capsys.readouterr().out.strip().splitlines()[-1].split()[1]
    assert main(["log", "append", "verdict", "--author", "agent-one",
                 "--body", json.dumps({"verdict": "accept", "target": first})]) == 0
    capsys.readouterr()
    assert main(["log", "verify"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "checked": 2, "head": _head(tmp_path), "anchored": True}
    assert main(["log", "tail", "-n", "1"]) == 0
    assert "verdict" in capsys.readouterr().out


def _head(tmp_path):
    from exo.ledger import Ledger
    return Ledger(tmp_path / "exo-home" / "principals" / "p" / "ledger.sqlite").head().chain


def test_missing_principal_exits(capsys):
    import pytest
    with pytest.raises(SystemExit):
        main(["lint"])
