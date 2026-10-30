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


# ---------------- create fault injection: the temporary is cleaned up on any failure


def test_create_temp_cleaned_up_when_write_fails(tmp_path, monkeypatch):
    home = tmp_path / "home"

    def boom(_fd, _data):
        raise OSError("disk full")

    monkeypatch.setattr(profile_management, "_write_all", boom)
    with pytest.raises(OSError, match="disk full"):
        create_profile(home, "scout", role="fast")
    seats_dir = home / "seats"
    assert seats_dir.is_dir()
    assert list(seats_dir.iterdir()) == []  # no temp, no published seat


def test_create_temp_cleaned_up_when_fsync_fails(tmp_path, monkeypatch):
    home = tmp_path / "home"

    def boom(_fd):
        raise OSError("fsync failed")

    monkeypatch.setattr(profile_management.os, "fsync", boom)
    with pytest.raises(OSError, match="fsync failed"):
        create_profile(home, "scout", role="fast")
    seats_dir = home / "seats"
    assert seats_dir.is_dir()
    assert list(seats_dir.iterdir()) == []


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


# ------------------------------------------------------------- P03b: bounded update

import hashlib  # noqa: E402

from exo.profile_management import ProfileConflict, profile_digest, update_profile  # noqa: E402


def _home_with(tmp_path, **kwargs):
    """A home holding one created profile plus the digest an update must present."""
    home = tmp_path / "home"
    create_profile(home, "scout", role="fast", description="Runs ahead", **kwargs)
    return home, profile_digest(home, "scout")


def _seat_bytes(home):
    return (home / "seats" / "scout.toml").read_bytes()


def _snapshot(home):
    """Every entry in the seats dir with its bytes and mode, for untouched-on-refusal checks.

    The permanent ``.<name>.lock`` metadata file is deliberately excluded (a refused update may
    create it, which is documented); every other entry — including temps — must be unchanged.
    """
    out = {}
    for p in sorted((home / "seats").iterdir()):
        if p.name.endswith(".lock"):
            continue
        info = os.lstat(p)
        out[p.name] = (p.read_bytes() if stat.S_ISREG(info.st_mode) else None,
                       stat.S_IMODE(info.st_mode), stat.S_ISLNK(info.st_mode))
    return out


def test_digest_is_sha256_of_exact_bytes(tmp_path):
    home, digest = _home_with(tmp_path)
    assert digest == "sha256:" + hashlib.sha256(_seat_bytes(home)).hexdigest()
    assert digest.startswith("sha256:") and len(digest) == len("sha256:") + 64
    assert digest == profile_digest(home, "scout")  # stable read
    assert profile_digest(str(home), "scout") == digest  # accepts str home


def test_digest_creates_no_lock_file_or_dirs(tmp_path):
    home, _digest = _home_with(tmp_path)
    before = _snapshot(home)
    profile_digest(home, "scout")
    assert _snapshot(home) == before
    assert not (home / "seats" / ".scout.lock").exists()  # reads never allocate the lock


def test_update_role_and_description_returns_seat(tmp_path):
    home, digest = _home_with(tmp_path)
    seat = update_profile(home, "scout", expected_digest=digest, role="deep", description="Plans")
    assert (seat.name, seat.role, seat.description) == ("scout", "deep", "Plans")
    assert seat.path == str(home / "seats" / "scout.toml")
    loaded = seats.load(home, "scout")
    assert (loaded.role, loaded.description) == ("deep", "Plans")


def test_update_one_field_retains_the_other(tmp_path):
    home, digest = _home_with(tmp_path)
    seat = update_profile(home, "scout", expected_digest=digest, role="deep")
    assert (seat.role, seat.description) == ("deep", "Runs ahead")


def test_update_clears_a_field_with_empty_string(tmp_path):
    home, digest = _home_with(tmp_path)
    seat = update_profile(home, "scout", expected_digest=digest, description="")
    assert seat.description == ""
    assert 'description = ""' not in _seat_bytes(home).decode("utf-8")  # omitted, loader defaults it
    assert seats.load(home, "scout").description == ""


def test_update_new_digest_differs_and_chains(tmp_path):
    home, first = _home_with(tmp_path)
    update_profile(home, "scout", expected_digest=first, role="deep")
    second = profile_digest(home, "scout")
    assert second != first
    with pytest.raises(ProfileConflict):  # the old fingerprint is now stale
        update_profile(home, "scout", expected_digest=first, role="other")
    update_profile(home, "scout", expected_digest=second, role="standard")
    assert seats.load(home, "scout").role == "standard"


def test_update_preserves_file_mode(tmp_path):
    home, digest = _home_with(tmp_path)
    os.chmod(home / "seats" / "scout.toml", 0o640)
    update_profile(home, "scout", expected_digest=digest, role="deep")
    assert _mode(home / "seats" / "scout.toml") == 0o640


def test_update_leaves_no_temporary_files(tmp_path):
    home, digest = _home_with(tmp_path)
    update_profile(home, "scout", expected_digest=digest, role="deep")
    names = sorted(p.name for p in (home / "seats").iterdir())
    assert names == [".scout.lock", "scout.toml"]  # only the permanent lock
    assert [n for n in names if n.startswith(".") and n.endswith(".lock")] == [".scout.lock"]
    assert not any(".tmp-" in n for n in names)
    assert _mode(home / "seats" / ".scout.lock") == 0o600


def test_update_idempotent_rewrite_of_same_values(tmp_path):
    home, digest = _home_with(tmp_path)
    before = _snapshot(home)
    seat = update_profile(home, "scout", expected_digest=digest, role="fast", description="Runs ahead")
    assert (seat.role, seat.description) == ("fast", "Runs ahead")
    after = _snapshot(home)
    assert after["scout.toml"] == before["scout.toml"]  # bytes and mode untouched


def test_update_roundtrips_escapes_through_loader(tmp_path):
    home, digest = _home_with(tmp_path)
    value = 'q"\\\n\t\x00\x1f\x7f末'
    seat = update_profile(home, "scout", expected_digest=digest, description=value)
    assert seat.description == value
    assert seats.load(home, "scout").description == value
    assert tomllib.loads(_seat_bytes(home).decode("utf-8"))["seat"]["description"] == value


def test_update_requires_expected_digest(tmp_path):
    home, _digest = _home_with(tmp_path)
    before = _snapshot(home)
    with pytest.raises(TypeError):
        update_profile(home, "scout", role="deep")  # keyword is required
    with pytest.raises(ProfileError, match="expected_digest is required"):
        update_profile(home, "scout", expected_digest=None, role="deep")
    assert _snapshot(home) == before


@pytest.mark.parametrize("bad", [
    "", "sha256:", "sha256:xyz", "md5:" + "0" * 32, "sha256:" + "A" * 64,
    "sha256:" + "0" * 63, "sha256:" + "0" * 65, " " + "sha256:" + "0" * 64, 7, b"sha256:" + b"0" * 64,
])
def test_bad_expected_digest_refused_before_allocation(tmp_path, bad):
    home, _digest = _home_with(tmp_path)
    before = _snapshot(home)
    with pytest.raises(ProfileError, match="expected_digest"):
        update_profile(home, "scout", expected_digest=bad, role="deep")
    assert _snapshot(home) == before  # nothing created, nothing rewritten


def test_stale_digest_refused_and_file_untouched(tmp_path):
    home, digest = _home_with(tmp_path)
    stale = "sha256:" + "0" * 64
    before = _snapshot(home)
    with pytest.raises(ProfileConflict, match="changed since expected_digest"):
        update_profile(home, "scout", expected_digest=stale, role="deep")
    assert _snapshot(home) == before


def test_no_field_to_change_refused_before_allocation(tmp_path):
    home, digest = _home_with(tmp_path)
    before = _snapshot(home)
    with pytest.raises(ProfileError, match="at least one field"):
        update_profile(home, "scout", expected_digest=digest)
    with pytest.raises(ProfileError, match="at least one field"):
        update_profile(home, "scout", expected_digest=digest, role=None, description=None)
    assert _snapshot(home) == before


@pytest.mark.parametrize("kwargs", [{"role": 3}, {"description": ["x"]}, {"role": b"x"}])
def test_non_string_update_field_refused_before_allocation(tmp_path, kwargs):
    home, digest = _home_with(tmp_path)
    before = _snapshot(home)
    with pytest.raises(ProfileError, match="must be a string"):
        update_profile(home, "scout", expected_digest=digest, **kwargs)
    assert _snapshot(home) == before


@pytest.mark.parametrize("bad_name", [None, 7, "Scout", "scout!", "scout/evil", "", "scout\n", "-scout"])
def test_update_invalid_name_refused_before_allocation(tmp_path, bad_name):
    home, digest = _home_with(tmp_path)
    before = _snapshot(home)
    with pytest.raises(ProfileError):
        update_profile(home, bad_name, expected_digest=digest, role="deep")
    assert _snapshot(home) == before


def test_update_lone_surrogate_refused_and_file_untouched(tmp_path):
    home, digest = _home_with(tmp_path)
    before = _snapshot(home)
    with pytest.raises(ProfileError, match="UTF-8"):
        update_profile(home, "scout", expected_digest=digest, role="\ud800")
    assert _snapshot(home) == before


def test_update_missing_home_allocates_nothing(tmp_path):
    home = tmp_path / "absent"
    with pytest.raises(ProfileError, match="does not exist"):
        update_profile(home, "scout", expected_digest="sha256:" + "0" * 64, role="deep")
    assert not home.exists()


def test_update_missing_seat_in_existing_home_allocates_no_seat(tmp_path):
    home = tmp_path / "home"
    (home / "seats").mkdir(parents=True)
    with pytest.raises(ProfileError, match="no profile"):
        update_profile(home, "scout", expected_digest="sha256:" + "0" * 64, role="deep")
    assert sorted(p.name for p in (home / "seats").iterdir()) == [".scout.lock"]  # metadata only


def test_digest_of_missing_seat_refused_without_allocation(tmp_path):
    home = tmp_path / "home"
    (home / "seats").mkdir(parents=True)
    with pytest.raises(ProfileError, match="no profile"):
        profile_digest(home, "scout")
    assert list((home / "seats").iterdir()) == []


# --------------------------------------------------- update is canonical-form only


def _write_seat(home, raw):
    path = home / "seats" / "scout.toml"
    path.write_text(raw, encoding="utf-8")
    return path


@pytest.mark.parametrize("raw", [
    '# a comment\n[seat]\nname = "scout"\nrole = "fast"\n',
    '[seat]\nname = "scout"\nrole = "fast"\ndescription = "d"\n[models]\ntier = "fast"\n',
    '[seat]\nname = "scout"\nextra = "x"\nrole = "fast"\ndescription = "d"\n',
    '[seat]\nname = "scout"\ndescription = "d"\nrole = "fast"\n',
    '[seat]\nname = "scout"\nrole = "fast"\ndescription = "d"\n\n',
    '[seat]\nrole = "fast"\ndescription = "d"\n',
    '[seat]\nname = "other"\nrole = "fast"\ndescription = "d"\n',
    '[seat]\nname = "scout"\nrole = 3\ndescription = "d"\n',
    'name = "scout"\nrole = "fast"\ndescription = "d"\n',
    '{"seat": {"name": "scout", "role": "fast", "description": "d"}}',
    'not toml at all {{{',
])
def test_non_canonical_file_refused_untouched(tmp_path, raw):
    home = tmp_path / "home"
    (home / "seats").mkdir(parents=True)
    path = _write_seat(home, raw)
    original = path.read_bytes()
    with pytest.raises(ProfileError):
        profile_digest(home, "scout")
    with pytest.raises(ProfileError):
        update_profile(home, "scout", expected_digest="sha256:" + "0" * 64, role="deep")
    assert path.read_bytes() == original  # never rewritten


def test_canonical_file_digest_accepted(tmp_path):
    home, digest = _home_with(tmp_path)
    update_profile(home, "scout", expected_digest=digest, description="d2")
    assert profile_digest(home, "scout") == "sha256:" + hashlib.sha256(_seat_bytes(home)).hexdigest()


# ------------------------------------------ alternate forms and symlinks are refused


@pytest.mark.parametrize("variant", ["json", "dir", "seat_toml", "seat_json"])
def test_alternate_form_beside_canonical_refused(tmp_path, variant):
    home, digest = _home_with(tmp_path)
    seats_dir = home / "seats"
    if variant == "json":
        extra = seats_dir / "scout.json"
        extra.write_text("{}")
    elif variant == "dir":
        extra = seats_dir / "scout"
        extra.mkdir()
    else:
        extra = seats_dir / "scout" / ("seat.toml" if variant == "seat_toml" else "seat.json")
        extra.parent.mkdir()
        extra.write_text("{}")
    before = _snapshot(home)
    with pytest.raises(ProfileError, match="alternate form"):
        update_profile(home, "scout", expected_digest=digest, role="deep")
    assert _snapshot(home) == before
    assert 'role = "fast"' in _seat_bytes(home).decode("utf-8")  # never rewritten


def test_noop_update_with_alternate_form_refused_untouched(tmp_path):
    """An ambiguous name is refused even when the requested update is a no-op (same values)."""
    home, digest = _home_with(tmp_path)
    (home / "seats" / "scout.json").write_text("{}")
    before = _snapshot(home)
    with pytest.raises(ProfileError, match="alternate form"):
        update_profile(home, "scout", expected_digest=digest,
                       role="fast", description="Runs ahead")
    assert _snapshot(home) == before
    assert 'role = "fast"' in _seat_bytes(home).decode("utf-8")


@pytest.mark.parametrize("variant", ["json", "dir", "seat_toml", "seat_json"])
def test_alternate_form_only_has_no_digest(tmp_path, variant):
    home = tmp_path / "home"
    seats_dir = home / "seats"
    seats_dir.mkdir(parents=True)
    if variant == "json":
        target = seats_dir / "scout.json"
    elif variant == "dir":
        target = seats_dir / "scout"
        target.mkdir()
    else:
        target = seats_dir / "scout" / ("seat.toml" if variant == "seat_toml" else "seat.json")
        target.parent.mkdir()
    if not target.is_dir():
        target.write_text('{"seat": {"name": "scout"}}')
    with pytest.raises(ProfileError):
        profile_digest(home, "scout")


def test_symlinked_seat_file_refused(tmp_path):
    home, _digest = _home_with(tmp_path)
    real = tmp_path / "real.toml"
    real.write_bytes(_seat_bytes(home))
    target = home / "seats" / "scout.toml"
    target.unlink()
    target.symlink_to(real)
    target_digest = "sha256:" + hashlib.sha256(real.read_bytes()).hexdigest()
    with pytest.raises(ProfileError):  # never followed, no digest through a link
        profile_digest(home, "scout")
    with pytest.raises(ProfileError):
        update_profile(home, "scout", expected_digest=target_digest, role="deep")
    assert real.read_bytes() == target.read_bytes()  # the link target is untouched
    assert target.is_symlink()


def test_symlinked_lock_refused(tmp_path):
    home, digest = _home_with(tmp_path)
    elsewhere = tmp_path / "elsewhere.lock"
    elsewhere.write_text("")
    (home / "seats" / ".scout.lock").symlink_to(elsewhere)
    before = _seat_bytes(home)
    with pytest.raises(ProfileError, match="lock"):
        update_profile(home, "scout", expected_digest=digest, role="deep")
    assert _seat_bytes(home) == before
    assert elsewhere.read_text() == ""  # the symlink was never followed


def test_lock_held_by_another_process_refused_fast(tmp_path):
    home, digest = _home_with(tmp_path)
    lock = home / "seats" / ".scout.lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        import fcntl as _fcntl
        _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        before = _seat_bytes(home)
        with pytest.raises(ProfileConflict, match="locked"):
            update_profile(home, "scout", expected_digest=digest, role="deep")
        assert _seat_bytes(home) == before
        assert not any(".tmp-" in p.name for p in (home / "seats").iterdir())
    finally:
        os.close(fd)
    # once the flock is released the update proceeds
    update_profile(home, "scout", expected_digest=digest, role="deep")
    assert seats.load(home, "scout").role == "deep"


def test_lock_is_owner_regular_and_0600(tmp_path):
    home, digest = _home_with(tmp_path)
    update_profile(home, "scout", expected_digest=digest, role="deep")
    lock = home / "seats" / ".scout.lock"
    info = os.lstat(lock)
    assert stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
    assert stat.S_IMODE(info.st_mode) == 0o600


def test_update_symlinked_seats_dir_refused(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    real = tmp_path / "real-seats"
    real.mkdir()
    (real / "scout.toml").write_text('[seat]\nname = "scout"\n')
    (home / "seats").symlink_to(real, target_is_directory=True)
    with pytest.raises(ProfileError):
        update_profile(home, "scout", expected_digest="sha256:" + "0" * 64, role="deep")
    with pytest.raises(ProfileError):
        profile_digest(home, "scout")
    assert (real / "scout.toml").read_text() == '[seat]\nname = "scout"\n'
    assert sorted(p.name for p in real.iterdir()) == ["scout.toml"]  # no lock created through the link


def test_digest_refuses_seat_not_owned_by_this_user(tmp_path, monkeypatch):
    home, _digest = _home_with(tmp_path)
    target = home / "seats" / "scout.toml"
    before = target.read_bytes()
    fake_uid = os.geteuid() + 1000
    monkeypatch.setattr(profile_management.os, "geteuid", lambda: fake_uid)
    with pytest.raises(ProfileError, match="not owned"):
        profile_digest(home, "scout")
    assert target.read_bytes() == before


# ---------------------------------------------- external race between check and replace


def test_external_race_between_validation_and_replace_detected(tmp_path, monkeypatch):
    """An uncooperative edit landing before the final re-check is caught and not overwritten.

    The re-check is the last thing the design can do: an edit landing *after* it (inside the
    replace itself) cannot be detected without a filesystem compare-and-swap. That window is
    documented rather than claimed away; see the module docstring.
    """
    home, digest = _home_with(tmp_path)
    target = home / "seats" / "scout.toml"
    raced = b'[seat]\nname = "scout"\nrole = "external"\ndescription = "Runs ahead"\n'

    real_write = profile_management._write_all
    calls = {"n": 0}

    def spy(fd, data):
        real_write(fd, data)
        if calls["n"] == 0:  # an uncooperative writer rewrites the seat after validation
            calls["n"] = 1
            target.write_bytes(raced)

    monkeypatch.setattr(profile_management, "_write_all", spy)
    with pytest.raises(ProfileConflict, match="between validation and replace"):
        update_profile(home, "scout", expected_digest=digest, role="deep")
    assert target.read_bytes() == raced  # the external edit was NOT overwritten
    assert not any(".tmp-" in p.name for p in (home / "seats").iterdir())  # temp cleaned up


def test_race_inside_replace_window_is_documented_not_prevented(tmp_path, monkeypatch):
    """Pin the honest limit: a write landing after the re-check wins; the API makes no CAS claim."""
    home, digest = _home_with(tmp_path)
    target = home / "seats" / "scout.toml"
    raced = b'[seat]\nname = "scout"\nrole = "external"\ndescription = "Runs ahead"\n'

    real_replace = os.replace

    def spy(*a, **kw):
        target.write_bytes(raced)  # lands after the final re-check, before the publish
        return real_replace(*a, **kw)

    monkeypatch.setattr(os, "replace", spy)
    seat = update_profile(home, "scout", expected_digest=digest, role="deep")
    assert seat.role == "deep"
    assert 'role = "deep"' in target.read_text()
    doc = " ".join((profile_management.__doc__ or "").split())
    assert "not a compare-and-swap" in doc


def test_temp_cleaned_up_when_write_fails(tmp_path, monkeypatch):
    home, digest = _home_with(tmp_path)
    before = _seat_bytes(home)

    def boom(fd, data):
        raise OSError("disk full")

    monkeypatch.setattr(profile_management, "_write_all", boom)
    with pytest.raises(OSError, match="disk full"):
        update_profile(home, "scout", expected_digest=digest, role="deep")
    assert _seat_bytes(home) == before
    assert not any(".tmp-" in p.name for p in (home / "seats").iterdir())


def test_temp_cleaned_up_when_replace_fails(tmp_path, monkeypatch):
    home, digest = _home_with(tmp_path)
    before = _seat_bytes(home)

    def boom(*a, **kw):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="replace failed"):
        update_profile(home, "scout", expected_digest=digest, role="deep")
    assert _seat_bytes(home) == before
    assert not any(".tmp-" in p.name for p in (home / "seats").iterdir())
    assert _snapshot(home)  # the seats dir is still readable


# --------------------------------------- platform guard and fcntl fail closed, no allocation


def test_update_platform_guard_refuses_before_allocation(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_management, "_REPLACE_OK", False)
    home = tmp_path / "home"
    with pytest.raises(ProfileError, match="dir_fd"):
        update_profile(home, "scout", expected_digest="sha256:" + "0" * 64, role="deep")
    assert not home.exists()
    with pytest.raises(ProfileError, match="dir_fd"):
        profile_digest(home, "scout")
    assert not home.exists()


def test_update_fcntl_absent_refused_before_allocation(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_management, "fcntl", None)
    home = tmp_path / "home"
    with pytest.raises(ProfileError, match="flock"):
        update_profile(home, "scout", expected_digest="sha256:" + "0" * 64, role="deep")
    assert not home.exists()


def test_update_creates_no_credentials(tmp_path):
    home, digest = _home_with(tmp_path)
    update_profile(home, "scout", expected_digest=digest, role="deep")
    assert not (home / "keys").exists()
    assert not (home / "seats" / "scout.key").exists()
    assert not (home / "config.json").exists()
    assert not (home / "principal.json").exists()
