# Secure credential setup

The web client's Credentials view stores write-only secrets for the selected core's actual data home and logical seat. The view shows that seat, such as `canary`; a conversational name such as Razi does not select a different credential scope.

This feature needs an upgraded core. The page probes its credential capability before forwarding any secret. An older or unreachable core cannot save a credential through this view. Replacing web assets alone does not upgrade the core.

## Enter a credential

Create the separate GitHub account and token yourself, then use **Credentials → Separate GitHub account**. Save or rotate its token, run the read-only account test, or disable the binding. The account test reports a GitHub login, never a token or upstream error body. Account creation and real credential entry are user actions.

For a tool API key, choose a service name, an `EXO_SECRET_` variable name and the allowed consumers: `bash`, `jobs`, or both. For example, a weather service can bind `EXO_SECRET_WEATHER` to `bash`. The agent receives a nonsecret catalog of allowed variable names in its instructions and can use that variable in an approved shell command. Provider processes and other tools do not receive it automatically. Model provider keys remain a separate feature in Providers.

The credential field is masked, is cleared on submission and navigation, and is not placed in chat, URLs, browser storage or command-line arguments. **Check storage** for a tool key only checks that the Keychain item is readable. It does not validate a remote API account or accept an arbitrary network destination.

## Storage and rotation

This secure view uses macOS Keychain explicitly. A missing, disabled, locked or refusing Keychain fails closed. It never silently writes a plaintext file or offers the CLI's insecure login mode. The existing Keychain writer sends the value to `security -i` through standard input, not through process arguments. Keychain's normal macOS access policy still applies; a data-home/seat namespace is not a separate OS account or a sandbox against another trusted process running as the same user.

Only nonsecret binding metadata is saved in the selected core's data home. Keychain account names include a hash of the resolved data home, logical seat and service. A metadata list does not read or unlock the Keychain and does not prove that a credential is currently usable. Use an explicit test when needed.

Secrets are resolved when an allowed subprocess starts. Rotation affects the next spawn; an existing child retains its original environment until it exits. `tools.env_deny` wins. Inherited `EXO_SECRET_` values are cleared before applying explicit bindings, so removing a binding or excluding a consumer cannot fall back to a host value.

Removal disables the binding before attempting Keychain deletion. If deletion fails, the UI reports pending cleanup rather than claiming the secret was deleted. Retry removal after resolving the Keychain problem. An already absent item counts as removed. GitHub mode, host or store changes require removing the previous binding and confirming cleanup first; replacing the token in the same Keychain item is rotation.

## Authority and boundaries

Credential HTTP requests require a loopback peer, loopback Host, same-origin browser request and the current page token. The selected core independently requires principal authority before parsing the credential payload or accessing a store. TCP and channel callers cannot gain that authority by supplying a label. The core derives home and seat itself; a browser cannot select another scope in a request body.

Credential setup uses a guarded `command.run` request, never a model turn or tool argument. The handler emits no credential event and normalizes parser, store and subprocess failures. Saved values are never returned by GET or status responses. Raw credential values are redacted from foreground output and from bound background jobs before their logs are written, including values split across reads.

The shell remains trusted. Code given a credential can use, transform or export it. Output redaction is not an exfiltration sandbox, and this feature does not expand tool permissions or bypass approval gates. The [GitHub credential guide](GITHUB-AUTH.md) describes the ordinary `gh` and HTTPS Git binding and its limits, including explicit command overrides and SSH.

For the next Canary handoff, keep GitHub explicitly disabled until the user configures the separate account's token. Staging a build is not activation or real account authentication. Do not change the running core or write its binding metadata as part of a source-only test.

## Implementation and verification

The implementation reuses Exo's provider setup, private-file, principal transport, environment policy and redaction code. No upstream harness source is copied. Tests use fake credentials, fake Keychain calls and disposable homes and browsers. They cover store failures, authority, scope, consumer selection, revocation, output redaction, write-only responses, unsupported cores and clearing an unsaved browser field on navigation.
