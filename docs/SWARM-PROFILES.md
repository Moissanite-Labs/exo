# Swarm profiles

One profile is one Exo seat: a named agent configuration under
`$EXO_HOME/seats/<name>.toml`. A swarm uses several of them (see
[SWARM-COMPOSITION.md](SWARM-COMPOSITION.md) and [SWARM-HOST.md](SWARM-HOST.md) for how
profiles become engines).

P03 status: **create only.** The foundation is
`exo.profile_management.create_profile(home, name, *, role="", description="") -> Seat`
in `src/exo/profile_management.py`.

## Create (`create_profile`)

Renders `[seat]` `name`/`role`/`description` as TOML basic strings (escaped via
`json.dumps(..., ensure_ascii=False)`, which agrees with TOML on quotes, backslashes,
newlines, tabs, other C0 controls and non-ASCII text; a raw DEL is re-escaped because TOML
forbids it), proves the result parses with `tomllib` **and** `exo.core.seats.parse`, then
publishes `<home>/seats/<name>.toml` and returns the parsed `Seat` (from
`exo.core.seats`). Empty optional fields are left out of the file.

Guarantees:

- **Fail before the filesystem.** A non-string `name`/`role`/`description`, an
  invalid seat ID, or text that cannot be encoded as UTF-8 raises `ProfileError` before
  any file or directory is allocated.
- **No credentials.** No key is generated and `exo.core.seats.ensure_key`/`key_path` are
  never called; creating a profile grants no authority.
- **No principal store or config access.** No store is read, `config.json` is never read
  or written, and no environment variable is consulted.
- **Collisions win.** If `seats.load` could load this name from any of its supported
  variants — `<name>.toml`, `<name>.json`, `<name>/seat.toml`, `<name>/seat.json` — the
  create is refused. A dangling symlink still occupies the name (checked with `lstat`, so
  nothing is followed), and a concurrent creator is refused, never overwritten. The
  exclusive-publish guarantee covers the `<name>.toml` file itself: a concurrent creator of
  the same `.toml` loses with a refusal, never a silent overwrite. The same is **not**
  guaranteed against an external process creating the other variants at the same time — a
  simultaneous external `<name>.json` or `<name>/seat.*` creation can yield two files for one
  name, and P03 does not detect or clean that up.
- **Safe paths.** The seats directory is refused if it is a symlink or not a directory.
  It is created with mode 0700 (and every directory `create_profile` makes gets 0700);
  the file is published with mode 0600. Symlinks are never followed: on POSIX the publish
  runs relative to an `O_NOFOLLOW|O_DIRECTORY` descriptor of the seats directory, and the
  final create is `O_CREAT|O_EXCL`. Where `dir_fd`/`O_DIRECTORY`/hard-link support is missing
  (e.g. Windows, exotic filesystems) the create is refused with `ProfileError` — there is no
  less-safe fallback that could follow a swapped symlink.
- **No partial profile.** The bytes are built and validated in memory, written to an
  exclusively created temporary and linked into place (the temp name must still exist at
  link time, so a swap in cannot be published); the temporary is cleaned up on any
  failure, and only that temporary — never a target created by another process — is ever
  removed.

There is deliberately **no** update, delete, clone, import, export or key operation yet,
and `create_profile` never grants authority: it writes one seat file and nothing else.

## SOUL (deferred)

A profile's `soul` (the SOUL.md text that defines its personality) is **not** part of P03.
`create_profile` takes no `soul=` argument — passing one raises `TypeError` before anything is
allocated — and the flat `<name>.toml` form carries no soul (core `seats.load` reads soul only
from the directory form). Soul support lands in a later P number.

## Planned (not implemented)

Remaining profile operations land later as separate bounded changes; none of them grant
or record authority:

- update (edit `role`/`description`/`soul` of an existing profile)
- delete
- clone
- import / export (move profiles between homes or machines)
