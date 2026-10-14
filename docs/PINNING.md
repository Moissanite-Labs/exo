# Pinning Hermes

Exo runs on upstream Hermes Agent, not on a fork (D-005). We pin one release tag, carry at most five
core patches on top, and check the plugin contracts before every move. This page covers the three
parts: the pin, the patch queue, and the rollout.

## 1. The pin

`scripts/pin-hermes.sh [TAG]` owns one Hermes checkout.

| Variable | Default | Meaning |
|---|---|---|
| `EXO_HOME` | `~/.exo` | Exo's home |
| `EXO_HERMES_DIR` | `$EXO_HOME/hermes` | the checkout the script owns |
| `EXO_HERMES_REPO` | `https://github.com/NousResearch/hermes-agent` | upstream (a mirror works too) |
| `EXO_PATCH_DIR` | `<exo repo>/patches` | the patch queue |
| `EXO_HERMES_TAG` | latest stable | tag to pin when no argument is given |
| `EXO_PATCH_SKIP` | empty | canary only: patches to leave out, for example `0001,0004` |

What it does:

1. Picks the tag. With no argument it reads `git ls-remote --tags` and takes the highest plain
   `vX.Y.Z` tag. Release candidates, canary builds and suffixed tags are ignored. Hermes moved from
   calendar tags (`v2026.9.24`) to semver (`v0.21.6`); semver wins, calendar tags are the fallback.
2. Creates the checkout if needed, marked with `git config exo.managed true`. It refuses a non-empty
   folder that it did not create, and any path inside `~/.hermes` or `$HERMES_HOME`. Your live
   Hermes install is never touched.
3. Fetches only the tag, shallow.
4. Dry run: applies every patch in order in a temporary worktree. If one fails, it stops, prints the
   rejected files and leaves the checkout exactly as it was.
5. Real run: moves the checkout to the tag, applies the same patches, and checks that the patched
   tree matches the dry run.
6. Writes `PINNED` in the checkout: tag, tag commit, patched tree SHA, and each patch with its
   SHA-256 (or `skipped`).

It is idempotent. If `PINNED` already matches the tag, the patch files and the tree on disk, it exits
without doing anything. Untracked files that are git-ignored, such as a `venv/` inside the checkout,
survive a re-pin.

Example:

```sh
scripts/pin-hermes.sh                 # latest stable tag, full queue
scripts/pin-hermes.sh v0.21.6         # a specific tag
EXO_HERMES_DIR=/tmp/try scripts/pin-hermes.sh   # a throwaway checkout
cat ~/.exo/hermes/PINNED
```

## 2. The patch queue

See `patches/README.md` for the live list and its status. The rules:

- At most five patches. Each is also an open upstream PR.
- Patches apply with plain `git apply`. No fuzz, no 3-way merge. A patch that does not apply blocks
  the pin until it is rebased onto the tag.
- A patch leaves the queue when its PR merges into a tag we pin, or when a hook replaces it.
- New needs go upstream as small hook PRs first (like `0005-aux-routing-hook.SPEC.md`). Exo then
  uses the hook from a plugin, which needs no patch at all.

## 3. Contract tests

`tests/contract/` checks that the plugin surfaces Exo uses still exist on a checkout:
`MemoryProvider` and `ContextEngine` methods, the `PluginContext.register_*` functions, the hook
names in Hermes's `hermes_cli.plugins.VALID_HOOKS` and the middleware kinds. They also fail if either
base class gains an abstract method Exo does not implement. Without `EXO_HERMES_DIR` they skip.

Run them with a Python that has Hermes's dependencies, and keep Hermes away from any real home:

```sh
T=$(mktemp -d)
EXO_HERMES_DIR=~/.exo/hermes PYTHONPATH=~/.exo/hermes \
HOME=$T/home HERMES_HOME=$T/hermes-home \
  <hermes venv>/bin/python -m pytest --confcutdir tests/contract tests/contract
```

The tests check that Hermes was imported from `EXO_HERMES_DIR`, so a different install on the path
cannot pass for the pinned one.

## 4. Moving the pin

Move weekly, not daily. Upstream ships hundreds of commits a day; a weekly tag gives a stable target.

1. Pin the new tag into a throwaway folder: `EXO_HERMES_DIR=/tmp/exo-next scripts/pin-hermes.sh vX.Y.Z`.
2. If a patch fails: check whether its PR merged in this tag (then delete the patch), or rebase it.
   Do not move the pin with a patch silently missing.
3. Run the contract tests against the throwaway checkout. Any failure stops the move. Fix the Exo
   plugin, or raise the break upstream.
4. Run the Exo plugins' own tests against it.
5. Then roll out in stages.

## 5. Rollout: canary, then team profiles, then root

Hermes profiles share code but not state. Each profile's launcher (service unit, launchd job, shell
alias or desktop setting) decides which Hermes checkout runs it. Rollout means moving that choice one
stage at a time. The old checkout stays in place, so rollback is pointing the launcher back.

1. **Canary profile.** One profile with no important work, for example `canary`. Run it from the new
   checkout (`<checkout>/venv/bin/hermes -p canary`). Use it for at least a day: gateway, cron, one
   kanban task, one delegation, a memory write, a compaction. Watch errors and cost.
   `EXO_PATCH_SKIP` is allowed here and only here, while a patch is being rebased. `PINNED` shows
   what was skipped.
2. **Team profiles.** The working agent profiles (for A, the Protean team profiles). Move them
   together after the canary is clean. Watch one full day of lanes: stalled subagents, rate-limit
   recovery, model pins.
3. **Root profile.** The principal's own profile last. Only with the full patch queue, no skips.

At each stage, roll back by pointing the launcher at the previous checkout. Keep the previous
checkout until the next weekly move.

## 6. For a new user

Nothing here assumes one person (D-004). The defaults put everything under `~/.exo`. A new user runs
`scripts/pin-hermes.sh`, creates a venv inside the checkout using upstream's install steps, runs the
contract tests, and starts with a canary profile like anyone else.
