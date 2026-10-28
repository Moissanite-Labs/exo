"""#471 composed with main f28934a3's `env -S` (#591, #707) and `sudo -s`/`-i` stdin (#491) readings.

Bash reads `X='a b' cmd` as an assignment and runs cmd (#471). Main f28934a3 reads the quoted word as the program, an
unknown one that asks, so it never reads an `env -S` text or a sudo shell's stdin behind it: `X='a b' env -S 'rm -rf
/srv/data'` and `X='a b' sudo -s <<< 'rm -rf /srv/data'` ask under `deny: [delete]`, where the same lines with
`X=ab` deny. This branch reads the command after the quoted prefix with each of main's readings of it, and keeps
the call as main read it (``before``) beside that reading, with main's own `env -S` readings
(``split_readings``) inside it. So each line here decides at least as it does with a plain prefix (`X=ab`) and at
least as it does with the word quoted whole (`'X=a b'`, the program on every tree, as main read the quoted
prefix). Classification and decision only; nothing here runs the lines.
"""
import shlex

import pytest

from exo.gate import (CATEGORIES, OUTCOMES, binds_exact, call_approvers, classify, decide, no_grant,
                      normalize_approvals, normalize_policies, readings)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".exo").mkdir(parents=True)
    (h / ".ssh").mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("EXO_HOME", str(h / ".exo"))
    for k in ("EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL"):
        monkeypatch.delenv(k, raising=False)


def sh(command):
    return classify({"tool": "bash", "args": {"command": command}})


def dq(s):
    """``s`` in double quotes for the outer shell."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`") + '"'


RM = "rm -rf /srv/data"
STORE = "cp /tmp/x ~/.exo/policies/x"
SECRET = "cat ~/.ssh/id_rsa"
PAYLOADS = [RM, "git push --force origin main", SECRET, STORE]
QUOTED = ["X='a b'", 'X="$Y"', "X=a\\ b"]
PLAIN = "X=ab"             # the same assignment, unquoted
WHOLE = "'X=a b'"          # a word quoted whole: the program on every tree, as main read `X='a b'`
# Each spelling of env -S that GNU env 9.4 takes, the ones main's #707 reads only through its split readings too.
SPELLINGS = ["env -S {}", "env -S{}", "env --split-string={}", "env --split-string {}", "env --split {}",
             "env -iS {}", "env -i0vS{}", "env --split-s={}", "sudo env -S {}", "nice env -S {}",
             "env -u HOME -S {}"]
# Assignments ahead of the command in the -S text, as env -S reads them (#707).
IN_TEXT = ["", "'Y=c d' ", "-- 'Y=c d' ", "'Y=c d';x "]

POLICIES = {
    "default": None,
    "deny_delete": {"gates": {"deny": ["delete"]}},
    "deny_store": {"gates": {"deny": ["store_write"]}},
    "deny_credentials": {"gates": {"deny": ["credentials"]}},
    "deny_force": {"gates": {"deny": ["force_push"]}},
    "opaque_allow": {"gates": {"categories": {"opaque_exec": "allow"}}},
    "opaque_deny": {"gates": {"categories": {"opaque_exec": "deny"}}},
    "all_allow": {"gates": {"categories": {c: "allow" for c in CATEGORIES}}},
    "reserved_delete": {"gates": {"reserved": ["delete"]}},
    "reserved_opaque": {"gates": {"reserved": ["opaque_exec"]}},
    "host_opaque": {"gates": {"rules": [{"match": {"category": "opaque_exec", "target": "host"}, "decision": "deny"}]}},
}
NORM = {k: normalize_policies(v) if v else None for k, v in POLICIES.items()}
DENY_DELETE, DENY_STORE, DENY_CREDENTIALS = NORM["deny_delete"], NORM["deny_store"], NORM["deny_credentials"]
DELEGATED = normalize_approvals({"approvers": {"default": ["principal", "seat:a"]}})


def rank(d):
    return OUTCOMES.index(d.outcome)


def at_least(line, other):
    """``line`` decides at least as ``other`` under each policy: the outcome, each reserved hit and violation, the
    exact binding, no grant and the approvers."""
    c, o = sh(line), sh(other)
    for name, pol in NORM.items():
        d, e = decide(c, pol), decide(o, pol)
        assert rank(d) >= rank(e), (line, other, name, d, e)
        assert not [h for h in e.reserved if h not in d.reserved], (line, other, name, d.reserved, e.reserved)
        assert not [v for v in e.violations if v not in d.violations], (line, other, name)
        assert binds_exact(c, d) or not binds_exact(o, e), (line, other, name)
    assert no_grant(c) or not no_grant(o), (line, other)
    assert set(call_approvers(c, DELEGATED)) <= set(call_approvers(o, DELEGATED)), (line, other)


# --- 1. A quoted prefix before an `env -S` text, in each spelling (fix rows: main f28934a3 asks) ---------------------

ENV_DELETE = [f"{p} " + s.format(q(a + RM)) for p in QUOTED for s in SPELLINGS for a in IN_TEXT
              for q in (shlex.quote, dq)]


@pytest.mark.parametrize("line", ENV_DELETE)
def test_a_delete_in_an_env_s_text_behind_a_quoted_prefix_is_denied(line):
    c = sh(line)
    assert decide(c, DENY_DELETE).outcome == "deny", (line, decide(c, DENY_DELETE))
    assert binds_exact(c, decide(c, NORM["default"])), line


ENV_COMPOSED = [(p, s.format(shlex.quote(a + pay))) for p in QUOTED for s in SPELLINGS for a in IN_TEXT[:2]
                for pay in PAYLOADS]


@pytest.mark.parametrize("prefix,rest", ENV_COMPOSED)
def test_an_env_s_line_behind_a_quoted_prefix_decides_at_least_as_with_a_plain_one(prefix, rest):
    at_least(f"{prefix} {rest}", f"{PLAIN} {rest}")


@pytest.mark.parametrize("prefix,rest", ENV_COMPOSED[::4])
def test_an_env_s_line_behind_a_quoted_prefix_decides_at_least_as_main_read_it(prefix, rest):
    at_least(f"{prefix} {rest}", f"{WHOLE} {rest}")


def test_mains_env_s_readings_stay_inside_the_reading_as_before():
    """The reading as before (``before``) is the call as main f28934a3 reads it, its `env -S` readings and all: a
    spelling #707 reads as `-S TEXT` keeps main's split readings there, and each is one of the line's readings."""
    c = sh("X='a b' env --split-s=\"'Y=c d' rm -rf /srv/data\"")
    assert c.before is not None and c.split_readings, c
    rs = readings(c)
    assert any(r is c.before for r in rs) and all(any(r is x for x in rs) for r in c.split_readings)
    assert "unknown program" in c.before.signals or c.before.category == "opaque_exec"


# --- 2. `env -S` inside a quoted prefix's value, and a quoted prefix inside an `env -S` text -----------------------

INSIDE = [
    f"GIT_SSH_COMMAND='env -S \"{RM}\"' git fetch",
    f"X=\"$(env -S '{RM}')\" ls",
    f"X=\"$(env -S '{RM}')\" git push origin feature",
    f"X='a b' env -S \"X='c d' {RM}\"",
    "env -S " + shlex.quote(f"X='a b' {RM}"),
    "env -S " + dq(f"X='a b' {RM}"),
    "env --split-s=" + dq(f"X='a b' {RM}"),
    "env -iS " + dq(f"X='a b' {RM}"),
    "sh -c " + shlex.quote("env -S " + dq(f"X='a b' {RM}")),
    "bash -c " + shlex.quote("X='a b' env -S " + shlex.quote(RM)),
    "env -iS'\\q' sh -c " + shlex.quote("env -S " + dq(f"'X=a b' {RM}")),
    f"git push origin feature && X='a b' env -S {shlex.quote(RM)}",
]


@pytest.mark.parametrize("line", INSIDE)
def test_env_s_and_a_quoted_prefix_inside_each_other_deny_the_delete(line):
    assert decide(sh(line), DENY_DELETE).outcome == "deny", line


# --- 3. A quoted prefix with a sudo -s or -i shell's stdin (#491) ----------------------------------------------------

SUDO_FORMS = ["{p} sudo -s <<< {q}", "{p} sudo -i <<< {q}", "echo {q} | {p} sudo -s", "{p} sudo --login <<< {q}",
              "{p} sudo -u $U <<< {q}", "{p} doas -s <<< {q}", "{p} env sudo -s <<< {q}", "{p} sudo env -S {q}",
              "git push origin feature && {p} sudo -s <<< {q}"]
SUDO = [(f, p) for f in SUDO_FORMS for p in QUOTED]


@pytest.mark.parametrize("form,prefix", SUDO)
def test_a_sudo_shell_behind_a_quoted_prefix_reads_its_stdin(form, prefix):
    line = form.format(p=prefix, q=shlex.quote(RM))
    assert decide(sh(line), DENY_DELETE).outcome == "deny", line
    store = form.format(p=prefix, q=shlex.quote(STORE))
    c = sh(store)
    assert decide(c, DENY_STORE).outcome == "deny", store
    assert no_grant(c) and call_approvers(c, DELEGATED) == ["principal"], store


@pytest.mark.parametrize("form,prefix", SUDO)
def test_a_sudo_shell_behind_a_quoted_prefix_decides_at_least_as_with_a_plain_one(form, prefix):
    for pay in (RM, SECRET):
        at_least(form.format(p=prefix, q=shlex.quote(pay)), form.format(p=PLAIN, q=shlex.quote(pay)))
        at_least(form.format(p=prefix, q=shlex.quote(pay)), form.format(p=WHOLE, q=shlex.quote(pay)))


@pytest.mark.parametrize("line", [
    f"sudo -s <<< {dq(f'X={chr(39)}a b{chr(39)} {RM}')}",
    f"echo {dq(f'X={chr(39)}a b{chr(39)} {RM}')} | sudo -i",
    f"sudo -iu root X='a b' {RM}",
])
def test_a_quoted_prefix_in_a_sudo_shells_stdin_hides_no_delete(line):
    assert decide(sh(line), DENY_DELETE).outcome == "deny", line


# --- 4. Category parts and approvals behind a quoted prefix through env -S and sudo ----------------------------------

@pytest.mark.parametrize("prefix", QUOTED)
@pytest.mark.parametrize("form", ["{p} env -S {q}", "{p} env --split-s={q}", "{p} sudo -s <<< {q}",
                                  "{p} cd ~/.exo && env -S {q}"])
def test_a_store_write_or_secret_keeps_its_category_part_and_the_principal(prefix, form):
    for pay, pol in ((STORE, DENY_STORE), (SECRET, DENY_CREDENTIALS)):
        line = form.format(p=prefix, q=shlex.quote(pay))
        c = sh(line)
        d = decide(c, pol)
        assert d.outcome == "deny", (line, d)
        assert no_grant(c) and binds_exact(c, decide(c, NORM["default"])), line
        # a store write is the principal's alone; a secret read keeps no approver its plain-prefix line lacks
        plain = call_approvers(sh(form.format(p=PLAIN, q=shlex.quote(pay))), DELEGATED)
        assert set(call_approvers(c, DELEGATED)) <= set(plain), line
        if pay == STORE:
            assert call_approvers(c, DELEGATED) == ["principal"], line


@pytest.mark.parametrize("prefix", QUOTED)
def test_a_logged_push_beside_an_env_s_delete_behind_a_quoted_prefix_keeps_each_reserved_hit(prefix):
    line = f"git push origin feature && {prefix} env -S {shlex.quote(RM)}"
    d = decide(sh(line), NORM["reserved_delete"])
    plain = decide(sh(line.replace(prefix, PLAIN)), NORM["reserved_delete"])
    assert d.outcome == "ask" and plain.reserved and all(h in d.reserved for h in plain.reserved), (d, plain)
