"""Issue #339: a bus file the environment moves out of the home (EXO_BUS_* / DIVAN_*) is protected by its
absolute path (#305), but a relative write after a cd or pushd into its folder, an extraction with tar -C
there, and a copy landing a protected name in the folder were not. A forged registry or seat key changes
which seat answers the bus trusts, so those forms ask as store_write too — while the rest of a shared
folder stays writable. Every command string here is data for exo.gate.classify/decide; none is run."""
import pytest

from exo import gate

BUS_ENV = ("EXO_BUS_REGISTRY", "DIVAN_REGISTRY", "EXO_BUS_DB", "DIVAN_DB", "EXO_BUS_KEYS_DIR", "DIVAN_KEYS_DIR")


@pytest.fixture(params=["EXO_BUS", "DIVAN"])
def srv(request, tmp_path, monkeypatch):
    """A temp HOME and the bus files moved under $H/srv, spelled with one env prefix (EXO_BUS_ or the
    legacy DIVAN_). Returns the srv path."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE", "EXO_SEAT", "EXO_HOME", *BUS_ENV):
        monkeypatch.delenv(name, raising=False)
    p = request.param
    monkeypatch.setenv(f"{p}_REGISTRY", str(tmp_path / "srv" / "divan" / "registry.json"))
    monkeypatch.setenv(f"{p}_DB", str(tmp_path / "srv" / "divan" / "relay.db"))
    monkeypatch.setenv(f"{p}_KEYS_DIR", str(tmp_path / "srv" / "kd"))
    return tmp_path / "srv"


def _sh(cmd):
    return {"tool": "bash", "args": {"command": cmd}}


def _assert_store_write(action):
    c = gate.classify(action)
    d = gate.decide(c)
    assert (c.category, d.outcome, d.rule) == ("store_write", "ask", "floor"), action
    # principal-only, whatever approvals.yaml delegates
    assert gate.approvers_for(c.category, {"default": ["seat:lead"],
                                           "categories": {"store_write": ["seat:lead"]}}) == ["principal"]


WRITES = [
    # the issue's rows: read, local_write or shell, and allowed, on main b419f91b
    lambda s: f"cd {s}/divan && cp forged.json registry.json",
    lambda s: f"cd {s}/divan && printf x > registry.json",
    lambda s: f"cd {s}/kd && cp evil.pub seat1.pub",
    lambda s: f"cd {s} && cp evil.pub kd/seat1.pub",
    lambda s: f"cd {s} && mv divan /tmp/x",
    lambda s: f"tar -C {s}/divan -xf evil.tar",
    lambda s: f"cp -r evil/kd {s}/",
    # the same folders reached other ways, and the relay database and its sidecars by relative name
    lambda s: f"pushd {s}/kd && cp evil.pub seat1.pub",
    lambda s: f"cd {s}/divan && cp x relay.db",
    lambda s: f"cd {s}/divan && rm relay.db-wal",
    lambda s: f"tar --directory={s}/divan -xf evil.tar",
    lambda s: f"tar --directory {s}/kd -xf evil.tar",
    lambda s: f"cd {s}/divan && tar -xf evil.tar",
    lambda s: f"cd {s} && tar -C divan -xf evil.tar",
    lambda s: f"cp -r evil/kd {s}",
    lambda s: f"mv evil/kd {s}/",
    lambda s: f"git -C {s}/divan checkout -- registry.json",
    lambda s: f"cd {s}/divan && cp sub/registry.json .",     # the copy lands on the name in that folder
]


@pytest.mark.parametrize("make", WRITES, ids=range(len(WRITES)))
def test_a_relative_write_to_a_moved_bus_file_is_a_store_write(make, srv):
    _assert_store_write(_sh(make(srv)))


NON_MOVING_CDS = [
    # a cd that does not move the shell leaves the bases alone, so the write still asks
    lambda s: f"cd {s}/divan && (cd /tmp) && cp forged.json registry.json",
    lambda s: f"cd {s}/divan && cd /tmp | cat; printf x > registry.json",
    lambda s: f"cd {s}/divan; cd /tmp & cp forged.json registry.json",
    lambda s: f"cd {s}/divan && false && cd /tmp; cp forged.json registry.json",
    lambda s: f"cd {s}/divan; if false; then cd /tmp; fi; cp forged.json registry.json",
    lambda s: f"cd {s}/divan; cd /no/such/dir; cp forged.json registry.json",
    lambda s: f"cd {s}/divan && cd /tmp && cd - && cp forged.json registry.json",
    lambda s: f"pushd {s}/divan && pushd /tmp && popd && cp forged.json registry.json",
    lambda s: f"cd {s}/divan && cd && cp forged.json registry.json",
]


@pytest.mark.parametrize("make", NON_MOVING_CDS, ids=range(len(NON_MOVING_CDS)))
def test_a_cd_that_does_not_move_the_shell_keeps_the_bus_base(make, srv):
    _assert_store_write(_sh(make(srv)))


RELATIVE_CDS = [
    lambda s: f"cd {s} && cd divan && cp forged.json registry.json",
    lambda s: f"cd {s} && cd kd && cp evil.pub seat1.pub",
    lambda s: f"cd {s}/divan && cd ../kd && cp evil.pub seat1.pub",
    lambda s: f"cd {s} && git -C divan checkout -- registry.json",
]


@pytest.mark.parametrize("make", RELATIVE_CDS, ids=range(len(RELATIVE_CDS)))
def test_a_relative_cd_into_a_bus_folder_is_a_store_write(make, srv):
    _assert_store_write(_sh(make(srv)))


GLOB_CDS = [
    lambda s: f"cd {s}/div* && cp forged.json registry.json",
    lambda s: f"cd {s}/k* && cp evil.pub seat1.pub",
    lambda s: f"cd {s}/{{divan,x}} && cp forged.json registry.json",
]


@pytest.mark.parametrize("make", GLOB_CDS, ids=range(len(GLOB_CDS)))
def test_a_glob_or_brace_cd_into_a_bus_folder_is_a_store_write(make, srv):
    _assert_store_write(_sh(make(srv)))


DELIVERY_FORMS = [
    lambda s: f"cp -t {s}/divan forged/registry.json",
    lambda s: f"cp --target-directory={s}/divan forged/registry.json",
    lambda s: f"cp --target-directory {s}/divan forged/registry.json",
    lambda s: f"cp -rt {s} evil/kd",
    lambda s: f"cp -r {{a,kd}} {s}/",
    lambda s: f"cd {s}/divan && cp -t . forged/registry.json",
    lambda s: f"unzip -d {s}/divan evil.zip",
    lambda s: f"unzip -d{s}/divan evil.zip",
    lambda s: f"cd {s}/divan && unzip evil.zip",
    lambda s: f"tar -C {s}/divan --ext -f evil.tar",
    lambda s: f"tar -C {s}/divan --extr -f evil.tar",
    lambda s: f"tar -C {s}/divan --extra -f evil.tar",
]


@pytest.mark.parametrize("make", DELIVERY_FORMS, ids=range(len(DELIVERY_FORMS)))
def test_a_delivery_form_into_a_bus_folder_is_a_store_write(make, srv):
    _assert_store_write(_sh(make(srv)))


NESTED_SHELLS = [
    lambda s: f"cd {s}/divan && bash -c 'cp f registry.json'",
    lambda s: f"cd {s}/divan && sh -c 'cp f registry.json'",
    lambda s: f"cd {s}/divan && eval 'cp f registry.json'",
]


@pytest.mark.parametrize("make", NESTED_SHELLS, ids=range(len(NESTED_SHELLS)))
def test_a_nested_shell_after_a_bus_cd_is_a_store_write(make, srv):
    # the write is seen through the nested shell, so its decide rule is not the floor rule; the category
    # and the ask are what the finding requires, including under an opaque_exec allow
    c = gate.classify(_sh(make(srv)))
    assert (c.category, gate.decide(c).outcome) == ("store_write", "ask"), make(srv)


@pytest.mark.parametrize("make, category", [
    (lambda s: f"cd {s}/divan && cp a.txt b.txt", "read"),        # the rest of a shared folder stays writable
    (lambda s: f"cd {s}/divan && printf x > notes.txt", "local_write"),
    (lambda s: f"cd {s}/divan && cat registry.json", "read"),     # a read of the file stays a read
    (lambda s: f"tar -C {s}/divan -czf /tmp/out.tar .", "shell"),  # creating an archive writes the archive
    (lambda s: f"tar -C {s}/divan -tf evil.tar", "shell"),        # listing writes nothing
    (lambda s: f"cp evil.pub {s}/", "shell"),                     # a plain name into the shared parent
    (lambda s: f"cp x {s}/notes.txt", "shell"),
    (lambda s: f"cd {s}/divan && cd /tmp && cp x registry.json", "store_write"),
    # a later cd never clears the bases: the gate cannot tell a cd that moves the shell from one that
    # does not (a subshell, a pipeline, the background, a dead branch, a failing cd), so the set only grows
], ids=range(8))
def test_neighbouring_forms_keep_their_class(make, category, srv):
    assert gate.classify(_sh(make(srv))).category == category
