"""A gh download or clone into the Exo store is a store write, not a read (#461).

gh release download, gh run download and gh attestation download write what they fetch into `-O`/`--output`
or `-D`/`--dir`, and gh repo clone and gh gist clone into their directory word. The gate read these commands
as reads on GitHub, so the store check never looked at the paths, and `gh release download -O
~/.exo/policies/gates.yaml --clobber` overwrote the gate policy itself, or `-O ~/.exo/update/allowed_signers`
the signed-update trust pin (#133), where `curl -o` and `git clone` on the same paths ask. Now the store check
reads their paths as writes, with the principal-only floor. `-O -` prints to stdout and stays a read, and so
does a path outside the store. Every command string here is data for exo.gate.classify/decide; none is run.
"""
import pytest

from exo import gate

POLICY = {"gates": {"categories": {"read": "allow"}}}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """The repro's temp home, with the store folders the rows write into."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for k in ("EXO_HOME", "EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_SEAT", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(k, raising=False)
    for name in ("state", "policies", "update"):
        (home / ".exo" / name).mkdir(parents=True)
    return home


def run(line, policy=POLICY):
    c = gate.classify({"tool": "bash", "args": {"command": line}})
    return c, gate.decide(c, policy)


def principal_only(c, d, line):
    """The floor: the principal alone, whatever approvals.yaml delegates."""
    assert (c.category, d.outcome, d.rule) == ("store_write", "ask", "floor"), (line, c, d)
    assert gate.approvers_for(c.category, {"default": ["seat:lead"], "categories": {"store_write": ["seat:lead"]}}) \
        == ["principal"], line


# The issue's rows, each read and allowed on main, beside the controls that already asked.
ROWS = [
    "gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml --clobber",
    "gh release download v1 -R pub/x -O ~/.exo/update/allowed_signers",
    "gh release download v1 -R pub/x -D ~/.exo/state --clobber",
    "gh run download 1 -R pub/x -D ~/.exo/state",
    "gh attestation download 1 -R pub/x -D ~/.exo/state",
    "gh repo clone pub/x ~/.exo/update",
    "gh gist clone abc ~/.exo/update",
    "curl -o ~/.exo/policies/gates.yaml https://x",          # control
    "git clone https://x ~/.exo/update",                     # control
]


@pytest.mark.parametrize("line", ROWS)
def test_a_download_or_clone_into_the_store_asks(line):
    principal_only(*run(line), line)


# Every spelling gh reads an output path in: a separated short or long one, one with `=`, a directory
# operand for a clone, and `--` before it.
SPELLINGS = [
    "gh release download v1 -R pub/x --output=~/.exo/policies/gates.yaml",
    "gh release download v1 -R pub/x --output ~/.exo/update/allowed_signers",
    "gh release download v1 -R pub/x --dir=~/.exo/state --clobber",
    "gh run download 1 -R pub/x --dir ~/.exo/state",
    "gh attestation download 1 -R pub/x --dir=~/.exo/state",
    "gh repo clone pub/x -- ~/.exo/update",
    "gh gist clone abc -- ~/.exo/update",
]


@pytest.mark.parametrize("line", SPELLINGS)
def test_every_spelling_of_the_output_path_asks(line):
    principal_only(*run(line), line)


# gh skips the options before a subcommand (#399), so a `-R` before it is not where the write hides.
BEFORE = [
    "gh -R pub/x release download v1 -O ~/.exo/policies/gates.yaml --clobber",
    "gh -R pub/x run download 1 -D ~/.exo/state",
    "gh -R pub/x attestation download 1 -D ~/.exo/state",
    "gh release -R pub/x download v1 -O ~/.exo/policies/gates.yaml --clobber",
    "gh run -R pub/x download 1 -D ~/.exo/state",
]


@pytest.mark.parametrize("line", BEFORE)
def test_a_repo_option_before_the_subcommand_asks(line):
    principal_only(*run(line), line)


# Wherever the gate reads the store write as a part it is the principal's alone (#447).
WRAPPERS = [
    "sudo gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml --clobber",
    "sudo gh repo clone pub/x ~/.exo/update",
    "bash -c 'gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml --clobber'",
    "bash -c 'gh gist clone abc ~/.exo/update'",
    "echo $(gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml --clobber)",
    "x=$(gh repo clone pub/x ~/.exo/update)",
]


@pytest.mark.parametrize("line", WRAPPERS)
def test_a_download_or_clone_behind_a_wrapper_asks(line):
    c, d = run(line)
    assert (c.category, d.outcome) == ("store_write", "ask"), (line, c, d)
    assert gate.approvers_for(c.category, {"default": ["seat:lead"], "categories": {"store_write": ["seat:lead"]}}) \
        == ["principal"], line


# `-O -` prints to stdout, and a path outside the store stays a read, as do gh's other reads.
READS = [
    "gh release download v1 -R pub/x -O -",
    "gh release download v1 -R pub/x --output=-",
    "gh release download v1 -R pub/x -O /tmp/gates.yaml",
    "gh run download 1 -R pub/x -D /tmp/state",
    "gh repo clone pub/x /tmp/work",
    "gh repo clone pub/x",
    "gh gist clone abc /tmp/gists",
    "gh release view v1 -R pub/x",
    "gh run view 1 -R pub/x",
]


@pytest.mark.parametrize("line", READS)
def test_stdout_and_a_path_outside_the_store_stay_reads(line):
    c, d = run(line)
    assert (c.category, d.outcome) == ("read", "allow"), (line, c, d)


def test_the_write_names_the_path_gh_downloads_to():
    c, _ = run("gh release download v1 -R pub/x -O ~/.exo/update/allowed_signers")
    assert (c.category, c.target) == ("store_write", "~/.exo/update/allowed_signers"), c
    c, _ = run("gh repo clone pub/x ~/.exo/state")
    assert (c.category, c.target) == ("store_write", "~/.exo/state"), c


# A path the gate cannot pin to a name counts as the store, as for every writer (#309).
def test_an_unread_spelling_of_the_output_path_asks():
    principal_only(*run("gh release download v1 -R pub/x -O ~/.exo/polic$(printf i)es/gates.yaml"),
                   "unread spelling")
