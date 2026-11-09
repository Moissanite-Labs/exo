# CLI canary refresh, November 9

This candidate preserves the existing canary capabilities and adds the reviewed Anthropic thinking replay repair. Shared changes remain owned by the canonical Exo repository.

## Sources and behavior

- Existing canary: `daab39b65d3319f445136fec97577c67d78734c3`.
- Current main: `e021f76fcf0499ddc78eaa0953b6de1152b1c673`. Its hardening tree matches the already carried reviewed hardening composition. Preserve the existing web changes when reconciling squash ancestry.
- Original Anthropic repair: `3db9a180f9f51d21669f9b2a8af95cf2003cefc2`, by Razi. Reviewed guard follow-up: `8838dad27dc0390108727ec52a8fb6d032dd5515`, by gpt-forge. Producing-model metadata, first-party mismatch controls and omission of unsupported replayed reasoning preserve saved history and tool pairs.
- Carry the previously reviewed synchronous Pad storage callback and unused-import corrections. The original follow-up operator was gpt-forge, as recorded in the canonical PRs.
- The CLI and production web assets are unchanged from the existing canary. Unfinished exploration work remains isolated and is not activated.

## Evidence and activation boundary

The combined command-plugin, Pad, private-home, Anthropic, continuation, delegation, worker-model and unlimited-round checks passed 1,037 tests in 213 seconds on runtime composition `75dcf09b`. Whole-tree Ruff passed. Independent provider review passed 70 checks, with 36 additional overlapping composition checks.

Activate only after idle checks and private backups. Preserve conversation records, selected models, effective worker routes and their revision, existing access scope, unlimited rounds and saved configuration. Load the already saved worker token limit through the new process. Keep the existing CLI client attached and leave the desktop closed. Verify the reconnect, configuration, old messages and working artifacts after activation. Never restore an older database over newer work.

Local tests, independent review, installed activation, canonical PR publication, hosted CI and main merges remain separate evidence. Private operational receipts hold session identifiers and working-file locations and are not committed here.
