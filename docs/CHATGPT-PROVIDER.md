# Use a ChatGPT subscription

The `chatgpt` provider connects an eligible ChatGPT plan to Exo. Open **Providers** and choose
**Continue with ChatGPT**. Complete sign-in and consent in your system browser, then return to Exo.
Choose an account and one of its available models before changing your conversation's model.

For a source install, include the `chatgpt` extra: `uv sync --extra tui --extra chatgpt`. Desktop builds
that offer this connection must include that extra. The base Exo installation keeps its existing dependencies.

This connection uses OpenAI's [Sign in with ChatGPT for open-source apps](https://developers.openai.com/siwc/token-sharing-open-source).
It has its own Exo registration and does not read or replace a Codex or Hermes login. It does not import
ChatGPT conversations. Existing Exo sessions and memory stay in the shared Exo home.

Connection management is available in the desktop and web Providers view attached to the core. Direct
standalone CLI router commands, including `exo models --remote chatgpt`, do not yet attach this account
connection. Use the app's account model list for this provider.

## Accounts and plan usage

Each registration has its own issued client ID and verified identity. Registrations stay separate even
when their email addresses match. Adding or reconnecting an account leaves the active connection in place
until the new sign-in is validated. Cancel a pending sign-in to keep the current connection.

The model list comes from the selected account. Models that account cannot use are not offered as
subscription routes. A new connection does not choose a model or change a saved role automatically.

**Manage usage** opens [ChatGPT usage settings](https://chatgpt.com/settings/usage). Your account and workspace
determine eligibility and available allowance. A limit may apply to Exo's access or to your plan. Exo does
not infer a reset time or report a token allowance as a dollar balance.

If a turn selects ChatGPT plan usage, a missing connection or failed request does not silently continue
through an API-key provider in its fallback chain. Choose another provider explicitly if you want to use it.
An OpenAI API key remains a separate provider with separate billing.

## Credential handling

Access, refresh and identity tokens stay in protected local storage. The browser receives connection
status and account labels, never those tokens. Tokens do not enter Exo messages, configuration, events,
request URLs, diagnostics or the model context. Sign-in uses a temporary loopback callback and fresh
state, nonce and a PKCE verifier. PKCE binds the returned authorization code to the sign-in attempt.

On macOS, token bundles use the native Security framework for writes, reads and deletion with the same Python application identity. These operations refuse interactive authorization instead of opening a permission prompt. They do not pass through the `security -i`
[interactive parser](https://github.com/apple-oss-distributions/Security/blob/main/SecurityTool/macOS/security.c), whose line limit can split a large OAuth bundle. Tokens stay out of process arguments
and temporary files. A failed save leaves the previous connection unchanged. The callback gives a safe
failure message and directs you back to Providers to retry.

New macOS grants use a versioned native account namespace. Reconnect an older registration to migrate it.
The old item and registration remain unchanged if sign-in fails. Exo does not automatically read or delete
legacy CLI-created orphan items, or weaken their access policy. Linux Secret Service accounts retain their
existing namespace. A connection from another OS store requires sign-in on the current system.

Exo verifies the identity token's signature, issuer, audience, expiry and nonce before accepting it.
Permission to use the ChatGPT plan comes from the granted scopes, not from identity alone. A sign-in
without plan permission remains identifiable but cannot serve model requests.

Refreshes for a connection are serialized across processes. Exo saves a rotated refresh token together
with its replacement access token. Temporary network failures preserve the connection. A confirmed
unusable refresh token requires sign-in again.

Disconnect stops local use and attempts to revoke the renewable session. If remote revocation cannot
be confirmed, Exo reports that result. You can also disconnect the registration in ChatGPT settings.

This is local credential protection. Programs running with your user account and a trusted shell can
still exercise your account's authority; this provider does not add operating-system isolation.

## Transport and validation

The subscription route uses `POST https://api.openai.com/v1/responses`, with `store: false` and
`stream: true`. Exo sends the history required for each request, including local tool results. It follows
the subscription route's supported fields and function-tool format. It does not use ChatGPT backend
endpoints or treat a terminal failure as successful completion.

The existing protected model-spend contract supports its pinned Anthropic and Chat Completions
transports. It refuses Responses routes until that contract supports them separately. Adding a
subscription connection does not bypass a principal's spending controls or treat an unknown cost as zero.

Automated tests use disposable accounts, signed fixture tokens and HTTP fixtures. A real acceptance run
requires browser sign-in, account-specific model discovery, one streamed response, and a local tool
continuation. Passing fixture tests alone does not establish account eligibility or live provider access.

References: [registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in),
[accounts and token refresh](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions),
[models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference),
[errors](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery), and
[preview limits](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations).
