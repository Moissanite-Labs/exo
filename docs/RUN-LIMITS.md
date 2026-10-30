# Principal controls for core-wide run limits

`/limits` reports the policy for all conversations and delegated workers on the connected core. This policy lives in memory and resets when that core restarts. It changes new work. Running turns and workers keep the limits they had when admitted.

The command works in the terminal and web composer. Both probe support before sending a change. An older core receives no mutation, and the command is never sent to a model as a chat message.

| Command | Effect |
| --- | --- |
| `/limits` | Show scope, current overrides, configured defaults and effective limits. |
| `/limits rounds off` | Remove the tool-round cap for new main turns and new delegated workers. `0` also means off. |
| `/limits rounds 60` | Use 60 tool rounds for new turns and workers. |
| `/limits rounds default` | Clear the core rounds override. Main turns use their window/per-turn choice or configured default. Workers use their configured default. |
| `/limits worker-timeout 15m` | Give newly started workers a 900-second wall deadline. Seconds, `s`, `m` and `h` units are accepted. |
| `/limits worker-timeout off` | Remove only the wall deadline for new workers. |
| `/limits worker-timeout default` | Restore the configured worker deadline, normally 900 seconds. |
| `/limits default` | Clear both overrides. |

The optional suffix `all` is accepted after a field and value, for example `/limits rounds off all`. The scope is always the whole connected core. Values are bounded nonnegative integers. Negative, fractional, nonfinite and overflowing values are refused.

## Defaults, precedence and admission

Without an override, main turns retain the existing default of 24 rounds and workers retain their configured round cap and 900-second deadline. These are defaults, not a new policy to overwrite existing configuration.

A core rounds override takes precedence over `/rounds` or `--max-rounds` in a window. Clearing it restores those window/per-turn choices. `/rounds` itself still changes only that window. It does not configure workers.

The server takes an immutable rounds snapshot when a main turn is admitted. A delegated worker takes its own snapshot after its concurrency wait, immediately before its loop is created. A queued worker therefore uses the policy in effect when it starts. A policy change never extends or shortens a running worker's timer or mutates a running turn's loop.

The worker wall deadline covers the entire child loop, including approval waits. There is no new wall deadline for main turns. `off` for rounds does not turn off the worker deadline. `off` for the worker deadline does not remove the round cap.

## Authority and independent limits

Only a live principal connection can change this policy. Local automation, model/tool descendants, delegated agents, peers, channels and expired principal contexts cannot set it. Status is read-only metadata. No model-facing tool gains a new policy argument.

Token budgets, depth and concurrency limits, permission decisions, required executors and model spending controls remain independent. Removing rounds and wall limits does not remove those boundaries or select a different model. Approval requests are not answered by this command.

The core stores no policy in `config.json` or the session database. After restart, use `/limits` to inspect defaults and opt in again if needed. This is not an operating-system sandbox for trusted shell programs.

## Implementation and verification

`core/run_limits.py` owns the immutable process-local policy. The engine supplies the admitted main-turn limit. The delegator records the effective worker limits in its brief and honors zero explicitly instead of replacing it with 24. The `limits` built-in enforces principal authority before changing policy.

Disposable tests cover a child beyond 24 rounds, queued-worker admission, running-work snapshots, default restoration, strict parsing, expired/untrusted authority, independent token/depth/concurrency limits, worker deadline removal and client capability refusal. They use fake providers. No paid inference or live installation is required by these checks.
