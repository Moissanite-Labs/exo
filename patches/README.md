# Exo patch queue

Exo runs upstream Hermes Agent pinned to a release tag (D-005). This folder holds the few core
patches Exo carries on top of that tag. `scripts/pin-hermes.sh` applies them in file-name order.

## Rules

- At most 5 patches. The pin script refuses a sixth.
- Each patch is also an open upstream PR. No private core changes.
- A patch is dropped when its PR merges into a tag we pin, or when a plugin hook replaces it.
- A patch must apply cleanly to the pinned tag with `git apply`. No fuzz, no 3-way merge.
  If it does not apply, the pin script refuses and the old pin stays live.
- Each file starts with a short header (PR URL, state, head SHA). `git apply` ignores text
  before the first `diff --git` line, so the header is safe.

## Queue (captured 2026-10-11)

Target tag tested: **v0.21.6** (latest stable, commit `818c13be1dc4`). Each patch was checked alone
with `git apply --check` in a temporary worktree of a clean v0.21.6 checkout. The same check was run
against upstream `main` at `1fd18277267` for comparison.

| File | PR | State | Head SHA | Files | v0.21.6 | main |
|---|---|---|---|---|---|---|
| `0001-sole-credential-reprobe.patch` | [#119652](https://github.com/NousResearch/hermes-agent/pull/119652) | open | `07ed67bff1a8a02f93a899c51e9f65579e9f0f61` | 54 (16 new) | **fails** (23 hunks rejected) | clean |
| `0002-session-override-persistence.patch` | [#98901](https://github.com/NousResearch/hermes-agent/pull/98901) | open, GitHub says conflicting | `c46d0ea740972fd83bf8dcd8c946b34295becfd1` | 14 (4 new) | **fails** (3 hunks rejected) | fails |
| `0003-goal-barrier-release.patch` | [#107130](https://github.com/NousResearch/hermes-agent/pull/107130) | open | `20ff5f1852bb987cb771a6cb162a2772cff7e5e8` | 6 | **clean** | clean |
| `0004-skill-ledger-reversible.patch` | [#110011](https://github.com/NousResearch/hermes-agent/pull/110011) | open | `f1439bf1b300ca8f6eac2695bea108b1fa9275d0` | 8 (2 new) | **fails** (10 hunks rejected) | clean |
| `0005-aux-routing-hook.SPEC.md` | not opened yet | spec only | n/a | n/a | n/a | n/a |

0003 was also verified by its own tests on the pinned tree: 48 passed
(`tests/hermes_cli/test_goals.py`, `tests/hermes_cli/test_cli_goal_parked_resume.py`).

### Files touched

**0001 #119652** re-probe a lone rate-limited credential, plus ten pool and fallback fixes.
Core: `agent/agent_init.py`, `agent/agent_runtime_helpers.py`, `agent/agent_runtime_pool.py`,
`agent/anthropic_credentials.py`, `agent/auxiliary_client.py`, `agent/auxiliary_model_scope.py`,
`agent/auxiliary_plugin_oauth.py`, `agent/auxiliary_unavailable.py`, `agent/chat_completion_helpers.py`,
`agent/chat_completion_helpers_pool.py`, `agent/credential_pool.py`, `agent/credential_pool_admin.py`,
`agent/credential_pool_cooldowns.py`, `agent/credential_pool_model_cooldowns.py`, `agent/error_surface.py`,
`agent/fallback_cooldown.py`, `agent/turn_recovery.py`, `hermes_cli/auth.py`, `hermes_cli/auth_codex.py`,
`hermes_cli/auth_commands.py`, `hermes_cli/auth_cooldown.py`, `hermes_cli/auth_oauth_grants.py`,
`hermes_cli/cli_agent_setup_mixin.py`, `hermes_cli/inventory.py`, `hermes_cli/model_switch_providers.py`,
`plugins/image_gen/openai-codex/__init__.py`, `run_agent.py`, plus 27 test files.
Rejected on v0.21.6: `agent/auxiliary_client.py`, `agent/credential_pool.py`,
`agent/credential_pool_model_cooldowns.py`, `agent/fallback_cooldown.py`, `hermes_cli/auth.py`,
`hermes_cli/auth_commands.py`, `plugins/image_gen/openai-codex/__init__.py`,
`tests/agent/test_auxiliary_client.py`. The PR is built on newer `main`, so it waits for the next tag
or a rebase onto the tag.

**0002 #98901** session model and reasoning overrides survive idle reaping.
`gateway/run_config_loaders.py`, `gateway/session.py`, `gateway/slash_commands_session.py`,
`tui_gateway/contracts/config_free_tier_control.py`, `tui_gateway/methods_config.py`,
`tui_gateway/methods_config_set.py`, `tui_gateway/methods_session.py`, `tui_gateway/server.py`,
`apps/shared/src/gateway-contract.generated.ts`, `apps/shared/src/gateway-contract.openrpc.json`,
plus 4 test files. Rejected on v0.21.6: `gateway/session.py`, `tui_gateway/methods_config.py`,
`tui_gateway/methods_config_set.py`. It also fails on `main`: the PR branch needs a real rebase.

**0003 #107130** release a stalled-subagent wait barrier.
`cli.py`, `hermes_cli/cli_loops_mixin.py`, `hermes_cli/config_defaults.py`, `hermes_cli/goals.py`,
plus 2 test files. Applies cleanly.

**0004 #110011** reversible, complete skill ledger.
`hermes_cli/curator.py`, `tools/file_tools.py`, `tools/skill_ledger.py`, `tools/skill_manager_tool.py`,
`tools/skills_hub_install.py`, plus 3 test files. Rejected on v0.21.6: `tools/skill_ledger.py`,
`tools/skill_manager_tool.py`, `tools/skills_hub_install.py`. Clean on `main`, so it lands with the
next tag or after a rebase.

## What this means today

With the full queue, `pin-hermes.sh` refuses v0.21.6. That is the intended behaviour. For the canary
only, `EXO_PATCH_SKIP=0001,0002,0004` pins v0.21.6 with 0003, and PINNED records the skipped patches.
Next steps, in order of value:

1. Rebase 0002 on `main` upstream (it conflicts there too), then rebuild it on the tag.
2. When the next stable tag ships, re-run the check. 0001 and 0004 apply to `main` today, so they
   should apply to the next tag without work.
3. Open the 0005 hook PR upstream (see the spec). If it merges, part of Exo's routing work leaves
   the queue for good.

## Refreshing a patch

```sh
gh pr view <n> -R NousResearch/hermes-agent --json url,state,headRefOid
gh pr diff <n> -R NousResearch/hermes-agent > /tmp/pr.diff
# keep the header lines, replace the diff body, then:
EXO_HERMES_DIR=/tmp/exo-pin-test scripts/pin-hermes.sh <tag>
```

If a PR does not apply to the tag, rebase the PR branch on the tag in a scratch clone and store that
diff here instead. Note the base tag in the header.

## Optional gateway undo notification (#537)

`0006-gateway-undo-notice.patch` is an unapplied Exo integration proposal. Its provenance JSON records the exact source and method hashes; the reviewed archive has no Git metadata. It adds an optional `exo.host_undo.report(session)` before cached-agent eviction, while the old listener can still record the surviving window. A fresh context engine consumes that record on the next turn. Missing Exo or a failing optional hook does not prevent the host undo.

The tests apply the shipped patch to the recorded method fixture and execute the callback with isolated context state. An unpatched control restores the removed ruling; the patched flow drops it. No installed host was changed. Applying/upstreaming this host patch and native host acceptance remain pending. The existing handoff file rewrite limit in #537 is not repaired here.
