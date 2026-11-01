"""A rule or entry keyed on a category and an ssh hop's host reaches the remote command behind a quoted prefix
assignment (#852, a follow-up of #546).

Behind `X=ab ssh host 'echo hi > /tmp/f'` the remote write leads the line, its own path is its target and the host
is among its other targets, so decide reads it on the host too (#399, #493): `{category: local_write, target:
host*}` denies it and `reserved: [{category: local_write, target: host*}]` lists `(local_write, host)`. Behind
`X='a b'` the gate also keeps main's reading of the line (#471), where `X=a b` is an unknown program. That part
outranks a reversible write, a web read or a read with a target of its own, so it led, and the line was decided on
its targets only: the rule asked as opaque code and the reserved hit was lost, below the `X=ab` twin. Each part read
behind the quoted word now has a copy on each of its other targets among its parts, so the rule and the entry meet
it as a part (D-075), and main's reading stays beside it. Classification and decision only; nothing is executed.
"""
import pytest

from exo.gate import OUTCOMES, classify, decide, no_grant, normalize_policies
from scripts.gate_diff import BUILTIN_POLICIES

ISSUE = "X='a b' ssh host 'echo hi > /tmp/f'"

# The quoted prefixes bash reads as assignments before the command (#471), and one quoted value after a plain one.
PREFIXES = ["X='a b'", 'X="a b"', "X=$'a b'", "X='a b' Y='c d'", "X=ab Y='c d'", "X=a\\ b", 'X="$Y"']
# The hops the gate reads a host from (ssh and mosh), with an option, a user, a URL, `--`, and behind a wrapper.
HOPS = ["ssh host", "ssh -p 22 host", "ssh user@host", "ssh -l user host", "ssh ssh://user@host:22", "ssh host --",
        "ssh -t host", "mosh host", "env ssh host", "timeout 5 ssh host", "nice ssh host", "command ssh host"]
# Remote commands that main's unknown program outranks: each has a target of its own, so the host is only among
# its other targets. (body, the category of the remote command)
FIX_BODIES = [("'echo hi > /tmp/f'", "local_write"), ("'echo hi >> ~/.bashrc'", "local_write"),
              ("'curl https://example.com/x'", "web_read"), ("'wget https://example.com/x'", "web_read"),
              ("'gh pr view 1 -R o/r'", "read")]
# Remote commands that outrank main's unknown program, so they lead the line and are read on the host on main too.
LEAD_BODIES = [("'rm -rf /srv/data'", "delete"), ("'cp x ~/.exo/state/y'", "store_write"),
               ("git push origin main", "vcs_push"), ("'git push --force origin main'", "force_push"),
               ("'curl -X POST -d x https://example.com/x'", "web_post")]


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "home" / ".exo"))


def sh(command):
    return {"tool": "bash", "args": {"command": command}}


def gates(**g):
    return normalize_policies({"gates": g})


def host_policies(cat, pat="host*"):
    """The issue's policies for a remote command of category ``cat``: a rule on the category and the host, a rule on
    the host alone, a reserved entry on the two, and a deny entry on the two."""
    m = {"category": cat, "target": pat}
    return {"rule": gates(rules=[{"match": m, "decision": "deny"}]),
            "target_rule": gates(rules=[{"match": {"target": pat}, "decision": "deny"}]),
            "reserved": gates(reserved=[m]),
            "deny": gates(deny=[m])}


def twin_policies(cat):
    """Policies keyed on the hop's host for the twin rows: category and target rules that deny or ask, target rules,
    reserved and deny entries in each form, with the host as written and as a pattern."""
    out = {}
    for pat in ("host", "host*", "*host*"):
        m = {"category": cat, "target": pat}
        for decision in ("deny", "ask"):
            out[f"rule {decision} {cat} {pat}"] = gates(rules=[{"match": m, "decision": decision}])
            out[f"target rule {decision} {pat}"] = gates(rules=[{"match": {"target": pat}, "decision": decision}])
        out[f"reserved {cat} {pat}"] = gates(reserved=[m])
        out[f"reserved target {pat}"] = gates(reserved=[f"target:{pat}"])
        out[f"deny {cat} {pat}"] = gates(deny=[m])
        out[f"deny target {pat}"] = gates(deny=[f"target:{pat}"])
        # main's unknown program allowed: the deny and reserved entries still reach the remote command
        for kind in ("rule deny", "reserved", "deny"):
            name = f"{kind} {cat} {pat}"
            raw = {"categories": {"opaque_exec": "allow"}}
            raw.update({"rule deny": {"rules": [{"match": m, "decision": "deny"}]}, "reserved": {"reserved": [m]},
                        "deny": {"deny": [m]}}[kind])
            out[f"{name} +opaque_exec allow"] = gates(**raw)
    return out


BUILTIN = {k: normalize_policies(v) for k, v in BUILTIN_POLICIES.items()}


def at_least(d, twin):
    """``d`` is no lower than ``twin``: its outcome, and at an equal outcome below deny, each reserved hit."""
    assert OUTCOMES.index(d.outcome) >= OUTCOMES.index(twin.outcome), (d, twin)
    if d.outcome == twin.outcome != "deny":
        assert set(twin.reserved) <= set(d.reserved), (d.reserved, twin.reserved)


# --- (a) fix rows: each fails on main d742d1bb ----------------------------------------------------------------------

def test_the_issue_rows():
    c = classify(sh(ISSUE))
    pols = host_policies("local_write")
    d = decide(c, pols["rule"])
    assert (d.outcome, d.rule) == ("deny", "rule"), d          # main: ask (opaque)
    d = decide(c, pols["reserved"])
    assert d.outcome == "ask" and ("local_write", "host") in d.reserved, d     # main: the hit was lost
    d = decide(c, pols["deny"])
    assert (d.outcome, d.rule) == ("deny", "deny"), d          # main: ask (opaque)


@pytest.mark.parametrize("body, cat", FIX_BODIES)
@pytest.mark.parametrize("hop", HOPS)
@pytest.mark.parametrize("prefix", PREFIXES)
def test_a_category_and_host_entry_reaches_the_command_behind_a_quoted_prefix(prefix, hop, body, cat):
    c = classify(sh(f"{prefix} {hop} {body}"))
    pols = host_policies(cat)
    assert decide(c, pols["rule"]).outcome == "deny", c
    assert decide(c, pols["deny"]).outcome == "deny", c
    d = decide(c, pols["reserved"])
    assert d.outcome == "ask" and (cat, "host") in d.reserved, d


@pytest.mark.parametrize("body, cat", FIX_BODIES)
@pytest.mark.parametrize("prefix", PREFIXES)
def test_the_user_and_host_as_written_are_reached_too(prefix, body, cat):
    c = classify(sh(f"{prefix} ssh user@host {body}"))
    pols = host_policies(cat, "user@host")
    assert decide(c, pols["rule"]).outcome == "deny", c
    d = decide(c, pols["reserved"])
    assert d.outcome == "ask" and (cat, "user@host") in d.reserved, d


# #887 review: a nameref whose subscript runs a hop is read when the name is used, after the command behind the
# quoted word (ctx["late"]); that part is signed there too, and gets its copy on the host as the others do.
DEFERRED = ['declare -n r="x[\\$(ssh host {body})]"; echo $r', 'typeset -n r="x[\\$(ssh host {body})]"; echo $r',
            'declare -n r="x[\\$(ssh host {body})]"; r=1', 'declare -n r="x[\\$(ssh host {body})]"; echo "${{r}}"',
            'declare -n r="x[\\$(ssh host {body})]"; : $((r))']


def test_the_review_rows():
    for prefix in ("X=ab", "X='a b'"):
        c = classify(sh(f"{prefix} declare -n r=\"x[\\$(ssh host 'echo hi > /tmp/f')]\"; echo $r"))
        d = decide(c, gates(deny=[{"category": "local_write", "target": "host*"}]))
        assert (d.outcome, d.rule) == ("deny", "deny"), (prefix, d)      # edf9b2fb: X='a b' asked (opaque)


@pytest.mark.parametrize("body, cat", FIX_BODIES)
@pytest.mark.parametrize("form", DEFERRED)
@pytest.mark.parametrize("prefix", ["X='a b'", "X='a b' Y='c d'", 'X="$Y"'])
def test_a_part_read_later_behind_a_quoted_prefix_is_reached_too(prefix, form, body, cat):
    c = classify(sh(f"{prefix} {form.format(body=body)}"))
    pols = host_policies(cat)
    assert decide(c, pols["rule"]).outcome == "deny", c
    assert decide(c, pols["deny"]).outcome == "deny", c
    d = decide(c, pols["reserved"])
    assert d.outcome == "ask" and (cat, "host") in d.reserved, d


@pytest.mark.parametrize("line", [
    "X='a b' ssh host 'echo hi > /tmp/f'; echo \"${A:-'}'}\"",
    "X='a b' Z=\"${A#'}'}\" ssh host 'echo hi > /tmp/f'",
    "X=ab Z=\"${A#'}'}\" ssh host 'echo hi > /tmp/f'",
])
def test_both_of_158s_readings_have_the_copy(line):
    # #158 (main 080e87ba): a ${..} word bash and the lexer before #143 may read apart is read both ways, and the
    # call read the old way is c.flat; the command behind the quoted word gets its copy on the host in each reading
    c = classify(sh(line))
    assert c.flat is not None, line
    pols = host_policies("local_write")
    for r in (c, c.flat):
        assert decide(r, pols["deny"]).outcome == "deny", r          # main d3cbd874: ask (opaque)
        assert decide(r, pols["rule"]).outcome == "deny", r
        d = decide(r, pols["reserved"])
        assert d.outcome == "ask" and ("local_write", "host") in d.reserved, d


@pytest.mark.parametrize("body, cat", FIX_BODIES + LEAD_BODIES)
@pytest.mark.parametrize("hop", ["ssh host", "ssh -p 22 host", "mosh host"])
def test_a_reason_that_names_the_copy_names_the_host(hop, body, cat):
    # #887 review: the copy's summary says where it lands, as decide's reason does for a part that leads there
    d = decide(classify(sh(f"X='a b' {hop} {body}")), host_policies(cat)["rule"])
    assert d.outcome == "deny" and "host" in d.reason, d


# --- (b) guard rows: main decides these as here ---------------------------------------------------------------------

@pytest.mark.parametrize("line", ["ssh host 'echo hi > /tmp/f'", "X=ab ssh host 'echo hi > /tmp/f'"])
def test_the_issue_guard_rows(line):
    c = classify(sh(line))
    pols = host_policies("local_write")
    assert decide(c, pols["rule"]).outcome == "deny"
    assert decide(c, pols["target_rule"]).outcome == "deny"
    d = decide(c, pols["reserved"])
    assert d.outcome == "ask" and d.reserved == [("local_write", "host")], d


@pytest.mark.parametrize("body, cat", FIX_BODIES + LEAD_BODIES)
@pytest.mark.parametrize("hop", ["ssh host", "ssh user@host", "mosh host"])
def test_an_assignment_env_reads_is_reached_as_on_main(hop, body, cat):
    # `env X='a b' ssh ..`: env takes the quoted word as an assignment itself, so main read the hop there too
    c = classify(sh(f"env X='a b' {hop} {body}"))
    pols = host_policies(cat)
    assert decide(c, pols["rule"]).outcome == "deny" and decide(c, pols["deny"]).outcome == "deny"
    d = decide(c, pols["reserved"])
    assert d.outcome == "ask" and (cat, "host") in d.reserved, d
    _twin_check(f"env X='a b' {hop} {body}", f"env X=ab {hop} {body}", twin_policies(cat).values())


@pytest.mark.parametrize("body, cat", FIX_BODIES + LEAD_BODIES)
@pytest.mark.parametrize("hop", HOPS)
@pytest.mark.parametrize("prefix", PREFIXES)
def test_a_rule_on_the_host_alone_denies_as_on_main(prefix, hop, body, cat):
    assert decide(classify(sh(f"{prefix} {hop} {body}")), host_policies(cat)["target_rule"]).outcome == "deny"


@pytest.mark.parametrize("hop, body, cat", [
    *((hop, body, cat) for hop in ("ssh host", "ssh user@host", "mosh host", "sudo ssh host")
      for body, cat in LEAD_BODIES),
    ("sudo ssh host", "'echo hi > /tmp/f'", "local_write"),      # sudo makes the write costly, so it leads
])
@pytest.mark.parametrize("prefix", PREFIXES)
def test_a_remote_command_that_leads_is_reached_as_on_main(prefix, hop, body, cat):
    c = classify(sh(f"{prefix} {hop} {body}"))
    pols = host_policies(cat)
    assert decide(c, pols["rule"]).outcome == "deny" and decide(c, pols["deny"]).outcome == "deny"
    d = decide(c, pols["reserved"])
    assert d.outcome == "ask" and (cat, "host") in d.reserved, d


# --- (c) twin rows: a quoted prefix decides at least as strictly as its X=ab twin -----------------------------------

def _twin_check(line, twin_line, pols):
    # decide() already folds the line read as before #471 (c.before) into d, so only the X=ab twin is compared here
    c, twin = classify(sh(line)), classify(sh(twin_line))
    assert no_grant(c) or not no_grant(twin), c
    for pol in pols:
        at_least(decide(c, pol), decide(twin, pol))


@pytest.mark.parametrize("body, cat", FIX_BODIES + LEAD_BODIES)
@pytest.mark.parametrize("hop", HOPS + ["sudo ssh host"])
@pytest.mark.parametrize("prefix", PREFIXES)
def test_a_quoted_prefix_decides_at_least_as_its_plain_twin(prefix, hop, body, cat):
    _twin_check(f"{prefix} {hop} {body}", f"X=ab {hop} {body}", twin_policies(cat).values())


@pytest.mark.parametrize("body, cat", FIX_BODIES + LEAD_BODIES)
@pytest.mark.parametrize("hop", ["ssh host", "ssh user@host", "mosh host", "sudo ssh host", "env ssh host"])
@pytest.mark.parametrize("prefix", ["X='a b'", "X='a b' Y='c d'", 'X="$Y"'])
def test_a_quoted_prefix_decides_at_least_as_its_plain_twin_under_the_built_in_policies(prefix, hop, body, cat):
    _twin_check(f"{prefix} {hop} {body}", f"X=ab {hop} {body}", BUILTIN.values())


@pytest.mark.parametrize("body, cat", FIX_BODIES + LEAD_BODIES)
@pytest.mark.parametrize("form", DEFERRED)
@pytest.mark.parametrize("prefix", ["X='a b'", "X='a b' Y='c d'", 'X="$Y"'])
def test_a_part_read_later_decides_at_least_as_its_plain_twin(prefix, form, body, cat):
    line = form.format(body=body)
    _twin_check(f"{prefix} {line}", f"X=ab {line}", twin_policies(cat).values())
