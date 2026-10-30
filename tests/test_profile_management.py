"""Tests for exo.profile_management.create_profile (P03, create-only).

Covers the documented guarantees: valid IDs and string fields only, fail-before-any-
allocation, collisions (every variant seats.load accepts, including dangling symlinks)
are refused and never overwritten, escaped unicode/newlines round-trip, mode 0700/0600,
no key files, and a symlinked seats directory is refused.
"""

import os
import stat
from pathlib import Path

import pytest
import tomllib

from exo import profile_management
from exo.core import seats
from exo.profile_management import ProfileError, create_profile


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


# --------------------------------------------------------------------------- create


def test_creates_seat_file_and_returns_seat(tmp_path):
    home = tmp_path / "home"
    seat = create_profile(home, "scout", role="fast", description="Runs ahead")
    target = home / "seats" / "scout.toml"
    assert target.is_file()
    assert seat.name == "scout"
    assert seat.role == "fast"
    assert seat.description == "Runs ahead"
    assert seat.path == str(target)
    # every supported field round-trips through the core loader
    loaded = seats.load(home, "scout")
    assert loaded.name == "scout"
    assert loaded.role == "fast"
    assert loaded.description == "Runs ahead"


def test_home_accepts_path_or_str(tmp_path):
    for home in (str(tmp_path / "s-home"), tmp_path / "p-home"):
        seat = create_profile(home, "alpha")
        assert seat.name == "alpha"
        assert (Path(home) / "seats" / "alpha.toml").is_file()


def test_empty_optional_fields_omitted_but_default(tmp_path):
    home = tmp_path / "home"
    create_profile(home, "quiet")
    text = (home / "seats" / "quiet.toml").read_text("utf-8")
    assert 'role = ""' not in text
    assert 'description = ""' not in text
    assert text.startswith("[seat]\nname = \"quiet\"\n")
    loaded = seats.load(home, "quiet")
    assert loaded.role == "" and loaded.description == ""


def test_creates_missing_dirs_with_mode_0700(tmp_path):
    home = tmp_path / "deep" / "home"
    create_profile(home, "scout")
    assert _mode(home) == 0o700
    assert _mode(home / "seats") == 0o700
    assert _mode(home / "seats" / "scout.toml") == 0o600


def test_existing_home_and_seats_dir_reused(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "seats").mkdir(mode=0o755)
    os.chmod(home, 0o755)
    create_profile(home, "scout")
    assert _mode(home) == 0o755  # untouched, not chmod'd
    assert _mode(home / "seats") == 0o755


def test_no_key_files_or_authority_created(tmp_path):
    home = tmp_path / "home"
    create_profile(home, "scout")
    assert not (home / "keys").exists()
    assert not (home / "keys").is_symlink()
    assert sorted(p.name for p in (home / "seats").iterdir()) == ["scout.toml"]


def test_returns_roundtrip_seat_with_path_and_no_partial_temps(tmp_path):
    home = tmp_path / "home"
    seat = create_profile(home, "scout", role="x", description="y")
    # no leftover temp files (dot-prefixed .toml.tmp-* are never published)
    names = [p.name for p in (home / "seats").iterdir()]
    assert names == ["scout.toml"]
    assert seat.path == str(home / "seats" / "scout.toml")


# ---------------------------------------------------------------------- collisions


@pytest.mark.parametrize("variant", ["toml", "json", "seat_toml", "seat_json", "dir"])
def test_collision_refused_and_file_unchanged(tmp_path, variant):
    home = tmp_path / "home"
    seats_dir = home / "seats"
    seats_dir.mkdir(parents=True)
    if variant == "toml":
        target = seats_dir / "scout.toml"
    elif variant == "json":
        target = seats_dir / "scout.json"
    elif variant == "seat_toml":
        target = seats_dir / "scout" / "seat.toml"
        target.parent.mkdir()
    elif variant == "seat_json":
        target = seats_dir / "scout" / "seat.json"
        target.parent.mkdir()
    else:
        target = seats_dir / "scout"
        target.mkdir()
    original = b"occupied entry: keep me"
    if variant != "dir":
        target.write_bytes(original)
    with pytest.raises(ProfileError, match="already exists"):
        create_profile(home, "scout")
    if variant != "dir":
        assert target.read_bytes() == original  # unchanged
    assert _mode(target) != 0  # still there


@pytest.mark.parametrize("variant", ["toml", "json", "seat_toml", "seat_json", "dir"])
def test_dangling_symlink_still_occupies_name(tmp_path, variant):
    home = tmp_path / "home"
    seats_dir = home / "seats"
    seats_dir.mkdir(parents=True)
    if variant == "toml":
        link = seats_dir / "scout.toml"
    elif variant == "json":
        link = seats_dir / "scout.json"
    elif variant == "seat_toml":
        link = seats_dir / "scout" / "seat.toml"
        link.parent.mkdir()
    elif variant == "seat_json":
        link = seats_dir / "scout" / "seat.json"
        link.parent.mkdir()
    else:
        link = seats_dir / "scout"
    link.symlink_to(seats_dir / "nowhere-target")
    with pytest.raises(ProfileError, match="already exists"):
        create_profile(home, "scout")
    assert link.is_symlink() and not link.exists()  # still dangling, untouched


def test_reserved_directory_name_collides(tmp_path):
    home = tmp_path / "home"
    (home / "seats" / "scout").mkdir(parents=True)
    with pytest.raises(ProfileError, match="already exists"):
        create_profile(home, "scout")


def test_seat_file_collides_with_directory_of_same_name(tmp_path):
    home = tmp_path / "home"
    (home / "seats" / "scout" / "seat.toml").parent.mkdir(parents=True)
    with pytest.raises(ProfileError, match="already exists"):
        create_profile(home, "scout")


# ---------------------------------------- invalid input fails before any allocation


@pytest.mark.parametrize("bad", [
    "Scout", "scout!", "sc out", "scout/evil", "", "scout\n", "scout\ud800", "-scout",
    "_scout", "scout.",
])
def test_invalid_name_refused_before_allocation(tmp_path, bad):
    home = tmp_path / "home"
    with pytest.raises(ProfileError):
        create_profile(home, bad)
    assert not home.exists()


@pytest.mark.parametrize("kwargs", [
    {"role": None}, {"description": 3}, {"role": ["x"]},
])
def test_non_string_fields_refused_before_allocation(tmp_path, kwargs):
    home = tmp_path / "home"
    with pytest.raises(ProfileError, match="must be a string"):
        create_profile(home, "scout", **kwargs)
    assert not home.exists()


def test_soul_kwarg_rejected_typeerror_before_allocation(tmp_path):
    home = tmp_path / "home"
    with pytest.raises(TypeError):
        create_profile(home, "scout", soul="deferred to a later P number")
    assert not home.exists()


@pytest.mark.parametrize("bad_name", [None, 7, 3.14, b"scout", object()])
def test_non_string_name_refused_before_allocation(tmp_path, bad_name):
    home = tmp_path / "home"
    with pytest.raises(ProfileError, match="name must be a string"):
        create_profile(home, bad_name)
    assert not home.exists()


def test_lone_surrogate_refused_before_allocation(tmp_path):
    home = tmp_path / "home"
    with pytest.raises(ProfileError, match="UTF-8"):
        create_profile(home, "scout", role="\ud800")
    assert not home.exists()


# ---------------------------------------------------------------- escaped unicode


@pytest.mark.parametrize("field,value", [
    ("role", 'a "quoted" role'),
    ("description", "back\\slash and 'single'"),
    ("role", "ctrl \x00 \x01 \x1f"),
    ("description", "DEL \x7f raw"),
    ("description", "日本語 🎉 émoji"),
    ("role", "trailing backslash \\"),
])
def test_escaped_unicode_and_newlines_roundtrip(tmp_path, field, value):
    home = tmp_path / "home"
    seat = create_profile(home, "scout", **{field: value})
    text = (home / "seats" / "scout.toml").read_text("utf-8")
    data = tomllib.loads(text)  # must parse as valid TOML (no raw DEL, etc.)
    assert data["seat"][field] == value
    assert getattr(seat, field) == value
    loaded = seats.load(home, "scout")
    assert getattr(loaded, field) == value


def test_quotes_backslashes_newlines_written_as_escapes(tmp_path):
    home = tmp_path / "home"
    create_profile(home, "scout", role='say "hi"', description="a\nb")
    text = (home / "seats" / "scout.toml").read_text("utf-8")
    assert 'role = "say \\"hi\\""' in text
    # a raw newline inside the value would make TOML invalid; verify the file still parses
    tomllib.loads(text)
    # the description string with a newline must not contain a raw newline inside quotes
    desc_line = [ln for ln in text.splitlines() if ln.startswith("description = ")][0]
    assert desc_line.endswith('"') and desc_line.count('"') == 2


def test_del_is_reescaped_not_raw(tmp_path):
    home = tmp_path / "home"
    create_profile(home, "scout", description="bad \x7f char")
    text = (home / "seats" / "scout.toml").read_text("utf-8")
    assert "\\u007f" in text
    assert "\x7f" not in text
    assert tomllib.loads(text)["seat"]["description"] == "bad \x7f char"


def test_unicode_written_raw_not_ascii_escaped(tmp_path):
    home = tmp_path / "home"
    create_profile(home, "scout", description="日本語")
    text = (home / "seats" / "scout.toml").read_text("utf-8")
    assert "日本語" in text
    assert "\\u65e5" not in text


def test_escaped_field_roundtrips_through_json_escaping_agreement(tmp_path):
    home = tmp_path / "home"
    value = 'q"\\\n\ttab\x00\x1f\x7f末'
    seat = create_profile(home, "scout", description=value)
    assert seat.description == value
    data = tomllib.loads((home / "seats" / "scout.toml").read_text("utf-8"))
    assert data["seat"]["description"] == value


# ------------------------------------------------------------- unsafe paths refused


def test_symlinked_seats_dir_refused(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    real = tmp_path / "real-seats"
    real.mkdir()
    (home / "seats").symlink_to(real, target_is_directory=True)
    with pytest.raises(ProfileError, match="symlink"):
        create_profile(home, "scout")
    assert not (real / "scout.toml").exists()  # never wrote through the link
    assert (home / "seats").is_symlink()  # untouched


def test_symlinked_home_is_followed_where_it_already_existed(tmp_path):
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    home = tmp_path / "home-link"
    home.symlink_to(real_home, target_is_directory=True)
    create_profile(home, "scout")
    assert (real_home / "seats" / "scout.toml").is_file()
    assert (home / "seats" / "scout.toml").is_file()  # via the link
    assert (home / "seats").is_symlink() is False


def test_seats_dir_is_a_regular_file_refused(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "seats").write_text("not a dir")
    with pytest.raises(ProfileError, match="not a directory"):
        create_profile(home, "scout")
    assert (home / "seats").read_text() == "not a dir"


def test_home_is_a_regular_file_refused(tmp_path):
    home = tmp_path / "home"
    home.write_text("not a dir")
    with pytest.raises(ProfileError):
        create_profile(home, "scout")


def test_home_is_a_symlink_to_a_file_refused(tmp_path):
    real = tmp_path / "real"
    real.write_text("x")
    home = tmp_path / "home-link"
    home.symlink_to(real)
    with pytest.raises(ProfileError):
        create_profile(home, "scout")


# -------------------------------------------- unsupported platform refused before allocation


def test_unsupported_platform_refuses_before_creating_fresh_home(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_management, "_DIR_FD_OK", False)
    home = tmp_path / "home"
    with pytest.raises(ProfileError, match="dir_fd"):
        create_profile(home, "scout")
    assert not home.exists()  # nothing allocated


def test_unsupported_platform_refuses_without_creating_seats_in_existing_home(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_management, "_DIR_FD_OK", False)
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(ProfileError, match="dir_fd"):
        create_profile(home, "scout")
    assert home.is_dir()
    assert not (home / "seats").exists()  # seats never created
