# Installing Exo

The README has the short version (`## Install`). This page has the full detail.

One script installs Exo for any user on macOS or Linux (D-004). It never touches an existing
`~/.hermes` unless you name it.

```sh
git clone <exo repo> && cd exo
scripts/install.sh --dry-run      # print the plan, change nothing
scripts/install.sh                # install Exo (no Hermes)
. ~/.exo/env.sh && exo models     # check providers and routes
scripts/install.sh --with-hermes  # optional: also pin Hermes and its patches
```

## What it does

| Step | Action |
|---|---|
| 1 | Checks the OS (macOS or Linux), `git`, and Python 3.11 or newer. It picks the newest of `python3.14` to `python3.11`, or `--python`. Hermes supports Python below 3.15. |
| 2 | Creates `$EXO_HOME` (default `~/.exo`, mode 700) with `keys/`, `principals/` and `bin/`. |
| 3 | Only with `--with-hermes`. Pins Hermes with `scripts/pin-hermes.sh` into `$EXO_HOME/hermes`: the latest stable tag, or `--tag`. If a patch does not apply to that tag, the installer skips it, warns, and retries. `PINNED` and `config.json` (`hermes_patch_skip`) record the skip. `--strict-patches` fails instead. At the end it prints a warning list of every patch that did not apply and exits 1. |
| 4 | Creates a venv at `$EXO_HOME/venv` and pip installs the `exo` package. With `--with-hermes` it also installs the pinned Hermes checkout (editable). |
| 5 | Copies the Exo plugins (every `plugins/*/plugin.yaml`, or `--plugins`) into `<HERMES_HOME>/plugins/`, without tests. Each copy carries a `.exo-managed` marker. A plugin folder without the marker is never overwritten. |
| 6 | Only with `--with-hermes`. Writes `<HERMES_HOME>/config.yaml` only if it does not exist: `plugins.enabled`, `memory.provider: exo-memory`, `context.engine: exo-context`. An existing file is left alone and the lines to add are printed. |
| 7 | Writes `$EXO_HOME/env.sh` and `$EXO_HOME/config.json`. With `--with-hermes` it also writes `$EXO_HOME/bin/exo-hermes` (runs the pinned Hermes with Exo's `HERMES_HOME`). |
| 8 | Offers `exo init <id> --interview`. With `--principal ID` it runs without asking; add `--answers FILE` for a scripted interview. |

## Where things go

| Path | Holds |
|---|---|
| `$EXO_HOME/hermes` | the pinned Hermes checkout (owned by `pin-hermes.sh`) |
| `$EXO_HOME/venv` | Python for Hermes and Exo |
| `$EXO_HOME/hermes-home` | the default `HERMES_HOME`: new, Exo-owned, separate from `~/.hermes` |
| `$EXO_HOME/principals/<id>` | principal stores |
| `$EXO_HOME/keys` | signing keys (0600) |
| `$EXO_HOME/config.json` | the shared config (`exo.config`) |

## Why a plain venv

Upstream's installer and `setup-hermes.sh` build a pm/uv-managed environment and install into
`~/.hermes`. Exo needs a separate home, so it uses a plain venv and `pip install -e` on the
pinned checkout. Hermes exact-pins its dependencies, so the result matches upstream's.

## Options

| Option | Meaning |
|---|---|
| `--dry-run` | print every action; write nothing anywhere |
| `--exo-home DIR` | Exo's home (default `$EXO_HOME`, else `~/.exo`) |
| `--hermes-home DIR` | install plugins into this `HERMES_HOME`. The only way to use `~/.hermes`. |
| `--with-hermes` | also install Hermes and its patch queue (kept until the wave 4 cutover). Off by default. |
| `--tag TAG` | Hermes release tag (needs `--with-hermes`) |
| `--exo-source SRC` | a local exo checkout or a git URL (cloned into `$EXO_HOME/src/exo`) |
| `--python PY` | interpreter for the venv |
| `--plugins LIST` | comma list, for example `exo-memory,exo-context` |
| `--principal ID`, `--answers FILE` | run the interview without prompts |
| `--no-pin`, `--no-pip`, `--no-interview` | skip a step |
| `--strict-patches` | fail when a patch does not apply (needs `--with-hermes`) |
| `--editable` | install exo editable (development) |
| `-y` | never prompt |

`HERMES_HOME` in your environment is ignored on purpose; it usually points at a live install.
Use `--hermes-home` or `EXO_HERMES_HOME`.

## Safety and re-runs

- Refuses a `HERMES_HOME`, Hermes checkout, venv or `EXO_HOME` inside `~/.hermes`, unless
  `--hermes-home` names it explicitly.
- Idempotent. A second run re-checks: plugins that match are left alone, an existing store is
  not re-created, the venv is reused, and pip is skipped when the pinned tree and the exo commit
  have not changed.
- `pin-hermes.sh` has its own guards (see `PINNING.md`).

## Adopting an existing store

```sh
exo import-store ~/my-kb --id me --activate         # copies; corpus/private stays behind
exo import-store ~/my-kb --id me --include-private  # also copies corpus/private and the ledger
```

See `ONBOARDING.md`.

## Tests

`tests/install/` runs the script under `bash` with a fake `HOME`: the dry run writes nothing,
the live-home guard holds, and a run with `--no-pin --no-pip` installs plugins, writes the
config, runs a scripted interview and is idempotent. The tests never pin Hermes.
