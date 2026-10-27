"""Issue #336: a store file held on a descriptor reaches stdout or stderr by a copy.

#217/#305 made a store file opened for reading on a numbered descriptor a store write, because a later
`> /dev/fd/N` or `> /dev/stdout` reopens it for writing. That read left out stdin and copies: a store file
opened on stdin (`< G`, also a compound's) or on descriptor 3 through a spelling the gate cannot pin to a
path, then copied onto stdout or stderr (`1<&0`, `>&0`, `2>&0`, `1<&3`, `N<&M-`), is written back by the
next write there, with no link or hardlink involved. The gate now follows what each descriptor holds
across a line's commands -- a compound's redirections bind the commands inside it, an exec's persist --
and a copy onto descriptor 1 or 2 from a held one is a store write. Every command string here is data
for exo.gate.classify/decide; none is run."""
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
    # the issue's rows: read or local_write, and allowed, on main b419f91b
    f"printf F < {GRANTS} 1<&0 > /dev/stdout",
    f"printf F < {GRANTS} >&0 > /dev/stdout",
    f"printf F 0< {GRANTS} 1>&0 > /dev/stdout",
    f"printf F < {GRANTS} 1<&0- > /dev/stdout",
    f"printf F < {GRANTS} 1<&0 >> /dev/stdout",
    f"exec < {GRANTS}; exec 1<&0; printf F > /dev/stdout",
    f"exec < {GRANTS}; exec 2>&0; printf F > /dev/stderr",
    f"printf F < {GRANTS} 1<&0 > /dev/fd/1",
    f"exec < {GRANTS}; exec 1<&0; printf F > /proc/self/fd/1",
    f"printf F < {GRANTS} 2<&0 2> /proc/thread-self/fd/2",
    f"exec < {GRANTS}; exec 1<&0; echo F | tee /dev/stdout",
    f"{{ exec 1<&0; printf F > /dev/stdout; }} < {GRANTS}",
    f"{{ printf F > /dev/stdout; }} < {GRANTS} >&0",
    f'F={GRANTS}; exec 3< "$F"; exec 1<&3; printf F > /dev/stdout',
    # chains and other compounds: what a descriptor holds travels through copies and group stdin
    f'F={GRANTS}; exec 3< "$F"; exec 4<&3; exec 1<&4; printf F > /dev/stdout',
    f"exec < {GRANTS}; exec 5<&0; exec 2<&5; printf F > /dev/stderr",
    f"( exec 1<&0; printf F > /dev/stdout ) < {GRANTS}",
    f"while read l; do exec 1<&0; done < {GRANTS}",
    f"cat < {GRANTS} 1<&0",                       # one command: its stdin is the file the copy lands on stdout
    f"exec < {GRANTS}; cat 1<&0",                 # a plain command's copy inherits the shell's stdin
    "exec < ~/.exo/sta*/gate-grants.json; exec 1<&0; printf F",   # a glob target held on stdin
    "exec < gate-grants.json; exec 1<&0; printf F",               # a relative name held on stdin
    # repair pins (#336 reviews): compound pairing, seed restore, unpinned sources, command --, no-space, subs
    f"exec < {GRANTS}; ( printf F > /dev/stdout ) 1<&0",          # a subshell closer seeds its own commands
    f"{{ ( printf F > /dev/stdout ) 1<&0; }} < {GRANTS}",         # a subshell inside a group, both seeded
    f"if :; then {{ printf F 1<&0 > /dev/stdout; }}; fi < {GRANTS}",  # an if's stdin reaches a nested group
    f"exec < {GRANTS}; {{ :; }} < /dev/null; exec 1<&0; printf F > /dev/stdout",  # a group's stdin ends at }
    f"n=0; exec < {GRANTS}; exec 1<&$n; printf F > /dev/stdout",   # a variable source fails closed, not cleared
    f"exec < {GRANTS}; exec 1<&{{0..0}}; printf F > /dev/stdout",  # a brace source with one digit is that digit
    f"exec < {GRANTS}; exec 1<&{{0,1}}; printf F > /dev/stdout",   # several digits fail closed, not cleared
    f"command -- exec < {GRANTS}; exec 1<&0; printf F > /dev/stdout",  # `command --` still reaches exec
    f"printf F < {GRANTS} 1<&0>/dev/stdout",                       # no space before the next redirection
    f"exec < {GRANTS}; : $(printf F 1<&0 > /dev/stdout)",         # a substitution inherits the shell's descriptors
    f"exec < {GRANTS}; ( exec 1<&0; printf F > /dev/stdout )",    # a subshell inherits them too
    f"exec < {GRANTS}; exec 1<&0-; printf F > /dev/stdout",       # the copy hits before the close
])
def test_a_copy_onto_stdout_or_stderr_from_a_held_descriptor_is_a_store_write(cmd):
    assert _decided(_sh(cmd)) == ("store_write", "ask", True), cmd


@pytest.mark.parametrize("cmd, category", [
    # reading the store through stdin stays a read: no copy onto stdout or stderr
    (f"cat < {GRANTS}", "read"),
    (f"cat 0< {GRANTS}", "read"),
    (f"jq . < {GRANTS} > /dev/stdout", "read"),
    (f"wc -c < {GRANTS} > /dev/stdout", "read"),
    # copies among descriptors that hold nothing the gate tracks
    ("exec 3< /tmp/x; cat <&3", "read"),
    ("exec 3>&1; ls 2>&1 >&3", "read"),
    ('exec 3< "$F"', "read"),                     # held on 3, never copied: the open check's own limit
    ("exec < /tmp/x; exec 1<&0; printf F > /dev/stdout", "read"),   # a pinned non-store file on stdin
    # a command's own stdin redirection binds only itself, so the later exec copies the shell's stdin
    (f"cat < {GRANTS}; exec 1<&0", "read"),
    # order inside one command: the copy runs before the open, so it duplicates the shell's stdin
    (f"printf F 1<&0 < {GRANTS} > /dev/stdout", "read"),
    # a descriptor closed before the copy, or rebound to a plain file and never copied after
    ('exec < ' + GRANTS + '; exec 0<&-; exec 1<&0; printf F', "read"),
    ('exec 3< "$F"; exec 3<&-; exec 1<&3; printf F > /dev/stdout', "read"),
    # repair pins: what a compound holds ends at its closer, and a fed body does not inherit stdin
    ('exec < /dev/null; { exec < ' + GRANTS + '; }; exec 1<&0; printf F > /dev/stdout', "read"),
    ('exec < /dev/null; { exec 0</dev/null; } < ' + GRANTS + '; exec 1<&0; printf F > /dev/stdout', "read"),
    (f"exec < {GRANTS}; {{ exec 1<&0; printf F > /dev/stdout; }} < /dev/null", "read"),
    (f"exec < {GRANTS}; exec 0<&0-; exec 1<&0; printf F", "read"),
    (f"exec < {GRANTS}; cat > >(printf F 1<&0 > /dev/stdout)", "local_write"),
])
def test_reads_and_copies_that_hold_nothing_stay_classified(cmd, category):
    assert gate.classify(_sh(cmd)).category == category, cmd
