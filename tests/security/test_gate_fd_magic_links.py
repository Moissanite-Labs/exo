"""Issue #217: a write through a descriptor or process-folder path is a store write.

`/dev/fd/N`, `/proc/self/fd/N` and `/proc/self/cwd` name whatever the writing process holds there, which the gate
cannot resolve from its own process. On Linux `> /dev/fd/3` reopens the file behind descriptor 3 for writing, so
`exec 3< ~/.exo/state/gate-grants.json; printf X > /dev/fd/3` rewrote the grants with no ask. A store file opened
for reading on a numbered descriptor counts as a store write too, which covers stdout and stderr (`1<`, `2<`).
Every command string here is data for exo.gate.classify/decide; none is run."""
import pytest

from exo import gate

GRANTS = "~/.exo/state/gate-grants.json"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".exo" / "state").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("EXO_HOME", raising=False)
    for name in ("EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE", "EXO_SEAT"):
        monkeypatch.delenv(name, raising=False)
    return home


def _sh(cmd):
    return {"tool": "bash", "args": {"command": cmd}}


def _decided(action):
    """(category, outcome, whether the store floor holds): a line that also hides code asks first as opaque, and the
    floor then adds its reason to the same ask."""
    c = gate.classify(action)
    d = gate.decide(c)
    return c.category, d.outcome, "only the principal approves" in d.reason


@pytest.mark.parametrize("cmd", [
    # the issue's rows: local_write, and allowed, on main 4ac092b
    f"exec 3< {GRANTS}; printf FORGED > /dev/fd/3",
    "exec 3< ~/.exo/state; printf x > /proc/self/fd/3/custom.json",
    "{ printf x > /dev/fd/3/custom.json; } 3< ~/.exo/state",
    # every descriptor or process folder, however written
    "printf x > /dev/fd/3",
    "printf x >> /dev/fd/3",
    "printf x >| /dev/fd/3",
    "printf x &> /dev/fd/4",
    "printf x &>> /dev/fd/4",
    "printf x > /dev/fd/0",
    "printf x > /dev/stdin",
    "printf x > /dev/fd/$fd",
    f"exec {{fd}}< {GRANTS}; printf x > /dev/fd/$fd",
    "printf x > /proc/self/fd/3",
    "printf x > /proc/thread-self/fd/3",
    "printf x > /proc/self/task/7/fd/3",
    "printf x > /proc/1234/fd/3",
    "printf x > /proc/1234/fd/1",                   # another process's stdout
    "printf x > /proc/$$/fd/3",
    "printf x > /proc/self/cwd/custom.json",
    "printf x > /proc/self/root/tmp/x",
    "printf x > //dev/fd/3",
    "printf x > /dev/./fd/3",
    "printf x > /dev/fd/1/../3",
    "tee /dev/fd/3 < x",
    "cp x /dev/fd/3",
    "dd if=x of=/dev/fd/3",
    "ln -s /proc/self/fd/3 /tmp/l",
    "ln -s /dev /tmp/d",                            # then /tmp/d/fd/3 is one
    "ln -s /proc/self /tmp/p",
    # `..` below a descriptor folder is resolved from that folder, not from /dev
    "exec 3< ~/.exo/principals/me; printf x > /dev/fd/3/../../policies/default.yaml",
    "printf x > /dev/fd/1/../3",
    # into a descriptor folder, or one above it, then a relative name
    "cd /dev/fd/3 && printf x > custom.json",
    "cd /proc/self/cwd && printf x > custom.json",
    "cd /dev && printf x > fd/3",
    "cd /proc/self && printf x > fd/3",
    "cd / && printf x > dev/fd/3",
    "cd / && printf x > proc/self/fd/3",
    # a store file opened for reading on stdout, stderr or another numbered descriptor
    f"{{ printf FORGED > /dev/stdout; }} 1< {GRANTS}",
    f"{{ printf FORGED > /dev/stderr; }} 2< {GRANTS}",
    f"printf FORGED 1<{GRANTS} > /dev/stdout",
    f"bash -c 'printf FORGED > /dev/stdout' 1< {GRANTS}",
    f"exec 2< {GRANTS}",                            # a persistent shell writes it back on a later call
    f"exec 3< {GRANTS}",
    f"while read -u 3 l; do echo $l; done 3< {GRANTS}",
    '{ printf FORGED > /dev/stdout; } 1< "$F"',     # stdout or stderr from a path in a variable
])
def test_a_write_through_a_descriptor_path_is_a_store_write(cmd):
    assert _decided(_sh(cmd)) == ("store_write", "ask", True), cmd


@pytest.mark.parametrize("path", ["/dev/fd/3", "/proc/self/fd/3/custom.json", "/proc/self/cwd/x.json", "/dev/stdin"])
def test_a_file_tool_write_through_a_descriptor_path_is_a_store_write(path):
    assert _decided({"tool": "write_file", "args": {"path": path, "content": "x"}}) == ("store_write", "ask", True)


@pytest.mark.parametrize("cmd, category", [
    # stdout and stderr stay ordinary
    ("printf x > /dev/fd/1", "local_write"),
    ("printf x > /dev/fd/2", "local_write"),
    ("printf x > /dev/fd/01", "local_write"),
    ("printf x > /proc/self/fd/1", "local_write"),
    ("printf x > /proc/self/fd/2", "local_write"),
    ("printf x > /dev/stdout", "read"),
    ("printf x > /dev/stderr", "read"),
    ("printf x > /dev/null", "read"),
    ("echo hi >&2", "read"),
    ("exec 3>&1; ls 2>&1 >&3", "read"),
    # reading a descriptor, or opening a non-store file on one
    ("cat /dev/fd/3", "read"),
    ("ls -la /proc/self/fd/", "read"),
    ("exec 3< /tmp/x; cat <&3", "read"),
    ('exec 3< "$F"', "read"),
    (f"cat < {GRANTS}", "read"),                    # stdin from the store is a plain read
    (f"cat 0< {GRANTS}", "read"),
    (f"jq . {GRANTS} > /dev/stdout", "read"),
    ("cd /dev && ls", "read"),
    ("cd /proc && cat cpuinfo", "read"),
    ("diff <(ls) <(ls)", "read"),
    ("printf x > /tmp/proc/self/fdx", "local_write"),
    ("printf x > /dev/fd/1/", "local_write"),
    ("ls /dev > /tmp/x", "local_write"),            # naming /dev is not writing through it
    ("find /proc -maxdepth 1 > /tmp/x", "local_write"),
])
def test_stdout_reads_and_other_paths_keep_their_class(cmd, category):
    assert gate.classify(_sh(cmd)).category == category, cmd


def test_a_descriptor_number_of_any_length_never_raises():
    assert gate.classify(_sh("9" * 5000 + f"< {GRANTS} cat")).category == "store_write"
    assert gate.classify(_sh(f"01< {GRANTS} cat")).category == "store_write"


def test_the_lexer_keeps_the_descriptor_of_a_redirection():
    cmds = gate._parse("exec 3< a 10<b 2>c < d {x}<e")[0]
    assert [(op, t.text, t.fd) for op, t in cmds[0].redirs] == [
        ("<", "a", "3"), ("<", "b", "10"), (">", "c", "2"), ("<", "d", ""), ("<", "e", "")]
