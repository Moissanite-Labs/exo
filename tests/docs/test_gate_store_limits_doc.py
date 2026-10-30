"""Two texts that name what the gate makes of a `sudo -s` line must read it as the gate does (#491 follow-up).

#718 made the gate read the text a line feeds a `sudo -s` or `sudo -i` shell. The store section's known limits in
docs/GATE.md still said that `sudo -s <<< 'cp /tmp/x ~/.exo/config.json'` and `sudo -s <<< 'rm -rf /srv/data'` are
a `read`, and D-074's limits in DECISIONS.md said the same of the first line and of the same line with `rm -rf`.
A stdin the gate cannot read still asks only as `opaque_exec`, not as a store write, and each text says so too.
The check takes each `sudo` line either text quotes with what it says the line is now, and asks the gate for that
line's category and for what a policy decides on it, so the text cannot go stale quietly again. Classification
only: nothing here runs the lines.
"""
import re
from pathlib import Path

import pytest

from exo import gate

ROOT = Path(__file__).resolve().parents[2]
# Each text: the file, a phrase that starts it, and where it ends (the first of these after the start).
TEXTS = {
    "docs/GATE.md store limits": (
        "docs/GATE.md", "Known limits. These are classification checks, not a filesystem sandbox", ("\n\n",)),
    "DECISIONS.md D-074 limits": (
        "DECISIONS.md", "Found while testing, not changed here: `sudo -s`", ("\n- **", "\n## ", "\n\n")),
}

# What a text calls a line, and the category that word names. A plural ("deletes") counts as the word.
WORDS = {"`read`": "read", "store write": "store_write", "`store_write`": "store_write", "delete": "delete",
         "`delete`": "delete", "`opaque_exec`": "opaque_exec"}
# A line in backquotes that holds a sudo: `sudo -s <<< '...'`, `echo '...' | sudo -s`.
SPAN = r"`[^`]*\bsudo\b[^`]*`"
# "`sudo ...` is a store write", "`sudo ...` and `sudo ...` are a `read`", "`sudo ...` is now a `read`",
# "`sudo ...` is `delete`", "`sudo ...`, `... | sudo -s` or `sudo ...` are deletes". A past tense ("was a `read`")
# is history and is not read. When a text's wording moves, test_the_text_quotes_a_sudo_claim fails first: reword
# CLAIM to the new sentence.
CLAIM = re.compile(rf"((?:{SPAN}(?:,? (?:and|or) |, )?)+) (?:is|are)(?: now| still)? (?:an? )?("
                   + "|".join(map(re.escape, sorted(WORDS, key=len, reverse=True))) + r")s?(?!\w)")

# What the texts say a policy decides on a line of each category. A store write denies under `deny: [store_write]`.
# A line the gate reads only as `opaque_exec` (a stdin it cannot read) asks by default and under
# `deny: [store_write]`, and runs under a policy that allows `opaque_exec`.
DEFAULT = gate.normalize_policies({})
DENY_STORE = gate.normalize_policies({"gates": {"deny": ["store_write"]}})
ALLOW_OPAQUE = gate.normalize_policies({"gates": {"categories": {"opaque_exec": "allow"}}})
OUTCOMES = {
    "store_write": [("deny: [store_write]", DENY_STORE, "deny")],
    "opaque_exec": [("the default policy", DEFAULT, "ask"), ("deny: [store_write]", DENY_STORE, "ask"),
                    ("opaque_exec: allow", ALLOW_OPAQUE, "allow")],
}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".exo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("EXO_HOME", str(h / ".exo"))


def _read_claims(para: str) -> list[tuple[str, str]]:
    return [(line, WORDS[word]) for group, word in CLAIM.findall(para) for line in re.findall(r"`([^`]+)`", group)]


def _claims(name: str) -> list[tuple[str, str]]:
    path, opening, ends = TEXTS[name]
    text = (ROOT / path).read_text(encoding="utf-8")
    start = text.index(opening)
    end = min(i for i in (text.find(e, start + len(opening)) for e in ends) if i != -1)
    return _read_claims(" ".join(text[start:end].split()))


def _classify(line: str) -> gate.Classification:
    return gate.classify({"tool": "bash", "args": {"command": line}})


@pytest.mark.parametrize("sentence, claims", [
    ("so `sudo -s <<< 'a'` is a store write.", [("sudo -s <<< 'a'", "store_write")]),
    ("`sudo -s <<< 'a'` and `sudo -i <<< 'b'` are a `read`.", [("sudo -s <<< 'a'", "read"),
                                                               ("sudo -i <<< 'b'", "read")]),
    ("`sudo -s <<< 'a'` is now a `read`", [("sudo -s <<< 'a'", "read")]),
    ("`sudo -s <<< 'a'` is still a delete", [("sudo -s <<< 'a'", "delete")]),
    ("`sudo -s <<< 'a'` is `delete`", [("sudo -s <<< 'a'", "delete")]),
    ("`sudo -s <<< 'a'` is `store_write`", [("sudo -s <<< 'a'", "store_write")]),
    ("`sudo -s <<< 'a'`, `echo b | sudo -s` or `sudo -i <<< 'c'` are deletes",
     [("sudo -s <<< 'a'", "delete"), ("echo b | sudo -s", "delete"), ("sudo -i <<< 'c'", "delete")]),
    ("`sudo -s < f` and `env -S 'sudo -s' <<< 'a'` are `opaque_exec`: `deny: [store_write]` asks",
     [("sudo -s < f", "opaque_exec"), ("env -S 'sudo -s' <<< 'a'", "opaque_exec")]),
    ("`sudo -s <<< 'a'` was a `read`, as was the same line with `rm -rf`", []),
    ("`sudo -s <<< 'a'` is a deleted line", []),
    ("`bash <<< 'a'` is a delete", []),
])
def test_the_claim_pattern_reads_each_phrasing(sentence, claims):
    assert _read_claims(sentence) == claims


@pytest.mark.parametrize("name", TEXTS)
def test_the_text_quotes_a_sudo_claim(name):
    assert _claims(name), f"{name} ({TEXTS[name][1]!r}) names no sudo line as the CLAIM pattern reads it"


@pytest.mark.parametrize("name", TEXTS)
def test_the_text_names_a_stdin_the_gate_cannot_read(name):
    assert any(category == "opaque_exec" for _, category in _claims(name)), \
        f"{name} names no sudo line the gate reads only as `opaque_exec`: say what an unread stdin does"


@pytest.mark.parametrize("name", TEXTS)
def test_each_sudo_line_the_text_names_is_what_it_says(name):
    for line, category in _claims(name):
        got = _classify(line).category
        assert got == category, f"{name} calls `{line}` {category}, the gate reads {got}"


@pytest.mark.parametrize("name", TEXTS)
def test_a_policy_decides_each_line_as_the_text_says(name):
    for line, category in _claims(name):
        c = _classify(line)
        for policy_name, policy, outcome in OUTCOMES.get(category, ()):
            got = gate.decide(c, policy).outcome
            assert got == outcome, f"{name}: `{line}` under {policy_name} is {got}, not {outcome}"
