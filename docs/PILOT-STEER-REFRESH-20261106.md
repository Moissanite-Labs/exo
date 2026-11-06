# Delegate recovery and human steering pilot

Capability ID: `exo.delegation.parent-continuity`.

This Exo pilot keeps delegated work bounded and restores automatic parent continuation for new work after a person's steering message has been consumed. It preserves fences against older workers taking over the next turn.

The composition extends alpha `aaa020d741385aa81c6114f3ff654b9415c93150`. It does not consolidate the alpha feature stack into canonical main.

| Adaptation | Canonical change | Responsibility preserved |
| --- | --- | --- |
| Return a token-exhausted child's original failed receipt without an automatic higher-tier retry | [Budget retry fix](https://github.com/Moissanite-Labs/exo/pull/1223), `c68408cd87a070a78e3d6b9d5983ef14e7f24661` | Surface resource exhaustion. Ordinary failure escalation still works. |
| Make discovery guidance respect offline, no-network and no-paid-discovery task constraints | [Discovery guidance fix](https://github.com/Moissanite-Labs/exo/pull/1224), `1bbf603e39d19792b945d46040f116efc9ad3b7b` | Use local reading when hosted discovery is outside the task. Avoid repeating failed discovery. |
| Restore continuation ownership after consumed principal steering while preserving older-worker fences | [Steering ownership fix](https://github.com/Moissanite-Labs/exo/pull/1225), `77d791c090eebbb0aeaf7a6ceea8cd856d5e89dc` | Let new work resume its parent after steering without reviving older workers' authority. |

## Preserved behavior and peer review

The composition retains background delegation and task controls, unlimited rounds, worker model settings, Anthropic support, continuation behavior and UI code.

The only semantic conflict resolution combines the budget exception with the existing delegate tool instructions for background notices and stopping tasks. A test conflict retains both sets of added regressions.

The delegation runner, continuation controller, core loop, parent engine and tool guidance must work together. Fake providers and isolated stores exercise their shared behavior. An independent steward reviewed the canonical changes. Review of this exact composition remains required before commit or activation.

## Local composition evidence

The isolated Python 3.11.15 environment uses unchanged `uv.lock`, frozen offline resolution, and development, terminal, ChatGPT, bus and MCP extras. It includes `certifi==2026.10.23`.

- 223 tests passed for delegation, background tasks, receipt resume, continuation wake and steering, unlimited rounds, worker settings and terminal controls, Anthropic transport/discovery/spend, and code discovery.
- 26 retained-feature tests passed for browser worker controls, worker routes, web lifecycle, agent UI, approvals and terminal session resume.
- Ruff passed for changed Python files. `git diff --check` passed.

## Limits and continuity

These checks establish local source composition. They do not establish live installation, hosted CI, native desktop acceptance or recovery in an active session.

Discovery restrictions are model guidance, not enforced network policy. Token accounting and configured limits remain unchanged. Stopping an automatic budget retry does not repair high token consumption itself. Receipt ownership remains process-local, as in the alpha base.

The tests retain three lessons: surface resource exhaustion, honor offline constraints before discovery guidance, and give new work continuation ownership after steering while keeping older work fenced.

Next steps are exact-composition review, a reviewed commit, and the separately authorized idle refresh with rollback available. Keep the previous source and runtime receipt so a successor can distinguish source verification from live activation.
