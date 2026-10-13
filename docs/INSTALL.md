# Installing Exo

The README has the short version (`## Install`). This page has the full detail.

One script installs Exo for any user on macOS or Linux (D-004). It never touches an existing
`~/.hermes` unless you name it.

```sh
git clone https://github.com/aska-digital/exo && cd exo
scripts/install.sh --dry-run      # print the plan, change nothing
scripts/install.sh                # install Exo (no Hermes); `exo` lands on your PATH
exo                               # open the terminal client (a new terminal if PATH just changed)
scripts/install.sh --with-hermes  # optional: also pin Hermes and its patches
```

To install from the repository URL without cloning first, give the installer a source and it clones into
`~/.exo/src/exo`: `bash scripts/install.sh --exo-source https://github.com/aska-digital/exo`. A re-run pulls and
reinstalls (an upgrade). `exo update` does the same from anywhere.

## The `exo` command

The installer links `~/.local/bin/exo` to the venv's `exo` (`--bin-dir` or `$EXO_BIN_DIR` to change the folder).
If that folder is not on your PATH, it prints the line for your shell and does not touch your rc files:

```sh
export PATH="$HOME/.local/bin:$PATH"   # zsh: ~/.zshrc, bash: ~/.bashrc, fish: fish_add_path ~/.local/bin
```

`--modify-path` appends that line to the rc file once (a second run sees it and adds nothing). A file already at
`~/.local/bin/exo` that is not this installer's link is left alone, with a warning. The installer never reads or
writes model keys; the first `exo` run with no working provider opens the providers panel (`/providers`).

`exo update` upgrades in place and prints `old -> new`, then checks that `import exo` works, the protocol export
is current and `exo models` answers. It reads `install` in `config.json` (written by the installer): it pulls the
checkout and re-runs `install.sh`. For a `pip install -e` checkout it pulls and reinstalls; for a `uv tool install`
it runs `uv tool upgrade exo`. `exo update --dry-run` prints the steps and changes nothing.

## What it does

| Step | Action |
|---|---|
| 1 | Checks the OS (macOS or Linux), `git`, and Python 3.11 or newer. It picks the newest of `python3.14` to `python3.11`, or `--python`. Hermes supports Python below 3.15. |
| 2 | Creates `$EXO_HOME` (default `~/.exo`, mode 700) with `keys/`, `principals/` and `bin/`. |
| 3 | Only with `--with-hermes`. Pins Hermes with `scripts/pin-hermes.sh` into `$EXO_HOME/hermes`: the latest stable tag, or `--tag`. If a patch does not apply to that tag, the installer skips it, warns, and retries. `PINNED` and `config.json` (`hermes_patch_skip`) record the skip. `--strict-patches` fails instead. At the end it prints a warning list of every patch that did not apply and exits 1. |
| 4 | Creates a venv at `$EXO_HOME/venv` and pip installs the `exo` package with the terminal client (`exo[tui]`), then links `exo` into `~/.local/bin`. With `--with-hermes` it also installs the pinned Hermes checkout (editable). |
| 5 | Copies the Exo plugins (every `plugins/*/plugin.yaml`, or `--plugins`) into `$EXO_HOME/plugins/`, where the standalone core discovers them, without tests. With `--with-hermes`, `--hermes-home` or `EXO_HERMES_HOME`, it also copies them into `<HERMES_HOME>/plugins/`. Each copy carries a `.exo-managed` marker. A plugin folder without the marker is never overwritten. |
| 6 | Only with `--with-hermes`. Writes `<HERMES_HOME>/config.yaml` only if it does not exist: `plugins.enabled`, `memory.provider: exo-memory`, `context.engine: exo-context`. An existing file is left alone and the lines to add are printed. |
| 7 | Writes `$EXO_HOME/env.sh` and `$EXO_HOME/config.json`. With `--with-hermes` it also writes `$EXO_HOME/bin/exo-hermes` (runs the pinned Hermes with Exo's `HERMES_HOME`). |
| 8 | Offers `exo init <id> --interview`. With `--principal ID` it runs without asking; add `--answers FILE` for a scripted interview. |

## Where things go

| Path | Holds |
|---|---|
| `$EXO_HOME/hermes` | the pinned Hermes checkout (owned by `pin-hermes.sh`) |
| `$EXO_HOME/venv` | Python for Hermes and Exo |
| `$EXO_HOME/plugins` | plugins discovered by the standalone core |
| `$EXO_HOME/hermes-home` | the default optional `HERMES_HOME`: Exo-owned, separate from `~/.hermes` |
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
| `--hermes-home DIR` | also copy plugins into this `HERMES_HOME`. The only way to use `~/.hermes`. |
| `--with-hermes` | also install Hermes and its patch queue (kept until the wave 4 cutover). Off by default. |
| `--tag TAG` | Hermes release tag (needs `--with-hermes`) |
| `--exo-source SRC` | a local exo checkout or a git URL (cloned into `$EXO_HOME/src/exo`) |
| `--python PY` | interpreter for the venv |
| `--plugins LIST` | comma list, for example `exo-memory,exo-context` |
| `--principal ID`, `--answers FILE` | run the interview without prompts |
| `--no-pin`, `--no-pip`, `--no-interview` | skip a step |
| `--strict-patches` | fail when a patch does not apply (needs `--with-hermes`) |
| `--editable` | install exo editable (development) |
| `--bin-dir DIR` | where the `exo` link goes (default `$EXO_BIN_DIR`, else `~/.local/bin`) |
| `--modify-path` | add `DIR` to your shell rc file when it is not on PATH (default: print the line only) |
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

The core refuses tool execution if no approval callback is registered, or if an approval callback fails.
Re-run the installer to restore missing plugins. If `core_plugins` or a seat's plugins list limits what loads,
include `exo-gate`, then restart Exo.

## Adopting an existing store

```sh
exo import-store ~/my-kb --id me --activate         # copies; corpus/private stays behind
exo import-store ~/my-kb --id me --include-private  # also copies corpus/private and the ledger
```

See `ONBOARDING.md`.

## Tests

`tests/install/` runs the script under `bash` with a fake `HOME`: the dry run writes nothing,
the live-home guard holds, and a run with `--no-pin --no-pip` installs plugins, writes the
config, runs a scripted interview and is idempotent. Installation smoke tests discover and load all six
plugins from the standalone home, including the gate and command centre, with no `EXO_PLUGIN_DIRS` override.
The tests never pin Hermes.
