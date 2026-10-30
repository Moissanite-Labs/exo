"""Bounded swarm profile creation: the create-only foundation (docs/SWARM-PROFILES.md).

P03 is deliberately small. ``create_profile`` is the one entry point: it turns three explicit
strings (``name``, ``role``, ``description``) into exactly one seat file,
``$EXO_HOME/seats/<name>.toml``, and returns the parsed :class:`exo.core.seats.Seat`.

What it never does is as important as what it does:

* it never touches credentials — no key generation, no ``key_path``/``ensure_key`` call, no
  keyring or bus identity, so creating a profile grants no authority;
* it never reads a principal store and never reads or rewrites ``config.json`` or the
  environment, so the result cannot depend on ambient configuration;
* it never updates, deletes, clones or imports anything, and never overwrites an existing
  seat — a collision with any file the core loader would accept for that name is refused,
  even if the collision is a dangling symlink or arrives as a race;
* it writes no partial file: the bytes are built and validated in memory, then published
  atomically by linking an exclusively created, mode-0600 temporary into place, and only
  that temporary is ever cleaned up.

Validation is fail-closed before any filesystem access: the name must be a valid seat ID and
every field must already be a string, so a bad call allocates nothing (not even the seats
directory). The rendered TOML is parsed with ``tomllib`` *and* through
``exo.core.seats.parse`` before a byte reaches the disk, so a profile this module creates is
always one the core loader can read back.

Only ``[seat] name/role/description`` are serialized, as TOML basic strings produced by
``json.dumps(..., ensure_ascii=False)`` — the two escape grammars agree for quotes,
backslashes, newlines, tabs and C0 controls — with DEL (U+007F) re-escaped because TOML, unlike
JSON, forbids that character raw.

Platform notes (why the publish looks the way it does): publishing requires atomic ``dir_fd``,
``O_DIRECTORY`` and hard-link support — on POSIX the seats directory is opened once and every
step runs relative to that descriptor with ``O_NOFOLLOW``/``O_DIRECTORY``, so a symlink swapped
in mid-call cannot redirect a write. Where that support is unavailable (Windows, exotic
filesystems) the create is refused with ``ProfileError`` rather than degraded to a fallback
that could follow a swapped symlink. The target is created exclusively, so a concurrent creator
loses with a refusal instead of a silent overwrite. Directory creation is ``mkdir`` (0700) —
never ``makedirs`` — with an ``lstat`` re-check, so a pre-existing symlinked ``seats``
directory is refused rather than followed.
"""
from __future__ import annotations

import json
import os
import secrets
import stat
import tomllib
from pathlib import Path
from typing import Any

from exo.core import seats

FIELDS = ("name", "role", "description")
"""The only keys written, in this order, under ``[seat]``."""

TMP_PREFIX = "."
"""Temporary files are hidden and dot-prefixed so the loader's ``.toml``/``.json`` scan never sees them."""

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_DIR_FD_OK = (
    bool(_DIRECTORY)
    and os.open in os.supports_dir_fd
    and os.link in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
)


class ProfileError(ValueError):
    """A profile request was invalid, collided with an existing seat, or was refused on an unsafe path."""


def _validate(name: Any, role: Any, description: Any) -> None:
    """Fail before any filesystem access: valid ID, and every field already a string."""
    if not isinstance(name, str):
        raise ProfileError(f"profile name must be a string, not {type(name).__name__}")
    if not seats.ID_RE.fullmatch(name):
        raise ProfileError(
            f"bad profile name {name!r}: use lowercase letters, digits, - and _ (starting with a letter or digit)")
    for key, value in (("role", role), ("description", description)):
        if not isinstance(value, str):
            raise ProfileError(f"{key} must be a string, not {type(value).__name__}")


def _toml_string(value: str) -> str:
    """A TOML basic string. json.dumps(ensure_ascii=False) is used for the shared escapes.

    The two grammars agree on ``"``, ``\\``, ``\\n``, ``\\t``, ``\\b``, ``\\f``, ``\\r``, the
    ``\\u00XX`` form for the other C0 controls and on every printable, non-ASCII character.
    They disagree on exactly one class: TOML forbids a raw DEL (U+007F), which JSON leaves
    unescaped, so it is converted to its ``\\u007f`` escape here.
    """
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _render(name: str, role: str, description: str) -> str:
    values = {"name": name, "role": role, "description": description}
    lines = ["[seat]"]
    for key in FIELDS:
        if key != "name" and not values[key]:
            continue  # keep an empty optional field out of the file; the loader defaults it
        lines.append(f"{key} = {_toml_string(values[key])}")
    return "\n".join(lines) + "\n"


def _validated(name: str, role: str, description: str) -> tuple[str, dict[str, Any]]:
    """Render and prove the content round-trips through tomllib and the core seat parser."""
    text = _render(name, role, description)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:  # pragma: no cover - a bug here must never publish
        raise ProfileError(f"generated profile for {name!r} is not valid TOML: {e}") from e
    try:
        seat = seats.parse(data, name=name)
    except seats.SeatError as e:  # pragma: no cover - ditto
        raise ProfileError(f"generated profile for {name!r} does not parse as a seat: {e}") from e
    if seat.name != name or seat.role != role or seat.description != description:
        raise ProfileError(f"generated profile for {name!r} did not round-trip")  # pragma: no cover
    return text, data


def _collision_paths(seats_root: Path, name: str) -> list[Path]:
    """Every path ``exo.core.seats.load`` would accept for this name (mirrors ``seats._candidates``)."""
    return [
        seats_root / f"{name}.toml",
        seats_root / f"{name}.json",
        seats_root / name / "seat.toml",
        seats_root / name / "seat.json",
    ]


def _occupied(path: Path) -> bool:
    """Existence without following symlinks: a dangling symlink still occupies the name."""
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as e:  # a parent that cannot be listed is a refusal, never a licence to write
        raise ProfileError(f"{path} cannot be inspected: {e}") from e
    return True


def _check_free(seats_root: Path, name: str) -> None:
    for path in _collision_paths(seats_root, name):
        if _occupied(path):
            raise ProfileError(f"profile {name!r} already exists at {path}")
    if _occupied(seats_root / name):
        raise ProfileError(f"profile {name!r} already exists at {seats_root / name}")


def _ensure_dir(path: Path) -> None:
    """Create one directory with mode 0700 if missing; never follow or chmod an existing symlink."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:  # another process created it first: fall through to the checks
            pass
        except OSError as e:
            raise ProfileError(f"{path} cannot be created: {e}") from e
        try:
            info = os.lstat(path)
        except OSError as e:
            raise ProfileError(f"{path} cannot be inspected: {e}") from e
    except OSError as e:
        raise ProfileError(f"{path} cannot be inspected: {e}") from e
    if stat.S_ISLNK(info.st_mode):
        raise ProfileError(f"{path} is a symlink; refusing to write profiles through it")
    if not stat.S_ISDIR(info.st_mode):
        raise ProfileError(f"{path} exists and is not a directory")


def _ensure_home(home: Path) -> None:
    """Make ``home`` exist as a directory, creating any missing levels with mode 0700.

    ``mkdir`` is used one level at a time (never ``makedirs``), so every directory this
    function creates gets an explicit 0700 and a symlinked ``home`` (a mounted
    ``$EXO_HOME``) is followed only where it already existed.
    """
    missing = []
    probe = home
    while not probe.exists():
        missing.append(probe)
        parent = probe.parent
        if parent == probe:
            break
        probe = parent
    for level in reversed(missing):
        try:
            os.mkdir(level, 0o700)
        except FileExistsError:  # another process created it first: re-checked below
            pass
        except OSError as e:
            raise ProfileError(f"{level} cannot be created: {e}") from e
    if not home.is_dir():
        raise ProfileError(f"{home} exists and is not a directory")


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _temp_name(name: str) -> str:
    return f"{TMP_PREFIX}{name}.toml.tmp-{os.getpid()}-{secrets.token_hex(6)}"


def _publish_by_dirfd(seats_root: Path, target: str, data: bytes) -> None:
    """POSIX path: temp file and hard link both relative to one O_NOFOLLOW directory descriptor."""
    dfd = os.open(seats_root, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
    try:
        info = os.fstat(dfd)
        if not stat.S_ISDIR(info.st_mode):  # pragma: no cover - O_DIRECTORY already guarantees this
            raise ProfileError(f"{seats_root} is not a directory")
        tmp = _temp_name(target.removesuffix(".toml"))
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600, dir_fd=dfd)
        except FileExistsError as e:  # pragma: no cover - 96 bits of randomness
            raise ProfileError(f"temporary file for {target!r} already exists") from e
        try:
            _write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            # src_dir_fd/dst_dir_fd keep both names relative to the same descriptor, and
            # no AT_SYMLINK_FOLLOW: linkat() itself fails if a link ever took the temp's place.
            os.link(tmp, target, src_dir_fd=dfd, dst_dir_fd=dfd, follow_symlinks=False)
        except FileExistsError as e:
            raise ProfileError(f"profile file {target!r} appeared while it was being created") from e
        finally:
            try:
                os.unlink(tmp, dir_fd=dfd)  # only ever this call's own temporary
            except FileNotFoundError:  # pragma: no cover
                pass
    finally:
        os.close(dfd)


def create_profile(
    home: str | Path,
    name: str,
    *,
    role: str = "",
    description: str = "",
) -> seats.Seat:
    """Create ``<home>/seats/<name>.toml`` and return it as a :class:`exo.core.seats.Seat`.

    ``name`` must be a valid seat ID and ``role``/``description`` must be strings;
    anything else is refused before the filesystem is touched. An existing seat in any form
    ``seats.load`` accepts (``<name>.toml``, ``<name>.json``, ``<name>/seat.toml``,
    ``<name>/seat.json``, including a dangling symlink) is a collision and is never
    overwritten. No key, principal store or config is read or written: this grants no
    authority and creates no credential.
    """
    _validate(name, role, description)
    text, data = _validated(name, role, description)
    try:
        payload = text.encode("utf-8")
    except UnicodeEncodeError as e:  # a lone surrogate: refuse before the fs is touched
        raise ProfileError(f"profile {name!r} fields cannot be encoded as UTF-8: {e}") from e

    if not _DIR_FD_OK:
        raise ProfileError(
            "this platform lacks the dir_fd/O_DIRECTORY/hard-link support required for safe "
            "atomic profile publication; refusing to create without it")

    home_path = Path(home)
    _ensure_home(home_path)
    seats_root = home_path / "seats"
    _ensure_dir(seats_root)
    _check_free(seats_root, name)

    target = f"{name}.toml"
    _publish_by_dirfd(seats_root, target, payload)

    return seats.parse(data, name=name, path=str(seats_root / target))
