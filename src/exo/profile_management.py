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

The bounded update slice adds exactly two more operations and no new authority:
``profile_digest`` (the content fingerprint a caller must present) and ``update_profile``
(rewrite ``[seat] role``/``description`` of one *canonical* seat file). It NEVER creates the
home or the seats directory, never adds a seat, never touches a key and never reads a
principal store or ``config.json``.

Strictly canonical only: the existing bytes must be exactly what ``_render`` produces for
``[seat] name/role/description`` and must round-trip to the same three values. Anything else
— a comment, an unknown or extra key, another table, a JSON seat, the ``<name>/seat.toml``
form, reordered or hand-spaced lines — is refused untouched rather than rewritten, because
this module has no general TOML writer and will not invent one.

What bounded updating is and is not: ``expected_digest`` is a *fingerprint*, not a
compare-and-swap. Against cooperative writers (other callers of ``update_profile``/``profile_digest``)
updates are serialized by an exclusive, non-blocking ``flock`` on the sibling
``.<name>.lock`` file and a stale digest is refused. It is explicitly NOT a hardware or
filesystem atomic CAS: the final inode/byte re-check and the ``os.replace`` are two steps, so
an uncooperative external process that writes ``<name>.toml`` in that window can still have
its edit overwritten (or can be the one written after us). Nothing here pretends otherwise;
the lock file is never removed (removing it would break the serialization it provides) and it
is the only metadata a refused update may leave behind.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import tomllib
from pathlib import Path
from typing import Any

from exo.core import seats

try:  # POSIX only. A missing fcntl means the update lock cannot be taken, and updates fail closed.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

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


class ProfileConflict(ProfileError):
    """An update lost to a concurrent writer: the profile is locked, or its digest is no longer ``expected_digest``."""


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
            except FileNotFoundError:  # the link consumed it (or a concurrent cleaner did)
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


# --------------------------------------------------------------------- bounded update

DIGEST_PREFIX = "sha256:"
"""One digest form only: an algorithm tag and lowercase hex."""

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
"""The exact ``profile_digest`` spelling accepted as ``expected_digest``."""

LOCK_SUFFIX = ".lock"
"""The lock is a *sibling* of the seat file, hidden and never a ``.toml``/``.json`` the loader would see."""

_REPLACE_OK = (
    bool(_NOFOLLOW)
    and bool(_DIRECTORY)
    and os.open in os.supports_dir_fd
    # ``os.replace`` accepts ``src_dir_fd``/``dst_dir_fd`` but is not always listed in
    # ``os.supports_dir_fd`` (CPython does not advertise it); it shares ``os.rename``'s binding, so
    # a platform advertising either one supports the relative atomic replace.
    and (os.replace in os.supports_dir_fd or os.rename in os.supports_dir_fd)
)
"""Atomic same-directory replace relative to a directory descriptor, plus ``O_NOFOLLOW``."""

_REPLACE_REFUSAL = (
    "this platform lacks the dir_fd/O_NOFOLLOW/atomic-replace support required for safe profile "
    "updates; refusing to update without it")

_LOCK_REFUSAL = (
    "this platform has no fcntl/flock, so profile updates cannot be serialized; refusing to update")


def _check_name(name: Any) -> str:
    """The seat-ID rule, checked before any filesystem access."""
    if not isinstance(name, str):
        raise ProfileError(f"profile name must be a string, not {type(name).__name__}")
    if not seats.ID_RE.fullmatch(name):
        raise ProfileError(
            f"bad profile name {name!r}: use lowercase letters, digits, - and _ (starting with a letter or digit)")
    return name


def _digest_bytes(data: bytes) -> str:
    return DIGEST_PREFIX + hashlib.sha256(data).hexdigest()


def _check_digest(expected_digest: Any) -> str:
    """``expected_digest`` is mandatory and must be a ``sha256:<64 hex>`` string."""
    if expected_digest is None:
        raise ProfileError(
            "expected_digest is required: read it with profile_digest(home, name) and pass it back so a "
            "stale revision is refused instead of silently overwritten")
    if not isinstance(expected_digest, str) or not DIGEST_RE.fullmatch(expected_digest):
        raise ProfileError(
            f"expected_digest must look like 'sha256:<64 lowercase hex>', not {expected_digest!r}")
    return expected_digest


def _seat_name(name: str) -> str:
    return f"{name}.toml"


def _lock_name(name: str) -> str:
    return f".{name}{LOCK_SUFFIX}"


def _owned_regular(info: os.stat_result, label: str) -> None:
    """Only a regular file this user owns may be read as, locked as or replaced by a profile."""
    if not stat.S_ISREG(info.st_mode):
        raise ProfileError(f"{label} is not a regular file; refusing to treat it as a profile")
    getuid = getattr(os, "geteuid", None)
    if getuid is None:  # pragma: no cover - the dir_fd/O_NOFOLLOW guard already refused these platforms
        raise ProfileError(f"{label}: ownership cannot be checked on this platform; refusing")
    if info.st_uid != getuid():
        raise ProfileError(f"{label} is not owned by this user; refusing to read or rewrite it")


def _read_all(fd: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        block = os.read(fd, 1 << 16)
        if not block:
            return b"".join(chunks)
        chunks.append(block)


def _read_fd(fd: int, label: str) -> tuple[bytes, os.stat_result]:
    """Read one opened file completely, refusing a non-regular file and a read that raced a change."""
    before = os.fstat(fd)
    data = _read_all(fd)
    after = os.fstat(fd)
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) \
            or len(data) != after.st_size:
        raise ProfileConflict(f"{label} changed while it was being read; read it again")
    return data, after


def _absent(dfd: int, entry: str) -> None:
    """Prove ``entry`` is absent in the seats directory, without following a symlink."""
    try:
        os.stat(entry, dir_fd=dfd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as e:  # pragma: no cover - a directory we cannot inspect is a refusal
        raise ProfileError(f"{entry} cannot be inspected: {e}") from e
    raise ProfileError(
        f"profile {entry!r} is present in the seats directory; only a single regular "
        f"<name>.toml may be updated, so an alternate form is refused untouched")


def _check_only_canonical(dfd: int, name: str) -> None:
    """Refuse a name that is (also) present as ``<name>.json``, ``<name>/`` or ``<name>/seat.*``."""
    for entry in (f"{name}.json", name):
        _absent(dfd, entry)


def _open_seats(seats_root: Path) -> int:
    """Open an EXISTING seats directory read-only; never create it, never follow a symlink."""
    try:
        dfd = os.open(seats_root, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
    except FileNotFoundError as e:
        raise ProfileError(f"{seats_root} does not exist; an update never creates a home or a seats directory") from e
    except OSError as e:
        raise ProfileError(f"{seats_root} cannot be opened as a directory: {e}") from e
    try:
        info = os.fstat(dfd)
        if not stat.S_ISDIR(info.st_mode):  # pragma: no cover - O_DIRECTORY already guarantees this
            raise ProfileError(f"{seats_root} is not a directory")
    except BaseException:
        os.close(dfd)
        raise
    return dfd


def _strict_fields(data: bytes, name: str) -> tuple[str, str]:
    """Parse an existing seat file ONLY if it is the exact canonical form this module writes.

    Returns ``(role, description)``. Any other file — a comment, an unknown key, a second table,
    JSON, the directory form, a ``name`` that disagrees with the file name — is refused, because
    a rewrite through ``_render`` would silently drop whatever this module cannot represent.
    """
    try:
        doc = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise ProfileError(
            f"existing profile {name!r} is not valid UTF-8 TOML ({e}); refusing to rewrite it") from e
    if set(doc) != {"seat"} or not isinstance(doc.get("seat"), dict):
        raise ProfileError(
            f"existing profile {name!r} has more than a [seat] table (or no [seat] table); "
            "only the canonical flat form is updated")
    head = doc["seat"]
    extra = sorted(set(head) - set(FIELDS))
    if extra:
        raise ProfileError(
            f"existing profile {name!r} has keys this update cannot preserve ({', '.join(extra)}); "
            "refusing to rewrite it")
    if "name" not in head:
        raise ProfileError(
            f"existing profile {name!r} has no [seat] name; it is not the canonical form, so it is "
            "refused rather than assigned an identity")
    for key in FIELDS:
        if key in head and not isinstance(head[key], str):
            raise ProfileError(f"existing profile {name!r} has a non-string {key}; refusing to rewrite it")
    if head["name"] != name:
        raise ProfileError(
            f"existing profile {name!r} declares [seat] name {head['name']!r}; refusing to update a "
            "file whose identity disagrees with its name")
    role = head.get("role", "")
    description = head.get("description", "")
    if data != _render(name, role, description).encode("utf-8"):
        raise ProfileError(
            f"existing profile {name!r} is not in canonical form (comments, extra whitespace, a "
            "reordered or omitted field, or another layout); it is left untouched")
    return role, description


class _Snapshot:
    """What was validated before the rewrite: the exact bytes, the fields and the file identity."""

    __slots__ = ("data", "description", "ino", "mode", "role")

    def __init__(self, data: bytes, role: str, description: str, info: os.stat_result) -> None:
        self.data = data
        self.role = role
        self.description = description
        self.ino = info.st_ino
        self.mode = stat.S_IMODE(info.st_mode)


def _read_canonical(dfd: int, name: str, expected_digest: str) -> _Snapshot:
    """Open, verify and validate the one canonical seat file. Reads only: no lock file is created."""
    target = _seat_name(name)
    try:
        fd = os.open(target, os.O_RDONLY | _NOFOLLOW, dir_fd=dfd)
    except FileNotFoundError as e:
        raise ProfileError(
            f"no profile {name!r} at {target}; only an existing regular {target} can be updated") from e
    except OSError as e:
        raise ProfileError(f"{target} cannot be opened without following a symlink: {e}") from e
    try:
        _owned_regular(os.fstat(fd), target)
        data, info = _read_fd(fd, target)
    finally:
        os.close(fd)
    actual = _digest_bytes(data)
    if actual != expected_digest:
        raise ProfileConflict(
            f"profile {name!r} has changed since expected_digest was read (now {actual}); "
            "read profile_digest again and reapply")
    role, description = _strict_fields(data, name)
    return _Snapshot(data, role, description, info)


def _acquire_lock(dfd: int, name: str) -> int:
    """Exclusive, non-blocking flock on the sibling ``.<name>.lock`` (0600); returns its fd.

    The lock file is created with ``O_NOFOLLOW|O_CREAT`` and never unlinked: unlinking it would
    let two updaters hold different inodes of the same name and lose the serialization. It is
    therefore permanent metadata, and the only file an update may leave behind on a refusal.
    """
    entry = _lock_name(name)
    if fcntl is None:
        raise ProfileError(_LOCK_REFUSAL)
    try:
        fd = os.open(entry, os.O_RDWR | os.O_CREAT | _NOFOLLOW, 0o600, dir_fd=dfd)
    except OSError as e:
        raise ProfileError(f"lock {entry} cannot be opened without following a symlink: {e}") from e
    try:
        _owned_regular(os.fstat(fd), entry)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise ProfileConflict(
                f"profile {name!r} is being updated by another process ({entry} is locked); "
                "nothing was written, try again") from e
    except BaseException:
        os.close(fd)
        raise
    return fd


def _rendered(name: str, role: str, description: str) -> bytes:
    """Render, prove the round-trip and encode — never a partial or hand-built TOML document."""
    text, _data = _validated(name, role, description)
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as e:  # a lone surrogate: refuse before the fs is touched
        raise ProfileError(f"profile {name!r} fields cannot be encoded as UTF-8: {e}") from e


def _replace_verified(dfd: int, name: str, payload: bytes, snapshot: _Snapshot) -> None:
    """Write a mode-preserving temp, re-check the original, then replace it relative to ``dfd``."""
    target = _seat_name(name)
    tmp = _temp_name(name)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600, dir_fd=dfd)
    try:
        try:
            _write_all(fd, payload)
            os.fchmod(fd, snapshot.mode)  # keep the original mode instead of imposing 0600
            os.fsync(fd)
        finally:
            os.close(fd)
        # Re-check the original as late as the design allows (see the module docstring: this is a
        # re-check, not an atomic CAS, so an uncooperative external edit can still race it).
        now_fd = os.open(target, os.O_RDONLY | _NOFOLLOW, dir_fd=dfd)
        try:
            _owned_regular(os.fstat(now_fd), target)
            now, now_info = _read_fd(now_fd, target)
        finally:
            os.close(now_fd)
        if now_info.st_ino != snapshot.ino or now != snapshot.data:
            raise ProfileConflict(
                f"profile {name!r} changed between validation and replace; nothing was overwritten")
        _check_only_canonical(dfd, name)
        os.replace(tmp, target, src_dir_fd=dfd, dst_dir_fd=dfd)
        try:
            os.fsync(dfd)
        except OSError:  # pragma: no cover - fsync on a directory is not universal
            pass
    finally:
        try:
            os.unlink(tmp, dir_fd=dfd)  # only ever this call's own temporary
        except FileNotFoundError:  # the replace consumed it
            pass


def profile_digest(home: str | Path, name: str) -> str:
    """Return ``sha256:<hex>`` over the exact bytes of the canonical ``<home>/seats/<name>.toml``.

    Read-only: no lock file is created or taken, no directory is allocated, no key, principal
    store or config is read. Only a regular ``<name>.toml`` owned by this user counts — a
    symlink, the ``.json`` form and the ``<name>/seat.*`` form are refused, as is a missing or
    non-canonical file (the digest is only meaningful for a file ``update_profile`` may rewrite).
    """
    name = _check_name(name)
    if not _REPLACE_OK:
        raise ProfileError(_REPLACE_REFUSAL)
    seats_root = Path(home) / "seats"
    dfd = _open_seats(seats_root)
    try:
        _check_only_canonical(dfd, name)  # an alternate form means the name is ambiguous: refuse
        target = _seat_name(name)
        try:
            fd = os.open(target, os.O_RDONLY | _NOFOLLOW, dir_fd=dfd)
        except FileNotFoundError as e:
            raise ProfileError(
                f"no profile {name!r} at {seats_root / target}; only an existing regular "
                f"{target} has a digest") from e
        except OSError as e:
            raise ProfileError(f"{target} cannot be opened without following a symlink: {e}") from e
        try:
            _owned_regular(os.fstat(fd), target)
            data, _info = _read_fd(fd, target)
        finally:
            os.close(fd)
        _strict_fields(data, name)  # a file this module could not update gets no digest
        return _digest_bytes(data)
    finally:
        os.close(dfd)


def update_profile(
    home: str | Path,
    name: str,
    *,
    expected_digest: str,
    role: str | None = None,
    description: str | None = None,
) -> seats.Seat:
    """Rewrite ``[seat] role``/``description`` of one existing canonical seat file.

    ``expected_digest`` is required (see :func:`profile_digest`): a stale fingerprint is refused
    with :class:`ProfileConflict` instead of overwriting a newer revision. At least one of
    ``role``/``description`` must be given; ``None`` means "keep the current value", so a field
    is cleared by passing ``""``. Fields this module does not own are never written, and the
    canonical renderer (:func:`_render`) is the only TOML writer here.

    The call never creates the home or the seats directory, never adds a seat, and never reads or
    writes a key, principal store or ``config.json``. Only a regular, owned ``<name>.toml`` is
    updated; ``<name>.json``, ``<name>/``, ``<name>/seat.toml`` and symlinks are refused. The
    rewrite is serialized against other cooperative updaters by an exclusive non-blocking flock
    on ``.<name>.lock`` (that lock file is permanent and may be created even when the update is
    then refused) and published by atomic ``os.replace`` of a mode-preserving temp, after a final
    inode+byte re-check. That re-check plus replace is not an atomic compare-and-swap: an
    uncooperative external writer editing the file in that window can still be overwritten.
    """
    name = _check_name(name)
    expected_digest = _check_digest(expected_digest)
    changes: dict[str, str] = {}
    for key, value in (("role", role), ("description", description)):
        if value is None:
            continue
        if not isinstance(value, str):
            raise ProfileError(f"{key} must be a string, not {type(value).__name__}")
        changes[key] = value
    if not changes:
        raise ProfileError(
            "update_profile needs at least one field to change: pass role= and/or description=")
    if not _REPLACE_OK:
        raise ProfileError(_REPLACE_REFUSAL)
    if fcntl is None:
        raise ProfileError(_LOCK_REFUSAL)

    home_path = Path(home)
    seats_root = home_path / "seats"
    dfd = _open_seats(seats_root)
    try:
        lock_fd = _acquire_lock(dfd, name)
        try:
            snapshot = _read_canonical(dfd, name, expected_digest)
            payload = _rendered(
                name,
                changes.get("role", snapshot.role),
                changes.get("description", snapshot.description),
            )
            _check_only_canonical(dfd, name)  # refuse an ambiguous name even for a no-op rewrite
            if payload == snapshot.data:  # an idempotent call rewrites nothing at all
                return seats.parse(tomllib.loads(payload.decode("utf-8")), name=name,
                                   path=str(seats_root / _seat_name(name)))
            _replace_verified(dfd, name, payload, snapshot)
        finally:
            os.close(lock_fd)  # closing the descriptor drops the flock; the file itself stays
    finally:
        os.close(dfd)
    return seats.parse(tomllib.loads(payload.decode("utf-8")), name=name,
                       path=str(seats_root / _seat_name(name)))
