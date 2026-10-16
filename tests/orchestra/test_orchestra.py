"""Tests for the exo-orchestra plugin and the generic skill pack."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILLS = REPO / "skills"
PLUGIN = REPO / "plugins" / "exo-orchestra"
sys.path.insert(0, str(PLUGIN))

from exo import privacy  # noqa: E402
from exo_orchestra import orchestra  # noqa: E402

# The real names live in an untracked file (exo.privacy); this suite skips the name check without it.
DENY_RE = privacy.deny_regex(whole_word=True)
# Explicit allowlist: files that explain provenance may name the source seats.
ALLOWED = {PLUGIN / "PROVENANCE.md"}
TEXT_SUFFIXES = {".md", ".py", ".json", ".jsonl", ".yaml", ".yml", ".txt", ".sh", ".html"}


def manifest():
    return json.loads((SKILLS / "MANIFEST.json").read_text(encoding="utf-8"))


def test_manifest_lists_every_skill_and_each_entry_exists():
    doc = manifest()
    names = [s["name"] for s in doc["skills"]]
    assert len(names) == len(set(names))
    dirs = sorted(p.name for p in SKILLS.iterdir() if p.is_dir() and not p.name.startswith((".", "__")))
    assert sorted(names) == dirs
    for s in doc["skills"]:
        for key in ("name", "path", "description", "source", "generalized"):
            assert s.get(key), (s["name"], key)
        skill_md = SKILLS / s["path"] / "SKILL.md"
        assert skill_md.is_file()
        fm = orchestra.frontmatter(skill_md)
        assert fm.get("name") == s["name"]
        assert fm.get("description")
        for script in s.get("scripts", []):
            assert (SKILLS / s["path"] / script).is_file()


def test_seats_complete_and_model_free():
    for role in orchestra.ROLES:
        d = PLUGIN / "seats" / role
        soul = (d / "SOUL.md").read_text()
        for heading in ("## Owns", "## Does not own", "## Routing", "## Safety"):
            assert heading in soul, (role, heading)
        seat = orchestra.parse_seat(orchestra.render_seat_text(role))
        assert set(seat["models"]) == {"tier"}, role  # no model or provider named (D-018)
        assert orchestra.validate_seat(seat, SKILLS) == [], role
        assert seat["models"]["tier"] in orchestra.TIERS and seat["skills"]["load"], role


def test_render_seat_fills_placeholders(tmp_path):
    files = orchestra.render_seat("builder", tmp_path / "p", {"seat_name": "forge", "principal_store": "/s"})
    text = "".join(f.read_text() for f in files)
    assert "forge" in text and "/s" in text and "{{" not in text
    assert orchestra.load_seat(tmp_path / "p")["seat"]["name"] == "forge"
    with pytest.raises(FileExistsError):
        orchestra.render_seat("builder", tmp_path / "p")
    with pytest.raises(ValueError):
        orchestra.render_seat("intern", tmp_path / "q")
    with pytest.raises(ValueError):
        orchestra.render_seat("builder", tmp_path / "r", {"seat_name": 'x"\n[bad]'})


def test_plugin_register_and_command(tmp_path):
    calls = {"commands": {}, "skills": []}

    class Ctx:
        def __init__(self, settings):
            self.settings = settings

        def get_config(self, key, default=None):
            return self.settings.get(key, default)

        def register_command(self, name, handler, **kw):
            calls["commands"][name] = handler

        def register_skill(self, name, path, description="", frontmatter=None):
            assert Path(path).is_file()
            calls["skills"].append(name)

    orchestra.register(Ctx({"register_skills": False}))
    handle = calls["commands"]["orchestra"]
    assert "reviewer: tier deep" in handle("seats")
    assert "clean-writing" in handle("skills")
    out = handle(f"seat reviewer --seat qa-one --out {tmp_path / 'r'}")
    assert out.startswith("Wrote") and "qa-one" in (tmp_path / "r" / "seat.toml").read_text()
    assert "qa-one" in (tmp_path / "r" / "SOUL.md").read_text()
    assert handle(f"check {tmp_path / 'r'}") == "Seat is valid."
    assert calls["skills"] == []
    orchestra.register(Ctx({}))  # native skills are the default
    assert sorted(calls["skills"]) == sorted(s["name"] for s in manifest()["skills"])


def run_prose(*args, env=None):
    script = SKILLS / "clean-writing" / "scripts" / "check-prose.py"
    return subprocess.run([sys.executable, "-I", str(script), *args], capture_output=True, text=True,
                          env=env or {"PATH": "/usr/bin:/bin"})


def test_check_prose_gate(tmp_path):
    good = tmp_path / "good.md"
    good.write_text("The importer reads the file. It writes one row per record.\n")
    bad = tmp_path / "bad.md"
    bad.write_text("Notably, this is the right shape — trust me.\n")
    assert run_prose(str(good)).returncode == 0
    r = run_prose(str(bad))
    assert r.returncode == 1 and "em dash" in r.stdout and "right shape" in r.stdout
    assert run_prose().returncode == 3
    dup = tmp_path / "dup.md"
    dup.write_text("See #12. Also #12.\n")
    assert run_prose("--dupes", str(dup)).returncode == 1


def test_check_prose_loads_principal_policy(tmp_path):
    store = tmp_path / "store"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "internal-names.txt").write_text("# seats\nnorthstar\n")
    (store / "policies" / "prose.json").write_text(json.dumps({"banned_phrases": [r"\bsynergy\b"]}))
    text = tmp_path / "t.md"
    text.write_text("Ask northstar about synergy.\n")
    assert run_prose(str(text)).returncode == 0  # no store: generic rules only
    env = {"PATH": "/usr/bin:/bin", "EXO_PRINCIPAL_STORE": str(store)}
    r = run_prose(str(text), env=env)
    assert r.returncode == 1 and "northstar" in r.stdout and "synergy" in r.stdout


def test_sym_protocol_skill_suite_passes():
    r = subprocess.run([sys.executable, "-I", str(SKILLS / "sym-protocol" / "tests" / "run-tests.py")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def iter_text_files():
    for root in (SKILLS, PLUGIN):
        for p in root.rglob("*"):
            if p.is_file() and p.suffix in TEXT_SUFFIXES and "__pycache__" not in p.parts:
                yield p


def test_no_principal_names_outside_provenance_docs():
    if DENY_RE is None:
        pytest.skip("no privacy deny-list (EXO_PRIVACY_DENYLIST)")
    hits = []
    for p in iter_text_files():
        if p in ALLOWED:
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for m in DENY_RE.finditer(line):
                hits.append(f"{p.relative_to(REPO)}:{n}")
    assert not hits, "\n".join(hits[:50])


def test_deny_list_has_teeth(tmp_path):
    deny = privacy.deny_regex(["leo", "saga-x"], whole_word=True)
    assert deny.search("ask Leo to review")
    assert deny.search("seat saga-x posted")
    assert not deny.search("a leopard and a leotard")
    assert not deny.search("the leoprompt router")


def test_deny_list_catches_hyphenated_seat_names():
    assert privacy.deny_regex(["orda"], whole_word=True).search("route to protean-orda")
