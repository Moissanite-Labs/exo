"""Issue #337: a descriptor path spelt with a glob, or reached by chained relative cds, is a store write.

#305 matched descriptor paths and `cd` targets as written, but bash expands a glob before it opens the
file, so `> /dev/f[d]/3` opened `/dev/fd/3`, and a relative `cd dev` after `cd /` landed in `/dev`
without the gate joining the chain. The same gap existed for store paths themselves
(`> ~/.exo/sta?e/gate-grants.json`). A glob spelling now counts when it could name a descriptor path or
a store path, read part by part; a spelling that could name only the writer's stdout or stderr does not.

Repair notes (2026-10-27, against the 2026-10-25 review): the glob matcher is linear (indexed, never
sliced) and collapses `..` the way main does, so `/dev/s?m/../fd/3` counts; the chained-cd cap counts
parts of the NORMALISED path, so `./` padding and `x/../` padding cannot stop the tracking; and the
three-literal rule needs literals in the file's own part, so a folder name alone (`git add src/state/*`)
does not satisfy it. One deliberate divergence from the PR's draft: `/dev/fd/[12]` keeps main's answer
(store_write ask) — main's literal fd-folder match already asks there, and carrying the draft's
stdout-only exemption for bracket globs would loosen main.
Every command string here is data for exo.gate.classify/decide; none is run."""
import fnmatch

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
    c = gate.classify(action)
    d = gate.decide(c)
    return c.category, d.outcome, "only the principal approves" in d.reason


@pytest.mark.parametrize("cmd", [
    # the issue's rows: local_write or shell, and allowed, on main 5f434b15
    f'F={GRANTS}; exec 3< "$F"; printf F > /dev/f[d]/3',
    f'F={GRANTS}; exec 3< "$F"; printf F > /dev/[f]d/3',
    f'F={GRANTS}; exec 3< "$F"; printf F > /proc/sel[f]/fd/3',
    f'F={GRANTS}; exec 3< "$F"; printf F > /proc/*elf/fd/3',
    "exec 3< ~/.exo/sta?e/gate-grants.json; printf F > /dev/f?/3",
    "{ printf F > /dev/f[d]/3; } 3< ~/.exo/sta?e/gate-grants.json",
    "tee /dev/f?/3 < x",
    "cp x /dev/f[d]/3",
    "ln -s /dev/f? /tmp/x",
    f'F={GRANTS}; exec 3< "$F"; cd / && cd dev && printf F > fd/3',
    f'F={GRANTS}; exec 3< "$F"; cd / && cd proc/self && printf F > fd/3',
    f'F={GRANTS}; cd /de? && exec 3< "$F" && printf F > fd/3',
    # more spellings of the same routes
    "printf F > /proc/thread-sel[f]/fd/3",
    "printf F > /dev/fd/[0-9]",
    "printf F > /proc/*/fd/1",                     # some other process's stdout
    "printf F > /dev/f[d]/0",
    f'F={GRANTS}; cd / && cd de? && printf F > fd/3',
    f'F={GRANTS}; cd / && cd pr?c && cd self && printf F > fd/3',
    f'F={GRANTS}; cd /proc && cd sel[f] && printf F > fd/3',
    "ln -s /de? /tmp/x",                            # then /tmp/x/fd/3 is one
    "ln -s ~/.e?o /tmp/x",                          # then /tmp/x/state/... is the store
    # store paths spelt with a glob (the known limit GATE.md listed)
    "printf x > ~/.exo/sta?e/gate-grants.json",
    "cp x ~/.exo/sta?e/gate-grants.json",
    "tee ~/.exo/sta?e/gate-grants.json < x",
    "printf x > ~/.exo/polic*/default.yaml",
    "printf x > /tmp/sta?e/gate-grants.json",
    # controls: plain spellings, already store_write on main
    f'F={GRANTS}; exec 3< "$F"; printf F > /dev/fd/3',
    f'F={GRANTS}; exec 3< "$F"; cd /dev && printf F > fd/3',
    f'F={GRANTS}; exec 3< "$F"; cd /proc && cd self && printf F > fd/3',
])
def test_a_glob_spelling_or_chained_cd_is_a_store_write(cmd):
    assert _decided(_sh(cmd)) == ("store_write", "ask", True), cmd


@pytest.mark.parametrize("path", ["/dev/f[d]/3", "/proc/sel[f]/fd/3", "~/.exo/sta?e/gate-grants.json"])
def test_a_file_tool_write_through_a_glob_spelling_is_a_store_write(path):
    assert _decided({"tool": "write_file", "args": {"path": path, "content": "x"}}) == ("store_write", "ask", True)


@pytest.mark.parametrize("cmd, category", [
    # a glob that can name only the writer's stdout or stderr stays ordinary
    ("printf x > /dev/f[d]/1", "local_write"),
    ("printf x > /proc/sel[f]/fd/2", "local_write"),
    # ordinary globs and chained cds into ordinary folders are untouched
    ("printf x > /tmp/*", "local_write"),
    ("printf x > /dev/tty*", "local_write"),
    ("cd /tmp && printf x > fd/3", "local_write"),
    ("cd / && cd tmp && printf x > fd/3", "local_write"),
    ("cp x /tmp/*", "shell"),
    ("cp x /tmp/b*/r*", "shell"),                  # too little literal text to name bus/registry.json
    # reads stay reads
    ("ls /dev/tty*", "read"),
    ("cat /proc/*/status", "read"),
    ("cd /de? && ls", "read"),
])
def test_other_spellings_keep_their_class(cmd, category):
    assert gate.classify(_sh(cmd)).category == category, cmd


def test_bracket_std_glob_keeps_mains_answer():
    # Main's literal fd-folder match already asks for /dev/fd/[12]; the repair preserves main rather
    # than carrying the draft's stdout-only exemption for bracket globs, which would loosen main.
    assert _decided(_sh("printf x > /dev/fd/[12]")) == ("store_write", "ask", True)


@pytest.mark.parametrize("cmd", [
    f'F={GRANTS}; exec 3< "$F"; printf F > /dev/sh?/../fd/3',
    f'F={GRANTS}; exec 3< "$F"; printf F > /dev/s?m/../fd/3',
    f'F={GRANTS}; exec 3< "$F"; printf F > /proc/sel?/../self/fd/3',
    f'F={GRANTS}; tee /dev/sh?/../fd/3 < x',
    f'F={GRANTS}; cp x /dev/s?m/../fd/3',
])
def test_a_glob_through_dotdot_is_a_store_write(cmd):
    assert _decided(_sh(cmd)) == ("store_write", "ask", True), cmd


def test_cap_padding_does_not_stop_the_chain():
    # The cap counts parts of the normalised path: `./` padding and `x/../` padding no longer stop
    # the tracking and let a following descriptor write escape.
    assert _decided(_sh(
        f'F={GRANTS}; exec 3< "$F"; cd / && cd ' + "./" * 70 + "dev && printf F > fd/3"
    )) == ("store_write", "ask", True)
    assert _decided(_sh(
        f'F={GRANTS}; exec 3< "$F"; cd /dev && cd ' + "x/../" * 40 + " && printf F > fd/3"
    )) == ("store_write", "ask", True)


@pytest.mark.parametrize("cmd", [
    "git add src/state/*",
    "rm -rf src/state/*",
    "cp -r app/policies/* /tmp/o/",
    "sed -i s/a/b/ state/*.json",
])
def test_a_folder_name_alone_does_not_satisfy_the_literal_rule(cmd):
    # The three literals must be in the file's own part: a folder name alone never makes a glob a
    # principal-only store write.
    category, _, principal = _decided(_sh(cmd))
    assert not (category == "store_write" and principal), cmd


def test_ledger_glob_matches_the_literal():
    # The draft's hand-mirrored names drifted: the glob was allowed while the literal asked. The repair
    # reuses main's tables, so the glob now matches the literal.
    assert gate.classify(_sh("printf x > ledger.sqlite-shm")).category == "store_write"
    assert _decided(_sh("printf x > ledger.sqlite-s?m")) == ("store_write", "ask", True)


def test_chained_cd_into_proc_matches_absolute_cd():
    # Main already asks after an absolute `cd /proc` (a shell part after the landing is not a quiet
    # read); the chained form follows the same rule. #413's narrowing, when it lands, will govern both.
    assert _decided(_sh("cd / && cd proc && sort meminfo")) == ("store_write", "ask", True)


def test_long_glob_chain_is_linear_in_fnmatch_calls(monkeypatch):
    # The draft's matcher was quadratic (3.75s at 8k parts vs 0.40s on main) because it sliced the
    # remaining parts at every position. The repair indexes instead: ~3 fnmatch calls per part.
    calls = [0]
    orig = fnmatch.fnmatchcase

    def counting(s, pat):
        calls[0] += 1
        return orig(s, pat)

    monkeypatch.setattr(fnmatch, "fnmatchcase", counting)
    p = "/" + "/".join(f"q{i}z*" for i in range(8000))
    gate.classify(_sh(f"printf x > {p}"))
    assert calls[0] < 100000
