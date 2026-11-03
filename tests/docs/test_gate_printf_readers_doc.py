"""The texts on #1047 name the shells that read what printf or `echo -e` writes, and the openers whose readings read
it too, as the gate reads them (#1089 review follow-up).

#1047 made a shell that runs its stdin read what printf or `echo -e` pipes into it, with the escapes expanded. Its
approving reviews found that docs/GATE.md and the D-038 amendment named only `bash` or `sh` with no script, the
`sudo -s` shell and `ssh host` with no command, where the gate also reads `zsh`, `sudo -i`, `timeout 5 bash`,
`{ bash; }` and more. The two texts and the CHANGELOG entry also named only `!`, `then` and `{` before a `coproc`,
where #864's reading opens a compound after `else`, `elif`, `if`, `while`, `until` and `do` too, and right after
`coproc NAME {` and `function NAME {`, and that reading has a printed reading of its own.

Each list is taken from the gate where the gate keeps one: `_SHELLS`, and the reserved words in `_OPENERS` that
close no compound. So a list that grows in the code fails here until the texts name it. Each reader and opener a
text names is also run through the gate: the escaped line must decide as the same line written out does.
Classification and decision only: nothing here runs the lines.
"""
import re
from pathlib import Path

import pytest

from exo import gate

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".exo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("EXO_HOME", str(h / ".exo"))
    for k in ("EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL"):
        monkeypatch.delenv(k, raising=False)


# Each text: the file, the phrase that starts it, and where it ends (the first of these after the start).
READER_TEXTS = {
    "docs/GATE.md": ("docs/GATE.md", "Only a shell that runs its stdin reads what the command writes",
                     ("An interpreter and a SQL client",)),
    "DECISIONS.md D-038 amendment": ("DECISIONS.md", "A shell that runs its stdin also reads `out`",
                                     ("Interpreters and SQL clients",)),
}
OPENER_TEXTS = {
    "docs/GATE.md": ("docs/GATE.md", "The readings that open a compound", ("\n\n",)),
    "DECISIONS.md D-038 amendment": ("DECISIONS.md", "The readings that open a compound",
                                     ("Corrected 2026-11-03", "`decide` keeps the strictest of the two readings")),
    "CHANGELOG.md #1047 entry": ("CHANGELOG.md", "The readings that open a compound", ("\n\n",)),
}
# The texts that also name #853's prefixes. The CHANGELOG entry names `time` alone.
TIME_TEXTS = ("docs/GATE.md", "DECISIONS.md D-038 amendment")


def _text(path: str, opening: str, ends: tuple[str, ...]) -> str:
    text = (ROOT / path).read_text(encoding="utf-8")
    assert text.count(opening) == 1, f"{path}: {opening!r} should start one text"
    start = text.index(opening)
    found = [i for i in (text.find(e, start + len(opening)) for e in ends) if i != -1]
    assert found, f"{path}: the text that {opening!r} starts should end at one of {ends}"
    end = min(found)
    return " ".join(text[start:end].split())


def _spans(text: str) -> list[str]:
    return re.findall(r"`([^`]+)`", text)


def _decided(line: str) -> tuple[str, ...]:
    c = gate.classify({"tool": "bash", "args": {"command": line}})
    return (c.category, *(gate.decide(c, p).outcome for p in POLICIES))


# (category, default, deny: [store_write], deny: [delete], opaque_exec: allow), as in #1047's tests.
POLICIES = [gate.normalize_policies({}), gate.normalize_policies({"gates": {"deny": ["store_write"]}}),
            gate.normalize_policies({"gates": {"deny": ["delete"]}}),
            gate.normalize_policies({"gates": {"categories": {"opaque_exec": "allow"}}})]
DELETE = ("delete", "ask", "ask", "deny", "ask")
OPAQUE = ("opaque_exec", "ask", "ask", "ask", "allow")
ESCAPED, WRITTEN = r"r\x6d -rf /srv/data", "rm -rf /srv/data"


# ---- Readers ----

# A span in a reader text that names no reader: a code name, an option, `out`, or a whole example line.
def _readers(text: str) -> list[str]:
    return [s for s in _spans(text) if not (s.startswith("-") or "_" in s or s == "out" or "printf" in s)]


# A reader the texts name as not reading the output: a shell that ssh runs as its remote command.
NOT_READERS = {"ssh host bash"}
# The readers the reviews of #1089 found the texts left out (5726570654, 5732421140): `sudo -i`, a shell behind a
# prefix or wrapper, and a shell inside a compound the pipe feeds.
REVIEW_READERS = ["sudo -i", "timeout 5 bash", "nice bash", "env bash", "{ bash; }", "( bash )", "setsid bash",
                  "nohup bash", "command bash", "exec bash"]
NAMED_READERS = [(name, r) for name, spec in READER_TEXTS.items() for r in _readers(_text(*spec))]


@pytest.mark.parametrize("name", READER_TEXTS)
def test_the_text_names_every_shell_the_gate_reads_it_for(name):
    text = _text(*READER_TEXTS[name])
    missing = [s for s in sorted(gate._SHELLS) if f"`{s}`" not in text]
    assert not missing, f"{name} does not name {missing}, which the gate reads as shells (_SHELLS)"


@pytest.mark.parametrize("reader", REVIEW_READERS)
@pytest.mark.parametrize("name", READER_TEXTS)
def test_the_text_names_each_reader_the_reviews_found(name, reader):
    assert _decided(f"printf '{ESCAPED}' | {reader}") == _decided(f"printf '{WRITTEN}' | {reader}") == DELETE
    assert f"`{reader}`" in _text(*READER_TEXTS[name]), f"{name} does not name `{reader}`"


@pytest.mark.parametrize("name, reader", NAMED_READERS)
def test_each_reader_a_text_names_reads_the_output(name, reader):
    want = OPAQUE if reader in NOT_READERS else DELETE
    assert _decided(f"printf '{ESCAPED}' | {reader}") == _decided(f"printf '{WRITTEN}' | {reader}") == want


@pytest.mark.parametrize("name", READER_TEXTS)
def test_the_text_names_what_is_not_a_reader(name):
    assert set(_readers(_text(*READER_TEXTS[name]))) & NOT_READERS == NOT_READERS


# ---- Openers ----

# The reserved words after which bash takes a command as after `;`: the words of _OPENERS that close no compound.
# #864's reading opens a `coproc` or `function` compound behind each (_parse_line, `late`).
OPENERS = sorted(gate._OPENERS - set(gate._COMPOUND_CLOSERS))
# Each opener in a line bash accepts: what goes before the `coproc` and after its compound.
SHAPES = {"!": ("! ", ""), "then": ("if true; then ", "; fi"), "else": ("if false; then :; else ", "; fi"),
          "elif": ("if false; then :; elif ", "; then :; fi"), "if": ("if ", "; then :; fi"),
          "while": ("while ", "; do break; done"), "until": ("until ", "; do :; done"),
          "do": ("for j in 1; do ", "; done"), "{": ("{ ", "; }")}
# A loop whose second pass runs in the store (#479), and a heredoc after a group's closer that feeds the shell in it
# (#161). Main's reading leaves each the coproc's argv. Behind `while`, `until` and `do` the outer loop reads the
# store loop either way, so the heredoc row is the one that needs #864's reading there.
LOOP = "coproc for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done"
HEREDOC = "coproc W { bash\n } <<EOF\nrm -rf /srv/data\nEOF"
TIME_FORMS = ["time", "time -p", "time --", "time -p --"]
AFTER = ["coproc NAME {", "function NAME {"]


def _printed(written: str, escaped: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return _decided(f"printf '{escaped}' | bash"), _decided(f"printf '{written}' | bash")


def test_each_opener_has_a_shape():
    assert sorted(SHAPES) == OPENERS


@pytest.mark.parametrize("name", OPENER_TEXTS)
def test_the_text_names_every_opener_the_gate_opens_after(name):
    text = _text(*OPENER_TEXTS[name])
    missing = [w for w in OPENERS + AFTER if f"`{w}`" not in text]
    assert not missing, f"{name} does not name {missing}, after which #864's reading opens a compound"


@pytest.mark.parametrize("name", TIME_TEXTS)
def test_the_text_names_every_time_prefix(name):
    text = _text(*OPENER_TEXTS[name])
    assert not [f for f in TIME_FORMS if f"`{f}`" not in text]


@pytest.mark.parametrize("opener", sorted(SHAPES))
def test_the_output_opens_a_coproc_after_each_opener(opener):
    head, tail = SHAPES[opener]
    loop = head + LOOP + tail
    escaped, written = _printed(loop, loop.replace("coproc", r"\x63oproc", 1))
    assert escaped == written and written[2] == "deny"
    hd = head + HEREDOC + "\n" + tail.removeprefix("; ")
    assert _printed(hd, hd.replace("\n", r"\n")) == (DELETE, DELETE)


@pytest.mark.parametrize("form", TIME_FORMS)
def test_the_output_opens_a_coproc_behind_each_time_prefix(form):
    hd = f"{form} {HEREDOC}\n"
    assert _printed(hd, hd.replace("\n", r"\n")) == (DELETE, DELETE)


@pytest.mark.parametrize("head", ["coproc W {", "function f {"])
def test_the_output_opens_the_compound_right_after_a_named_group(head):
    loop = f"{head} for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done; }}"
    escaped, written = _printed(loop, loop.replace("{", r"\x7b", 1))
    assert escaped == written and written[2] == "deny"
