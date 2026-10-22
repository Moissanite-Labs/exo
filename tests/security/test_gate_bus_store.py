"""Issue #210: the bus registry and relay database are Exo store paths, so a write to them asks the principal.

The registry is an input to signed seat answers (exo.core.approvals.seat_pubkeys) and to the relay, so writing it is
a store_write: the floor, no standing grant, the principal alone. Every command string here is data for
exo.gate.classify/decide; none is run."""
import pytest

from exo import gate

BUS_ENV = ("EXO_BUS_REGISTRY", "DIVAN_REGISTRY", "EXO_BUS_DB", "DIVAN_DB", "EXO_BUS_KEYS_DIR", "DIVAN_KEYS_DIR")


@pytest.fixture(params=["default", "custom"])
def exo_home(request, tmp_path, monkeypatch):
    """The bus folder's home as a command spells it: the default ~/.exo, or a custom EXO_HOME."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE", "EXO_SEAT", *BUS_ENV):
        monkeypatch.delenv(name, raising=False)
    if request.param == "custom":
        monkeypatch.setenv("EXO_HOME", str(tmp_path / "srv" / "exo-home"))
        return "$EXO_HOME"
    monkeypatch.delenv("EXO_HOME", raising=False)
    return "~/.exo"


def _sh(cmd):
    return {"tool": "bash", "args": {"command": cmd}}


def _assert_store_write(action):
    c = gate.classify(action)
    d = gate.decide(c)
    assert (c.category, d.outcome, d.rule) == ("store_write", "ask", "floor"), action
    # principal-only, whatever approvals.yaml delegates
    assert gate.approvers_for(c.category, {"default": ["seat:lead"], "categories": {"store_write": ["seat:lead"]}}) \
        == ["principal"]


WRITES = [
    # the issue's rows: local_write or shell, and allowed, on main 4ac092b
    lambda h: _sh(f"cat forged.json > {h}/bus/registry.json"),
    lambda h: _sh(f"cp forged.json {h}/bus/registry.json"),
    lambda h: _sh(f"mv /tmp/x.json {h}/bus/registry.json"),
    lambda h: {"tool": "write_file", "args": {"path": f"{h}/bus/registry.json", "content": "{}"}},
    lambda h: {"tool": "patch", "args": {"path": f"{h}/bus/registry.json", "old_string": "a", "new_string": "b"}},
    lambda h: _sh(f"printf x > {h}/bus/relay.sqlite"),
    # the relay database's sidecars, other writers and the folder itself
    lambda h: _sh(f"printf x > {h}/bus/relay.sqlite-wal"),
    lambda h: _sh(f"cp x {h}/bus/relay.sqlite-shm"),
    lambda h: _sh(f"cp x {h}/bus/relay.sqlite-journal"),
    lambda h: _sh(f"tee {h}/bus/registry.json < forged.json"),
    lambda h: _sh(f"sed -i s/a/b/ {h}/bus/registry.json"),
    lambda h: _sh(f"ln -sf /tmp/forged.json {h}/bus/registry.json"),
    lambda h: _sh(f"rm -rf {h}/bus"),
    lambda h: _sh(f"mv {h}/bus {h}/bus.old"),
    lambda h: _sh(f"cd {h} && printf x > bus/registry.json"),
    lambda h: _sh(f"echo x > {h}/b{{u,x}}s/registry.json"),
]


@pytest.mark.parametrize("make", WRITES, ids=range(len(WRITES)))
def test_a_write_to_the_bus_folder_is_a_store_write(make, exo_home):
    _assert_store_write(make(exo_home))


@pytest.mark.parametrize("cmd", [
    "printf x > bus/registry.json",                 # a distinctive name counts anywhere, as state/gate-grants.json
    "cp x ./bus/relay.sqlite-wal",
    "printf x > ~/.exo/bus/registry.json",          # `.exo/bus` counts by its name, whatever EXO_HOME is
])
def test_the_bus_names_match_anywhere(cmd, exo_home):
    _assert_store_write(_sh(cmd))


@pytest.mark.parametrize("name, value, written", [
    ("EXO_BUS_REGISTRY", "reg/registry.json", "reg/registry.json"),
    ("DIVAN_REGISTRY", "reg/seats.json", "reg/seats.json"),
    ("EXO_BUS_DB", "db/bus.sqlite", "db/bus.sqlite"),
    ("EXO_BUS_DB", "db/bus.sqlite", "db/bus.sqlite-wal"),
    ("DIVAN_DB", "db/relay.db", "db/relay.db-journal"),
    ("EXO_BUS_KEYS_DIR", "seatkeys", "seatkeys/lead.pub"),
    ("DIVAN_KEYS_DIR", "seatkeys", "seatkeys/lead.pub"),
])
def test_bus_files_the_environment_moves_are_store_paths(name, value, written, exo_home, tmp_path, monkeypatch):
    monkeypatch.setenv(name, str(tmp_path / "srv" / value))
    target = tmp_path / "srv" / written
    _assert_store_write(_sh(f"cp forged {target}"))
    _assert_store_write({"tool": "write_file", "args": {"path": str(target), "content": "x"}})
    _assert_store_write(_sh(f"rm -rf {target.parent}"))          # the folder that holds it
    # a neighbour (beside a file; beside the keys folder, which is protected whole) and a read keep their class
    beside = (tmp_path / "srv") if name.endswith("KEYS_DIR") else target.parent
    assert gate.classify(_sh(f"cp x {beside}/notes.txt")).category == "shell"
    assert gate.classify(_sh(f"cat {target}")).category == "read"


@pytest.mark.parametrize("cmd, category", [
    ("cat ~/.exo/bus/registry.json", "read"),       # reading the registry stays a read
    ("cd ~/.exo && cat bus/registry.json", "read"),
    ("printf x > ~/project/bus.json", "local_write"),
    ("cp ok.json /tmp/registry.json", "shell"),
    ("printf x > ~/project/relay.sqlite", "local_write"),
])
def test_other_bus_like_paths_keep_their_class(cmd, category, exo_home):
    assert gate.classify(_sh(cmd)).category == category
