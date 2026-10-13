"""The version is one number: pyproject, the package, the changelog and `exo --version` agree (docs/VERSIONING.md)."""
import re
import tomllib
from pathlib import Path

import exo
from exo import cli

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def test_version_prints_the_codename_form(capsys):
    assert cli.main(["--version"]) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("exo 0.2.0 (egg)")


def test_codenames_follow_the_major():
    assert [exo.codename(f"{m}.0.0") for m in range(7)] == ["egg", "cell", "tile", "cube", "frame", "5D", "6D"]
    assert exo.display_version("1.4.2") == "1.4.2 (cell)"


def test_package_version_matches_pyproject():
    assert exo.__version__ == _pyproject_version()
    tui = tomllib.loads((ROOT / "clients/tui/pyproject.toml").read_text(encoding="utf-8"))
    assert tui["project"]["version"] == _pyproject_version()


def test_changelog_latest_release_is_the_pyproject_version():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    exo_part = text.split("\n## Site", 1)[0]
    released = re.findall(r"^## \[(\d+\.\d+\.\d+(?:-rc\.\d+)?)\]", exo_part, re.M)
    assert released, "CHANGELOG.md has no released version heading"
    assert released[0] == _pyproject_version()
    assert re.search(r"^## \[Unreleased\]", exo_part, re.M)
    assert exo_part.index("[Unreleased]") < exo_part.index(f"[{released[0]}]")
