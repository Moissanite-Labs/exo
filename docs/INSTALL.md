# Installing Exo

The README has the short version (`## Quick start`). This page has the full detail. The repository is public
and remains an early preview.

One script installs Exo for any user on macOS or Linux (D-004). It never touches an existing
`~/.hermes` unless you name it.

```sh
git clone https://github.com/Moissanite-Labs/exo && cd exo
scripts/install.sh --dry-run      # print the plan, change nothing
scripts/install.sh                # install Exo (no Hermes); `exo` lands on your PATH
exo                               # open the terminal client (a new terminal if PATH just changed)
scripts/install.sh --with-hermes  # optional: also pin Hermes and its patches
```

To install from the repository URL without cloning first, give the installer a source and it clones into
`~/.exo/src/exo`: `bash scripts/install.sh --exo-source https://github.com/Moissanite-Labs/exo`. A re-run pulls and
reinstalls (an upgrade). `exo update` does the same from anywhere.

## The `exo` command

The installer links `~/.local/bin/exo` to the venv's `exo` (`--bin-dir` or `$EXO_BIN_DIR` to change the folder).
If that folder is not on your PATH, it prints the line for your shell and does not touch your rc files:

```sh
export PATH="$HOME/.local/bin:$PATH"   # zsh: ~/.zshrc, bash: ~/.bashrc, fish: fish_add_path ~/.local/bin
```

`--modify-path` appends that line to the rc file once (a second run sees it and adds nothing). A file already at
`~/.local/bin/exo` that is not this installer's link is left alone, with a warning. The installer never reads or
writes model keys. The first `exo` run with no working provider says so in a note and opens the providers panel.
If one provider is set up but no main model is chosen, the model picker opens instead. Type `/providers` to open
the panel again.

`exo update` checks the checkout for tracked changes, untracked files and ignored source files before pulling with
`--ff-only`. When it stops, it names up to five of the paths that block it. Commit, stash (including untracked
files), or remove local changes before updating. It
checks the target Python for pip first; when pip is absent (for example after `uv sync`), it uses
`uv pip --python` for that same environment. If neither installer is available, it stops before the
pull. Installer-managed installs also refresh their plugins and skill pack with `install.sh --no-pip --no-interview`.
For a `uv tool install`, it runs `uv tool upgrade exo`. After installation a fresh Python process checks
imports, the protocol export and `exo models`. `exo update --dry-run` only prints the plan: it runs no
subprocesses, preflight probes, upgrades or checks.

That refresh keeps what you chose at install. `config.json` remembers a `--plugins` list (`plugin_selection`) and
the Hermes home the plugins were copied into (`hermes_home`, written by `--with-hermes`, `--hermes-home` or
`EXO_HERMES_HOME`). A re-run without those options, like the one `exo update` makes, installs the same plugins and
refreshes the copies in that Hermes home. `--plugins all` goes back to every plugin, new ones included. A remembered
Hermes home inside `~/.hermes` is left alone with a warning: pass `--hermes-home` again to refresh it. The core
loads every folder in `$EXO_HOME/plugins`, so a list also removes the plugins an earlier run installed there and the
list leaves out. It removes a folder only when its `.exo-managed` marker names that folder. Folders without the
marker stay, and so does a copy of a plugin, with a warning (see `--plugins`). A Hermes home is left as is, since its
`config.yaml` decides what loads. An install from before `plugin_selection` existed has no list on record, so its
next refresh installs every plugin. After that update, run `scripts/install.sh --no-pip --plugins LIST` once: it
records your list and removes the plugins the refresh added. A remembered name that this checkout no longer has
gets a warning, and the refresh goes on without it.

After updating, finish active turns, close your clients, run `exo stop`, then reopen `exo` or `exo web`.
The running shared core and clients retain their loaded code until restarted. The updater prints this
instruction and leaves stopping the core to you.

The updater refuses environment overrides that redirect Git, package sources/configuration, or
Python and shell execution (including `GIT_CONFIG*`, `PIP_INDEX_URL`, `UV_INDEX`, `PYTHONPATH`,
`PYTHONHOME`, `EXO_PYTHON`, `EXO_VENV`, `BASH_ENV`, `ENV`); its error names the variables to unset.
Cache and progress settings remain usable. Git subprocesses disable
checkout hooks and fsmonitor. These guards do **not** authenticate the remote or the pulled commit.
By default the remote's build code and, for installer-managed installs, its `install.sh` still execute after a
pull, and a clean checkout does not prove its contents are safe. To authenticate the target, turn on the signed-update check.

### Signed updates (opt-in)

`update.require_signed` in `$EXO_HOME/config.json` (default `false`; set it with
`python -m exo.config set update.require_signed true`). When true, `exo update` fetches, then verifies the
upstream tip with `git verify-commit` (SSH signatures, `ssh-keygen`) against a **pinned** signers file,
`$EXO_HOME/update/allowed_signers`. Only if the signature is good does it run `git merge --ff-only <that sha>`
and continue; otherwise it stops before changing the tree. It applies to checkout and installer installs, not
`uv tool` installs. The project does not sign releases yet, so leave it off until it does.

Only SSH signatures count. An OpenPGP or X.509 signature is refused whatever your gpg keyring holds, and so is
GitHub's signature on a merge made in its web interface. If `config.json` exists but cannot be read (invalid
JSON, or written by a newer Exo), or `update.require_signed` is not `true` or `false`, `exo update` refuses until
you fix it. A missing `config.json` means the check is off. `exo update --dry-run` says whether the check is on.

The pin is separate from the repo on purpose: a new commit that edits `trust/allowed_signers` cannot make
itself trusted. You create it once with `exo update --pin-trust`, which copies `trust/allowed_signers` from
`HEAD` (the code you already run) and prints it; read it before relying on it. After a key rotation, the new
list arrives in a commit signed by a key you already pinned. Fetch, review `git show @{u}:trust/allowed_signers`,
and run `exo update --pin-trust @{u}`. Until then, updates signed only by the new key are refused. Once a pin
exists, `--pin-trust REV` finds the commit that last changed the list in REV's first-parent history (the merge,
if the list came in through one) and takes the list only if that commit verifies against the current pin.
Otherwise it refuses and the pin stays as it was. A list that is not UTF-8 is refused too. Pinning the list you
already have always works. If a pinned key is lost or compromised, edit `$EXO_HOME/update/allowed_signers` by
hand. That is the only override. The check cannot tell an old list from a new one: a list that a pinned key
signed earlier can be pinned again, so read what `--pin-trust` prints. Tag signatures are not consulted: the tip
commit must be signed.

The gate treats `$EXO_HOME/update` as part of Exo's store. A tool call that writes, moves or deletes anything
there, or runs `exo update --pin-trust`, always asks you, and only you can answer. Reading the pin with `cat`,
`grep` or a file tool needs no approval. The gate reads commands, so it has limits. `docs/GATE.md` "The store is
credentials" lists them.

## What it does

| Step | Action |
|---|---|
| 1 | Checks the OS (macOS or Linux), `git`, and Python 3.11 or newer. It picks the newest of `python3.14` to `python3.11`, or `--python`. Hermes supports Python below 3.15. |
| 2 | Creates `$EXO_HOME` (default `~/.exo`, mode 700) with `keys/`, `principals/` and `bin/`. |
| 3 | Only with `--with-hermes`. Pins Hermes with `scripts/pin-hermes.sh` into `$EXO_HOME/hermes`: the latest stable tag, or `--tag`. If a patch does not apply to that tag, the installer skips it, warns, and retries. `PINNED` and `config.json` (`hermes_patch_skip`) record the skip. `--strict-patches` fails instead. At the end it prints a warning list of every patch that did not apply and exits 1. |
| 4 | Creates a venv at `$EXO_HOME/venv` and pip installs the `exo` package with the terminal client (`exo[tui]`), then links `exo` into `~/.local/bin`. With `--with-hermes` it also installs the pinned Hermes checkout (editable). |
| 5 | Copies the Exo plugins (every `plugins/*/plugin.yaml`, or `--plugins`) into `$EXO_HOME/plugins/`, where the standalone core discovers them, without tests. With `--with-hermes`, `--hermes-home` or `EXO_HERMES_HOME`, it also copies them into `<HERMES_HOME>/plugins/`. Each copy carries a `.exo-managed` marker. A plugin folder without the marker is never overwritten. It also copies the skill pack (`skills/` with `MANIFEST.json`) into `$EXO_HOME/skills/`, where the core's skill index and exo-orchestra find it. `/intro` needs its `exo-intro` skill. Each pack skill is its own marked folder, so a skill you add there is kept, and a folder of yours with a pack skill's name is left alone with a warning (yours wins). An unmarked exact copy of a pack skill, say from copying `skills/` by hand, is adopted as the pack's. A link is never replaced: a `skills` folder, skill or `MANIFEST.json` that links into the checkout keeps the pack live there and is left alone. A skill the pack no longer has is removed only when its marker names it. A copy of a pack skill carries the marker of the skill it came from, so a copy or a renamed pack skill is yours: it is kept, with a warning (delete its `.exo-managed` to stop the warning). The pack's skill folders belong to Exo. A re-run or `exo update` replaces an edit made in one, and warns when the `SKILL.md` it replaces was edited. To change a pack skill, copy its folder under a new folder name and set a new `name:` in the copy's `SKILL.md` frontmatter. Skills are found by that name, and in `$EXO_HOME/skills` the first folder in sorted order wins. So a copy that keeps `name: council` either hides the pack's `council` or stays hidden behind it. |
| 6 | Only with `--with-hermes`. Writes `<HERMES_HOME>/config.yaml` only if it does not exist: `plugins.enabled`, `memory.provider: exo-memory`, `context.engine: exo-context`. An existing file is left alone and the lines to add are printed. |
| 7 | Writes `$EXO_HOME/env.sh` and `$EXO_HOME/config.json`. With `--with-hermes` it also writes `$EXO_HOME/bin/exo-hermes` (runs the pinned Hermes with Exo's `HERMES_HOME`). |
| 8 | Offers `exo init <id> --interview`. With `--principal ID` it runs without asking. Add `--answers FILE` for a scripted interview. With `--no-interview`, or without a terminal, it is skipped: memory stays off, with one quiet notice, until you run `exo init <id>`. |

## Where things go

| Path | Holds |
|---|---|
| `$EXO_HOME/hermes` | the pinned Hermes checkout (owned by `pin-hermes.sh`) |
| `$EXO_HOME/venv` | Python for Hermes and Exo |
| `$EXO_HOME/plugins` | plugins discovered by the standalone core |
| `$EXO_HOME/skills` | the skill pack (marked folders and `MANIFEST.json`) beside skills you add yourself |
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
| `--plugins LIST` | comma list, for example `exo-gate,exo-memory,exo-context`. Remembered for re-runs and `exo update`. `all` means every plugin. Removes the other plugins an earlier run installed in `$EXO_HOME/plugins`, each only when its `.exo-managed` names that folder. A copy of a plugin carries the marker of the plugin it came from, so it is yours and is kept, with a warning. A marker from an older install names no folder, so Exo cannot tell that plugin from a copy: it is kept and still loads, and a warning names it. Delete it yourself if Exo installed it. Always keeps `exo-gate`: without it the core refuses every tool call. A name given here that matches no plugin stops the installer before it removes a plugin or writes config.json (the check runs after the venv and pip steps). A remembered name only warns. |
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
- Idempotent. A second run re-checks: plugins and pack skills that match are left alone, an existing store is
  not re-created, the venv is reused, and pip is skipped when the pinned tree and the exo commit
  have not changed.
- `pin-hermes.sh` has its own guards (see `PINNING.md`).

The core refuses tool execution if no approval callback is registered, or if an approval callback fails.
Re-run the installer to restore missing plugins or skills. If `core_plugins` or a seat's plugins list limits what loads,
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
