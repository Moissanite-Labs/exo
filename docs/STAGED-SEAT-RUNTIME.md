# Stage a seat runtime without switching the running core

Create an isolated worktree at the core version to preserve. Apply the reviewed source change there and create its own environment with `uv sync --frozen --extra tui --extra mcp`. Run focused tests against that environment. Record the base commit, staged commit and test results before arranging a handoff.

`scripts/run-seat-runtime.sh` is a manual TUI launcher template. Its caller supplies `EXO_RUNTIME_EXO`, `EXO_SEAT`, `EXO_HOME`, `EXO_PLUGIN_DIRS` and any existing bus settings. Supply `EXO_RESTART_LAUNCHER` as the caller's own path so `/restart` reopens the selected runtime. Keep machine paths and bus settings in the local wrapper, outside the repository.

Creating a wrapper does not switch a running core. The core may keep serving the selected seat from its old runtime until an explicit stop and start. Do not launch the staged wrapper to apply an upgrade while the user is working. Keep the existing launcher and runtime for recovery. After an authorized idle handoff, verify the new core version and seat identity before testing real credentials.

The template accepts a directory, flags, `resume`, or `restart`. The restart command opens a new terminal on macOS and passes the command as an AppleScript argument. It does not stop a core. Credential configuration uses the selected runtime's `exo github-auth` CLI separately. Staging and fake credential tests do not provision a GitHub account or authenticate one.
