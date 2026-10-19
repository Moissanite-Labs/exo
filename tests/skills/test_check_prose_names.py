"""check-prose.py flags an internal name at any edge, not only between word characters."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "skills/clean-writing/scripts/check-prose.py"
NAMES = ["~/.agents/team-skills", ".agents", "@bot", "seat.alpha.", "alpha-box"]


@pytest.fixture
def prose(tmp_path):
    names = tmp_path / "internal-names.txt"
    names.write_text("# seats and hosts\n" + "\n".join(NAMES) + "\n", encoding="utf-8")
    policy = tmp_path / "prose.json"
    policy.write_text(json.dumps({"flag_mentions": False}), encoding="utf-8")  # names rule alone

    def run(text):
        draft = tmp_path / "draft.md"
        draft.write_text(text, encoding="utf-8")
        return subprocess.run([sys.executable, str(SCRIPT), "--policy", str(policy), "--names", str(names),
                               str(draft)], capture_output=True, text=True,
                              env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    return run


@pytest.mark.parametrize("name", ["~/.agents/team-skills", ".agents", "@bot", "seat.alpha."])
@pytest.mark.parametrize("shape", ["{} is internal", "ask {}", "ask {} now"])
def test_name_with_a_non_word_edge_is_flagged(prose, name, shape):
    r = prose(shape.format(name) + "\n")
    assert r.returncode == 1, r.stdout
    assert f"internal identifier '{name}'" in r.stdout


def test_every_name_is_reported_in_full(prose):
    r = prose("Config in .agents now. See ~/.agents/team-skills too. Ask @bot. Ping seat.alpha. now. "
              "alpha-box runs.\n")
    assert r.returncode == 1 and "5 finding(s)" in r.stdout, r.stdout
    for name in NAMES:
        assert f"internal identifier '{name}'" in r.stdout


def test_names_stay_whole(prose):
    r = prose("myalpha-boxes and alpha-box2 and xseat.alpha.y and my.agentsx\n")
    assert r.returncode == 0, r.stdout
