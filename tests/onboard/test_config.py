import json

import pytest

from exo import config as xc


def test_roundtrip(tmp_path, monkeypatch):
    for var in xc.ENV.values():
        monkeypatch.delenv(var, raising=False)
    home = tmp_path / "exo-home"
    cfg = xc.Config.load(home)
    assert cfg.principal is None
    assert cfg.hermes_home == home / "hermes-home"
    assert cfg.hermes_dir == home / "hermes"
    assert cfg.plugins == xc.DEFAULT_PLUGINS
    cfg.set("principal", "sam")
    cfg.set("plugins", ["exo-memory", "exo-context"])
    cfg.set_plugin_config("exo-memory", {"pack_budget": 900})
    cfg.set_plugin_config("exo-memory", {"agent_id": "seat-1"})
    cfg.set("future.option", 3)
    path = cfg.save()
    assert oct(path.stat().st_mode & 0o777) == "0o600"

    again = xc.Config.load(home)
    assert again.principal == "sam"
    assert again.plugins == ["exo-memory", "exo-context"]
    assert again.plugin_config("exo-memory") == {"pack_budget": 900, "agent_id": "seat-1"}
    assert again.get("future.option") == 3
    assert again.store_root() == home / "principals" / "sam"
    assert json.loads(path.read_text())["version"] == xc.VERSION


def test_env_overrides_but_is_not_saved(tmp_path, monkeypatch):
    home = tmp_path / "h"
    cfg = xc.Config.load(home)
    cfg.set("principal", "sam")
    cfg.save()
    monkeypatch.setenv("EXO_PRINCIPAL", "other")
    monkeypatch.setenv("EXO_HERMES_HOME", str(tmp_path / "hh"))
    cfg = xc.Config.load(home)
    assert cfg.principal == "other"
    assert cfg.hermes_home == tmp_path / "hh"
    cfg.save()
    data = json.loads((home / "config.json").read_text())
    assert data["principal"] == "sam" and "hermes_home" not in data


def test_ignores_live_hermes_home_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "live"))
    assert xc.Config.load(tmp_path / "h").hermes_home == tmp_path / "h" / "hermes-home"


def test_validation_and_unset(tmp_path):
    cfg = xc.Config.load(tmp_path)
    with pytest.raises(xc.ConfigError):
        cfg.set("principal", "Bad Id")
    with pytest.raises(xc.ConfigError):
        cfg.set("plugins", "exo-memory")
    cfg.set("principals.sam", str(tmp_path / "elsewhere"))
    assert cfg.store_root("sam") == tmp_path / "elsewhere"
    assert cfg.unset("principals.sam") and not cfg.unset("principals.sam")
    (tmp_path / "config.json").write_text("[1]")
    with pytest.raises(xc.ConfigError):
        xc.Config.load(tmp_path)


def test_cli(capsys):
    assert xc.main(["set", "principal", "sam"]) == 0
    assert xc.main(["set", "plugin_config.exo-gate", '{"strict": true}']) == 0
    assert xc.main(["get", "plugin_config.exo-gate.strict"]) == 0
    assert capsys.readouterr().out.strip() == "true"
    assert xc.main(["get", "nope"]) == 1
    assert xc.Config.load().principal == "sam"
    assert xc.Config.load().path.parent == xc.exo_home()


# --- #255 W1-3: load() caches on (size, mtime_ns) and hands out deep copies --------

def _write(home, data):
    import os
    home.mkdir(parents=True, exist_ok=True)
    p = home / "config.json"
    p.write_text(json.dumps(data))
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))  # a distinct mtime, whatever the clock did
    return p


def test_load_reads_the_file_once_until_it_changes(tmp_path, monkeypatch):
    home = tmp_path / "h"
    _write(home, {"principal": "sam"})
    real = type(home).read_text
    reads = []

    def counting(self, *a, **kw):
        if self.name == "config.json":
            reads.append(str(self))
        return real(self, *a, **kw)

    monkeypatch.setattr(type(home), "read_text", counting)
    for _ in range(10):
        assert xc.Config.load(home).principal == "sam"
    assert len(reads) == 1


def test_load_sees_an_external_edit(tmp_path):
    home = tmp_path / "h"
    _write(home, {"principal": "sam"})
    assert xc.Config.load(home).principal == "sam"
    _write(home, {"principal": "jo"})
    assert xc.Config.load(home).principal == "jo"
    (home / "config.json").unlink()
    assert xc.Config.load(home).principal is None
    _write(home, {"principal": "sam"})
    assert xc.Config.load(home).principal == "sam"


def test_a_failed_save_is_invisible_to_the_next_load(tmp_path, monkeypatch):
    home = tmp_path / "h"
    _write(home, {"principal": "sam"})
    cfg = xc.Config.load(home)
    cfg.set("principal", "jo")
    monkeypatch.setattr(xc, "atomic_write", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        cfg.save()
    assert xc.Config.load(home).principal == "sam"


def test_mutating_a_loaded_value_does_not_leak_into_the_next_load(tmp_path):
    home = tmp_path / "h"
    _write(home, {"models": {"roles": {"main": ["fake:x"]}}})
    xc.Config.load(home).get("models")["roles"]["main"].append("evil:y")
    assert xc.Config.load(home).get("models") == {"roles": {"main": ["fake:x"]}}
