import importlib.util
import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "skills/github-draft-review-html/scripts/draft_pipeline.py"
NAMES = "# seats and hosts\n.agents\n~/.agents/team-skills\nseat.alpha.\nalpha-box\n@bot\n"


def _load():
    spec = importlib.util.spec_from_file_location("draft_pipeline", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _idents():
    return _load().DEFAULT_IDENTS


@pytest.fixture
def gate(monkeypatch, tmp_path):
    """identifier_gate with the test names file loaded (names load at import time)."""
    names = tmp_path / "internal-names.txt"
    names.write_text(NAMES, encoding="utf-8")
    monkeypatch.setenv("EXO_INTERNAL_NAMES", str(names))
    return _load().identifier_gate


def test_abs_path_catches_mac_and_linux_homes():
    pat = re.compile(_idents()["abs-path"])
    for text in ("/Users/sam/x", "/home/sam/x", "/root/x"):
        assert pat.search(text), text
    assert not pat.search("the /home page")


@pytest.mark.parametrize("text,allowed,leak", [
    ("open /Users/someone/demo.txt and /Users/sam/.ssh/id_ed25519",
     "/Users/someone/demo.txt", "/Users/sam/.ssh/id_ed25519"),
    ("see /root/demo/run.sh and /root/.ssh/id_rsa", "/root/demo/run.sh", "/root/.ssh/id_rsa"),
    ("see /home/sam/project and /home/sam/private/notes", "/home/sam/project", "/home/sam/private/notes"),
    ("on localhost:8080 and localhost:9999/secret", "localhost:8080", "localhost:9999/secret"),
    ("/private/tmp/demo and /private/tmp/sam-secret", "/private/tmp/demo", "/private/tmp/sam-secret"),
    ("cc team-ops@example.com and ops@example.com", "team-ops@example.com", "ops@example.com"),
    ("run /srv/alpha-box-demo then ask alpha-box", "/srv/alpha-box-demo", "alpha-box"),
])
def test_allowed_literal_exempts_one_token_not_its_class(gate, text, allowed, leak):
    ok, hits = gate(text, [allowed])
    assert not ok
    assert leak in [t for found in hits.values() for t in found], hits
    assert allowed not in [t for found in hits.values() for t in found], hits


@pytest.mark.parametrize("text", [
    "open /Users/someone/demo.txt.",
    "open (/Users/someone/demo.txt) or `/Users/someone/demo.txt`, or **/Users/someone/demo.txt**",
    "see [the demo](/Users/someone/demo.txt).",
    "open /USERS/SOMEONE/DEMO.TXT now",
])
def test_allowed_literal_still_exempts_its_own_token(gate, text):
    assert gate(text, ["/Users/someone/demo.txt"]) == (True, {})


def test_hit_reports_the_whole_token(gate):
    ok, hits = gate("then cat /Users/sam/.ssh/id_ed25519; done", [])
    assert not ok and hits == {"abs-path": ["/Users/sam/.ssh/id_ed25519"]}


@pytest.mark.parametrize("name", [".agents", "~/.agents/team-skills", "@bot", "seat.alpha."])
@pytest.mark.parametrize("shape", ["{} is internal", "ask {}", "ask {} now", "(see {})"])
def test_internal_name_with_a_non_word_edge_is_caught(gate, name, shape):
    ok, hits = gate(shape.format(name), [])
    assert not ok and "internal-name" in hits, hits


def test_internal_names_report_the_full_name(gate):
    text = "Config in .agents now. See ~/.agents/team-skills too. Ask @bot. Ping seat.alpha. now. alpha-box runs"
    ok, hits = gate(text, [])
    assert not ok
    assert hits["internal-name"] == [".agents", "@bot", "alpha-box", "seat.alpha", "~/.agents/team-skills"]


def test_internal_names_stay_whole_names(gate):
    assert gate("myalpha-boxes alpha-box2 xseat.alpha.y my.agentsx", []) == (True, {})
    # a non-word edge needs no boundary, so a name inside a longer dotted token still counts
    assert not gate("seat.alpha.example and config.agents", [])[0]
