"""A pattern a decision runs that does not compile is refused when the policy loads (#466).

`normalize_policies` compiled nothing: a rule's `match.command`, a `command:` entry or a mapping's `command` in
`deny:` and `reserved:`, and a `posting.forbid` pattern only failed when decide reached it, as re.PatternError.
The exo-gate hook caught that and, with no required executor, ran a read tool and offered a fallback card whose
"always" answered every later failing call. The loader now raises PolicyError naming the file and the entry, and
the hook loads the defaults on it. A policy that decides fine decides identically; only broken patterns change.
"""
import re

import pytest

from exo import gate


@pytest.mark.parametrize("raw, where", [
    ({"gates": {"rules": [{"match": {"command": "^("}, "decision": "allow"}]}}, "gates.rules[0].command"),
    ({"gates": {"rules": [{"command": "^(", "decision": "deny", "name": "flat"}]}}, "gates.rules[0].command"),
    ({"gates": {"rules": [{"match": {"command": ["^ls", "^("]}, "decision": "allow"}]}}, "gates.rules[0].command"),
    ({"gates": {"deny": ["command:^(rm|shred"]}}, "gates.deny[0]"),
    ({"gates": {"deny": [{"category": "local_write", "command": "^(rm|shred"}]}}, "gates.deny[0].command"),
    ({"gates": {"deny": [{"match": {"command": "command:^("}}]}}, "gates.deny[0].command"),
    ({"gates": {"reserved": ["command:^("]}}, "gates.reserved[0]"),
    ({"gates": {"reserved": [{"category": "shell", "command": "^("}]}}, "gates.reserved[0].command"),
    ({"posting": {"forbid": [{"pattern": "(?i)the cto (said"}]}}, "posting.forbid[0]"),
    ({"posting": {"forbid": ["ok literal", {"pattern": "["}]}}, "posting.forbid[1]"),
])
def test_a_pattern_that_does_not_compile_is_refused_naming_the_entry(raw, where):
    with pytest.raises(gate.PolicyError, match=re.escape(where) + r": bad pattern"):
        gate.normalize_policies(raw)


def test_the_error_names_the_pattern_and_why():
    with pytest.raises(gate.PolicyError,
                       match=r"gates.deny\[0\]: bad pattern '\^\(rm\|shred': missing \), unterminated"):
        gate.normalize_policies({"gates": {"deny": ["command:^(rm|shred"]}})


@pytest.mark.parametrize("raw", [
    {"gates": {"deny": ["command:^(rm|shred)"], "reserved": [{"category": "shell", "command": "\\brm\\b"}]}},
    {"gates": {"rules": [{"match": {"command": ["^ls", "^git status"]}, "decision": "allow"}]}},
    {"gates": {"rules": [{"command": "^git", "decision": "allow", "name": "git"}]}},
    {"gates": {"deny": ["delete", "tool:terminal", "target:*/.ssh/*", "prose is ignored"]}},
    {"posting": {"forbid": [{"pattern": r"(?i)the cto (said|commented)\b", "label": "internal direction"},
                            "password"]}},
    {"posting": {"forbid": [{"pattern": "secret", "ignore_case": True}]}},
    {},
])
def test_valid_variants_are_accepted(raw):
    errors: list[str] = []
    assert gate.normalize_policies(raw, errors) == gate.normalize_policies(raw)
    assert errors == []


# The issue's repro rows, and the calls its tests ask for: a store write, a force push and a post. Main's
# decisions under the fixed patterns (probe on 6054da56); a broken pattern never reaches decide now.
CALLS = [
    ("write_file", {"path": "notes.md", "content": "x"}),
    ("terminal", {"command": "echo x > ~/.exo/policies/gates.yaml"}),
    ("terminal", {"command": "git push --force origin main"}),
    ("terminal", {"command": "gh pr comment 3 --body hi"}),
]

MAIN_DENY_GOOD = {   # main under `deny: [{category: local_write, command: '^(rm|shred)'}]`
    ("write_file", "notes.md"): "allow",
    ("terminal", "echo x > ~/.exo/policies/gates.yaml"): "ask",
    ("terminal", "git push --force origin main"): "ask",
    ("terminal", "gh pr comment 3 --body hi"): "ask",
}


@pytest.mark.parametrize("tool, args", CALLS, ids=lambda a: str(a)[:40])
def test_a_good_policy_decides_the_repro_calls_as_main_did(tool, args):
    pol = gate.normalize_policies({"gates": {"deny": [{"category": "local_write", "command": "^(rm|shred)"}]}})
    d = gate.decide(gate.classify({"tool": tool, "args": args}), pol)
    assert d.outcome == MAIN_DENY_GOOD[(tool, args.get("path") or args.get("command"))], d
    if d.outcome == "deny":
        assert d.reason.startswith("denied by policy"), d
    elif (tool, args.get("path") or args.get("command")) == ("terminal", "echo x > ~/.exo/policies/gates.yaml"):
        assert d.rule == "floor" and "only the principal approves" in d.reason, d


@pytest.mark.parametrize("tool, args", CALLS, ids=lambda a: str(a)[:40])
def test_a_bad_pattern_in_any_entry_kind_refuses_the_policy(tool, args):
    for raw in ({"gates": {"deny": [{"category": "local_write", "command": "^(rm|shred"}]}},
                {"gates": {"deny": ["command:^(rm|shred"]}},
                {"gates": {"reserved": ["command:^("]}},
                {"gates": {"reserved": [{"category": "shell", "command": "^("}]}},
                {"gates": {"rules": [{"match": {"command": "^(git"}, "decision": "allow"}]}},
                {"posting": {"forbid": [{"pattern": "(?i)the cto (said"}]}}):
        with pytest.raises(gate.PolicyError):
            pol = gate.normalize_policies(raw)
            gate.decide(gate.classify({"tool": tool, "args": args}), pol)


# --- policy files -------------------------------------------------------------------------------

def _store(tmp_path, files):
    root = tmp_path / "store"
    (root / "policies").mkdir(parents=True)
    (root / "policies" / "internal-names.txt").write_text("zebrafinch\n")
    for name, text in files.items():
        (root / "policies" / name).write_text(text)
    return root


@pytest.mark.parametrize("files, file_name, where", [
    ({"gates.yaml": "deny:\n  - {category: local_write, command: '^(rm|shred'}\n"}, "gates.yaml", "gates.deny[0].command"),
    ({"gates.yaml": "rules:\n  - {match: {command: '^(git'}, decision: allow}\n"}, "gates.yaml", "gates.rules[0].command"),
    ({"gates.yaml": "deny: ['command:^(rm|shred']\n"}, "gates.yaml", "gates.deny[0]: bad pattern"),
    ({"gates.yaml": "reserved: ['command:^(']\n"}, "gates.yaml", "gates.reserved[0]: bad pattern"),
    ({"gates.yaml": "deny:\n  - match: {command: '^('}\n    reason: no\n"}, "gates.yaml", "gates.deny[0].command"),
    ({"posting.yaml": "forbid:\n  - {pattern: '(?i)the cto (said'}\n"}, "posting.yaml", "posting.forbid[0]: bad pattern"),
    ({"posting.md": "```yaml\nposting:\n  forbid:\n    - {pattern: \"(?i)the cto (said\"}\n```\n"},
     "posting.md", "posting.forbid[0]: bad pattern"),
])
def test_a_store_file_with_a_pattern_that_does_not_compile_is_refused_naming_the_file(tmp_path, files, file_name,
                                                                                      where):
    with pytest.raises(gate.PolicyError, match=re.escape(f"/policies/{file_name}") + r".*" + re.escape(where)):
        gate.load_policies(_store(tmp_path, files))


def test_a_store_with_good_patterns_loads_and_decides_as_before(tmp_path):
    root = _store(tmp_path, {
        "gates.yaml": "deny:\n  - {category: local_write, command: '^(rm|shred)'}\n"
                      "reserved:\n  - 'command:\\brm\\b'\n"
                      "rules:\n  - {match: {command: '^git'}, decision: allow, name: git}\n",
        "posting.yaml": "forbid:\n  - {pattern: '(?i)the cto (said|commented)\\b', label: internal direction}\n",
    })
    pol = gate.load_policies(root)
    d = gate.decide(gate.classify({"tool": "terminal", "args": {"command": "echo x > ~/.exo/policies/gates.yaml"}}),
                    pol)
    assert d.outcome == "ask" and d.rule == "floor", d
    d = gate.decide(gate.classify({"tool": "terminal", "args": {"command": "git push --force origin main"}}), pol)
    assert d.outcome == "allow" and d.rule == "rule", d
    d = gate.decide(gate.classify(
        {"tool": "terminal", "args": {"command": "gh pr comment 3 --body 'the CTO said ship it'"}}), pol)
    assert d.outcome == "ask" and "forbidden: internal direction" in d.violations, d
    d = gate.decide(gate.classify({"tool": "terminal", "args": {"command": "rm -rf build"}}), pol)
    assert d.outcome == "ask" and d.reserved, d
