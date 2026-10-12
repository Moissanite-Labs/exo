import importlib.util
import re
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "skills/github-draft-review-html/scripts/draft_pipeline.py"


def _idents():
    spec = importlib.util.spec_from_file_location("draft_pipeline", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.DEFAULT_IDENTS


def test_abs_path_catches_mac_and_linux_homes():
    pat = re.compile(_idents()["abs-path"])
    for text in ("/Users/sam/x", "/home/sam/x", "/root/x"):
        assert pat.search(text), text
    assert not pat.search("the /home page")
