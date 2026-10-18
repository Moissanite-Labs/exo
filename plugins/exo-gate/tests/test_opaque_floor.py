"""#178, D-038: only lowering opaque_exec turns off the indirection floor.

A principal who writes `categories: {opaque_exec: deny}` asks for more care with hidden code. On main
before the fix, naming opaque_exec at any level turned the floor off, so a command the gate reads inside
a wrapper fell back to its own category (a push logged). Naming it at ask or deny now keeps the floor;
naming it at log or allow lowers opaque code exactly as before. Strings here are data for the classifier.
"""
import pytest

from gate_fixtures import TEMPLATE  # noqa: F401  (puts src on sys.path)
from exo.gate import classify, decide, normalize_policies

WRAPPED = [
    "coproc git push origin main",
    "function f() { git push origin main; }; f",
    "bash -c 'git push origin main'",
    "eval git push origin main",
]

# Outcomes on main before the fix when the principal lowers opaque_exec. They must not change. coproc is
# opaque_exec on main and vcs_push once the coproc reading (#131) lands, so it accepts either outcome.
LOWERED = {
    "coproc git push origin main": {"allow": {"allow", "log"}, "log": {"log"}},
    "function f() { git push origin main; }; f": {"allow": {"log"}, "log": {"log"}},
    "bash -c 'git push origin main'": {"allow": {"log"}, "log": {"log"}},
    "eval git push origin main": {"allow": {"log"}, "log": {"log"}},
}

# Outcomes on main before the fix under a rule {match: {category: opaque_exec}, decision: allow}. The rule
# matches only a line whose own category is opaque_exec; a push read inside a wrapper is vcs_push and asks.
RULE_ALLOW = {
    "coproc git push origin main": {"allow", "ask"},
    "function f() { git push origin main; }; f": {"ask"},
    "bash -c 'git push origin main'": {"ask"},
    "eval git push origin main": {"ask"},
}


def sh(cmd):
    return ("bash", {"command": cmd})


def outcome(cmd, raw):
    return decide(classify(sh(cmd)), normalize_policies(raw)).outcome


def at_least_ask(out):
    return out in ("ask", "deny")


@pytest.mark.parametrize("level", ["ask", "deny"])
@pytest.mark.parametrize("cmd", WRAPPED)
def test_naming_opaque_at_ask_or_deny_keeps_the_floor(cmd, level):
    assert at_least_ask(outcome(cmd, {"gates": {"categories": {"opaque_exec": level}}})), (cmd, level)


@pytest.mark.parametrize("cmd", WRAPPED)
def test_naming_opaque_at_ask_keeps_the_floor_under_an_all_allow_matrix(cmd):
    allow_all = {rev: {exp: "allow" for exp in ("private", "internal", "public")}
                 for rev in ("reversible", "costly", "irreversible")}
    raw = {"gates": {"matrix": allow_all, "categories": {"opaque_exec": "ask"}}}
    assert at_least_ask(outcome(cmd, raw)), cmd


@pytest.mark.parametrize("level", ["allow", "log"])
@pytest.mark.parametrize("cmd", WRAPPED)
def test_lowering_opaque_still_lowers(cmd, level):
    assert outcome(cmd, {"gates": {"categories": {"opaque_exec": level}}}) in LOWERED[cmd][level]


@pytest.mark.parametrize("cmd", WRAPPED)
def test_opaque_rule_deny_keeps_the_floor_and_allow_keeps_main(cmd):
    def rule(decision):
        return {"gates": {"rules": [{"match": {"category": "opaque_exec"}, "decision": decision}]}}
    assert at_least_ask(outcome(cmd, rule("deny"))), cmd
    assert outcome(cmd, rule("allow")) in RULE_ALLOW[cmd], cmd


def test_an_opaque_rule_at_ask_keeps_the_floor_and_its_reason():
    # the rule half: a rule that names opaque_exec at ask keeps the floor, so the floor's reason is given too
    pol = normalize_policies({"gates": {"rules": [{"match": {"category": "opaque_exec"}, "decision": "ask"}]}})
    d = decide(classify(sh("$PROG x")), pol)
    assert d.outcome == "ask" and "cannot fully see" in d.reason, d.reason


def test_a_lowering_rule_does_not_lift_a_category_kept_at_ask():
    raw = {"gates": {"categories": {"opaque_exec": "ask"},
                     "rules": [{"match": {"category": "opaque_exec"}, "decision": "allow"}]}}
    assert at_least_ask(outcome("bash -c 'git push origin main'", raw))


def test_plain_opaque_line_follows_the_principal():
    assert outcome("$PROG x", {"gates": {"categories": {"opaque_exec": "deny"}}}) == "deny"
    assert outcome("$PROG x", {"gates": {"categories": {"opaque_exec": "ask"}}}) == "ask"
    assert outcome("$PROG x", {"gates": {"categories": {"opaque_exec": "allow"}}}) == "allow"
    assert outcome("$PROG x", {"gates": {"rules": [{"match": {"category": "opaque_exec"},
                                                    "decision": "deny"}]}}) == "deny"
