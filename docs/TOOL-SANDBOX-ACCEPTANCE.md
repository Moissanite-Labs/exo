# Tool sandbox acceptance work

This is test-first work for [issue #31](https://github.com/aska-digital/exo/issues/31), not an installed sandbox or a change to the D-039 trust boundary. Same-user code outside the core's process tree can still read its launch record. Gate classification alone cannot provide filesystem or socket isolation.

`tests/security/test_permissions_bypass.py::test_detached_tool_cannot_use_fixture_principal_credential` exercises the missing detached-child case through a real disposable Unix server. A subprocess double-forks, verifies reparenting to pid 1, reads only that server's generated credential, and attempts a principal-only permissions command. The subprocess has bounded waits and an HTTP timeout. It touches no installed socket, credential, policy, or provider.

The expected failure is restricted to the observed escape: the response succeeds and the fixture engine enters bypass mode. Setup failures, unsupported reparenting, malformed replies, and unrelated errors fail normally. If the escape stops reproducing, strict XPASS forces review of the marker rather than silently retaining an obsolete claim. The adjacent attached-child test remains the denial control. Both probes now extract the token from the JSON launch record; the old attached-child test incorrectly submitted the entire record and therefore only proved that an invalid credential was refused.

## Remaining implementation and acceptance

The sandbox implementation and its platform choice remain open. It needs an independently reviewed executor boundary before this issue can close. A successful executor must demonstrate all of these against disposable fixtures on both supported operating systems:

| Surface | Required acceptance evidence |
| --- | --- |
| Core credential, launch record and socket | Direct child and detached child cannot read the record or connect to the privileged socket, even when its path is known. |
| Policy store, ledger and both anchors | Tool reads/writes follow the explicit mount policy; changing ledger and anchors together cannot bypass isolation. |
| Workspace execution | Pytest hooks, Git hooks, build backends and package lifecycle scripts may run only with the same sandbox restrictions as the initial tool. |
| Startup files | Shell/session rc files, service units, launch agents, direnv, Python `.pth`, tmux and cron writes are outside the writable workspace unless explicitly granted. |
| Child lifetime | Fork, double-fork and a child surviving its launching command retain restrictions; cancellation cleans up only the executor's own jobs. |
| Normal work | Allowed workspace reads/writes and declared tool execution still work; denial is visible to the caller. |

Before choosing Linux namespaces/Landlock or a macOS executor, specify writable roots, credential mounts, network access, process cleanup and the behavior when the sandbox is unavailable. Do not silently fall back to unrestricted execution. No choice is made by this draft. The detached regression is one demonstrated gap; it does not claim that every historical command example in #31 still reproduces on current main.
