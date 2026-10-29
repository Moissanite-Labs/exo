# Providers and keys

Exo works with any model provider. You choose which model serves each role.

Provider status URLs in `exo models`, `/providers` and the web Providers data replace URL logins
(`user:password@`) with `***@`. Invalid URLs show a generic label. Scheme, host and endpoint path stay visible
for ordinary URLs. Connections and local-server probes still use the original configured endpoint. This is
userinfo redaction, not a guarantee about secrets placed in URL paths, queries or fragments (#271, D-034).

## Set up with the app or the TUI

Pick one of three places. They share one engine (`exo/providers_setup.py`, D-034), so they show the same
status and store keys the same way.

- **Terminal:** `exo providers` lists every provider: status, where its key is (env, keychain,
  secret-service or file, never the value), model count and the default. Then:

      exo providers add openai        # prompts for the key, no echo; runs one test call after
      exo providers test openai       # one tiny live call: latency, or an error class
      exo providers remove openai     # deletes it from the Keychain, Secret Service and keys.env
      exo providers default openai:gpt-5   # default model; old routes stay as fallbacks

  `add` never takes the key as an argument (it would land in shell history and in `ps`) and needs a terminal.
- **TUI:** `/providers` opens a panel with the same list. `a` adds or replaces a key in a masked field, `t`
  tests, `r` removes, `d` sets the default model. The key never enters the transcript, history or session log.
- **Web client:** `exo web` opens the page with a one-time launch code that the page trades for the per-launch
  token. The Providers tab has the same list, a masked key field, Test, Remove and a default model picker.

Where a new key goes: the macOS Keychain (service `exo`) on a Mac; on Linux the Secret Service through
`secret-tool` when it is installed, else `~/.config/exo/keys.env` (written mode 0600, atomically). Choose another
with `exo providers add NAME --store file`. Saving reports both the destination and the effective lookup source.
Environment variables, then the Keychain/Secret Service, take precedence over the file. A save to a lower
store keeps higher credentials intact and warns when they still win. The warning offers an update command
or removal of only the higher item; `exo providers remove` removes every stored copy, including the new file key.
An environment override must be changed or unset in the launching shell, followed by restarting Exo.

Exo file saves and removals take an owner-only sibling `keys.env.lock` and atomically replace `keys.env` at mode
0600, preserving other keys when TUI and web writes overlap. Symlinked key or lock files are refused.

What the test call is: OpenAI-style and local providers list their models (`GET /models`), Anthropic lists one
model, a decision provider answers one yes or no question, `claude-cli` only checks the binary is on PATH, and
`muse-cli` runs one tiny `muse exec` prompt (see "Use a Muse Code subscription").
Errors come back as a class: `no_key`, `auth`, `billing`, `not_found`, `rate_limited`, `server`, `timeout`,
`network`, `not_running`, `not_installed`, `unsigned`, `bad_response`, `http`, `error`. The last result is kept in
`$EXO_HOME/state/providers.json` (no secrets) and shows as `verified` or `failed: <class>`; a new key clears it.

If no provider can serve the main role, `exo tui` and `exo web` say so and point here.

The default core uses real catalog routes; it never substitutes an offline reply for missing credentials.
Anthropic and OpenRouter use their catalog routes, and an available Claude CLI can use its catalog alias
(see "Use your Claude Code login"). For a provider without a known main model, such as OpenAI, first run opens
the model chooser after the key is added. Choose the model you want before `/intro` runs. Explicit roles and
local model settings are kept.
Orientation is remembered only after its own turn finishes with a successful, nonempty real assistant reply;
a provider error, cancellation or deliberately configured fake reply leaves it unfinished for retry.

### Security of the web route

`exo web` binds to loopback, but any web page you visit can still make your browser send requests to
`127.0.0.1`, and DNS rebinding can make a hostile name resolve to it. So every `/api/providers` route needs all
of these, or it answers 403:

1. the client is on loopback;
2. the `Host` header is `127.0.0.1`, `localhost` or `[::1]` (a rebinding name fails here);
3. when the browser sends `Origin`, it is `http://` and the same host and port, and `Sec-Fetch-Site`, when sent,
   is `same-origin` or `none`;
4. the header `X-Exo-Token` holds the per-launch token. `exo web` makes a new one each start, keeps it in memory,
   and prints it in the URL fragment (`#token=...`), which browsers never send to a server. The page moves it to
   `sessionStorage` (gone when the tab closes) and strips it from the address bar. The browser `exo web` opens gets
   a one-time launch code instead, traded for the token over loopback (#166, `docs/ui/WEB.md`). A custom header
   also forces a CORS preflight, which the server never answers, so a cross-site page cannot send it.

The key travels once, in a POST body over loopback. The server does not log requests, never echoes the key and
never returns a stored value; the page clears the field and keeps the key in no storage. What this does not stop:
a program running as you can read the printed URL or the Keychain anyway. If a supervisor captures `exo web`'s
output, the token is in that log until the next start.

### The agent cannot use this

No tool gives the model these functions. The gate classes `exo providers add|remove|test|default`, `secret-tool`,
any shell or code that names `/api/providers` or `providers_setup`, and every Keychain read as `credentials`,
which always asks you and never gets a standing grant (D-033). Keys are write-only: no code path returns one.

## Use your Claude Code login (`claude-cli`)

If Claude Code is installed and logged in, Exo can run on that login with no API key. Each model call starts the
`claude` command. Exo holds no key for it. `ANTHROPIC_API_KEY` and the other API credentials are removed from the
child's environment, so the call uses the subscription, never an API account.

It is automatic. When the core starts, `claude` is on your PATH, no route of the main role has what it needs (by
default, no Anthropic or OpenRouter key), and neither `models.roles.main` in `config.json` nor the seat sets the
main model, the core puts `claude-cli:sonnet` first for `main`. Sonnet is the first model in the catalog entry
(`sonnet`, `opus`, `haiku`). The status line shows `claude-cli:sonnet`.

- **Cost reads `unknown`.** A subscription has no per-call price in the catalog, and Exo never shows a guessed
  `$0`. To see an estimate anyway, add a price under `models.models` (`docs/TRACE.md`, "Cost").
- **The lists show the configured routes.** The automatic choice is not saved, so `exo models` and
  `exo config roles` still show Anthropic first for `main`, and `exo providers` marks Anthropic as the default.
- **To choose it yourself,** or another model, run `exo providers default claude-cli:opus` (or `/default` in the
  terminal client). The choice is saved to `config.json`, the lists show it, and the automatic choice is off.
- **An Anthropic or OpenRouter key added later takes over the next time the core starts.** Until then the
  running core stays on Claude Code.
- **Exo keeps its own tools and gate.** The CLI runs with its own tools, MCP servers, skills and settings
  switched off. The model sees Exo's tools, and Exo runs each tool call itself, through the gate, before it calls
  the CLI again.
- **The login is not checked ahead of time.** `exo providers test claude-cli` only checks that the command is on
  PATH. Run `claude` once and log in before you start Exo. On a logged-out CLI the first turn fails with the
  CLI's error.

## Claude CLI compatibility and cleanup (`claude-cli`)

Exo's adapter assumes the stream-json envelopes pinned by the **synthetic fixture contract v1** in
`tests/core/models/test_claude_cli.py`. These tests exercise Exo's parser and process handling; they do not
certify an installed Claude CLI release or a real authenticated wire exchange. `exo providers test claude-cli`
only checks that the command is on PATH. Command help is not evidence that history replay or tool boundaries work.

The fixture contract is:

- Input uses `user` and `assistant` frames with a `message` containing `role` and content blocks. Earlier user
  frames carry `shouldQuery: false`; each must receive one `result` with `num_turns: 0` and no error before replay
  continues. A history ending with an assistant gets an explicit `(continue)` user turn.
- Output carries `stream_event` deltas (`text_delta`, `thinking_delta`) and `message_stop`, then assistant content
  and one final `result`. Ordinary completion requires exit 0 and `subtype: success` with no error.
- A tool boundary requires assistant `tool_use` blocks, `subtype: error_max_turns`, and exit 1. Only that
  combination lets Exo execute the tools and continue its own loop; the limit's error flag may be true.
- Usage is read from the final result when reported; absent counts stay unknown. The inert MCP bridge lists
  tools but refuses to run them. Blank input is ignored, malformed JSON gets a JSON-RPC parse error, and valid
  following requests continue to work.
- A user frame whose last text starts with a command name (`/help`) gets a local reply and a `result` with
  `num_turns: 0`, and no model call, as the real CLI does. Exo never sends a user text that starts with `/`.
- The fixture reads each file that an `@path` in any user frame names, as the real CLI does, and records what it
  read. Exo never sends a user `@` that the CLI would read.

The CLI-specific replay and boundary assumptions still need an opt-in, isolated real-CLI contract check against
a mocked local endpoint before any release can be called wire-verified. Default tests never use real OAuth,
private auth files, or network models. This remaining compatibility scope is tracked in issue #47.

Every stream exit cancels and awaits the stderr reader. It retains a bounded, scrubbed diagnostic tail rather
than waiting for a descendant to close stderr. The adapter stops its process group even after the leader exits,
and finishes cleanup before returning a terminal response to the router. A cancel (Esc) while the stream is read
stops it, even when it lands just as a line arrives (#163).

**Working folder.** The CLI runs in a private folder of its own, `claude-cli-cwd-<uid>` in the temp dir (mode
0700). It is the same folder on every call because the CLI writes its cwd into the prompt, and a changing folder
would break prompt caching. The CLI's environment note therefore names that folder, not the thread's, and the CLI
has no switch to drop the note when a system prompt is given. So Exo's system text names the thread's real
working folder on every route: tools run there, and it overrides any working folder an environment note names.
The line is fixed for the thread, so the cache holds. Running the CLI in the thread's folder was not chosen: the
provider call does not carry the thread, and the CLI then adds its own notes about that folder, written for
Claude Code.

**Slash commands.** The CLI reads a user text that starts with `/` as its own command, even with
`--disable-slash-commands`. On version 2.1.302 a built-in name such as `/help` gets a local "isn't available in
this environment" reply with no model call, which Exo reads as an incomplete response and falls back. Any other
word gets a CLI note added, and a replayed history frame is rewritten. Paths such as `/etc/hosts` pass. So the
adapter puts a zero-width space (U+200B) in front of every user text block that starts with `/`, and the model
reads the user's words as typed. Assistant text and tool results are never read as commands and stay unchanged.

**File mentions.** The CLI also reads `@path` in user text as a file to attach, even with `--tools ""`. On
version 2.1.302, wherever the `@` starts the text, follows whitespace (U+FEFF counts) or follows a CJK full stop,
comma, `？` or `！`, the CLI reads the file and sends it to the model, in the latest turn and in replayed history.
The quoted form `@"path"` works too. So `@~/.ssh/id_rsa` sent the key, past the gate, which asks before any
credential read. The text need not be the user's: peer and bus messages, notes from other threads and delegate
briefs reach the CLI as user text. The CLI looks the path up from its working folder, which is empty, and folds
`..` as text. So a path reaches a file only when it leaves that folder: `@/`, `@~`, `@.`, `@"`, or a word with a
`/..` step such as `@x/../../etc/hosts` (seen on version 2.1.303). The adapter puts the same zero-width space in
front of each such `@` in a user text block. The model still reads the `@`. Other `@` words pass as typed: a
decorator such as `@app.route`, CSS `@media`, a diff `@@` header and an address such as `a@example.com`. A space
hidden in a decorator line would break the file the model copies it into. The folder stays empty because the CLI
writes nothing there. If a file with a name not starting with `.` turns up in it, each call gets a fresh empty
folder instead. Exo's own `@path` already adds the file as its own part.

## Use a Muse Code subscription (`muse-cli`)

`muse-cli` lets Exo use a Muse Code subscription by running the `muse` command-line tool. Exo does not hold a
key for it. The Muse login keeps its token in the OS keychain, owned by the `muse` program, and Exo never reads,
copies or stores it. Exo only starts `muse exec` (one prompt, no screen) and reads what it prints.

Set it up once, yourself, in a terminal:

    muse login                      # browser sign-in
    exo providers test muse-cli     # one tiny muse exec call: ok, or an error class
    exo providers default muse-cli:default --role aux

`exo providers` shows `not installed`, `not logged in` or `installed`. The `not logged in` check reads two
non-secret fields (`mechanism`, `subs_active`) from `~/.config/muse/auth.json` and nothing else in that file. It
cannot see the keychain, so the test call is what proves the login works. Error classes from the test:
`not_installed`, `no_key` (not logged in), `auth` (login rejected), `billing` (subscription not active),
`rate_limited`, `timeout`, `network`, `unsigned`, `bad_response`, `error`.

Limits, stated plainly:

- **Text only.** `muse exec` runs its own agent. Exo cannot pass its tools to it or stream tool calls out of it.
  A turn that carries Exo tools is refused (status 501) and the router falls back to the next route. That refusal
  cools nothing, so the next call without tools still reaches `muse-cli` (exo-desktop #52). So `muse-cli` suits a
  role without tools. Its catalog limits it to `aux`. It does not make the main model ready for first run.
- **Slow start, no token stream.** Each turn is a new process. The answer arrives when the run ends. The history
  is flattened into one prompt, and no token counts come back (usage shows as unknown).
- **The native binary must be code-signed on macOS.** An unsigned native Muse executable can fail keychain
  access (`OSStatus -67068`). Exo checks signing with `codesign -dv` only when the configured command is a native
  Mach-O binary. The vendor's `muse` command may be a shell launcher: shell scripts cannot be code-signed, and
  Exo reports their signing state as unknown and lets them run. This does not verify the binary they start.
  An actual signing error is still reported as `unsigned`; if signing is needed, apply `codesign -s -` to the
  native binary, not the launcher. Signing and startup errors allow fallback to another configured route.
- **Locked down.** The run gets no shell, no file writes, no web tools, no reminder agents, no imported foreign
  personal context and no session log, with a private temporary directory as its workspace. Timeout and stop
  terminate its process group, including descendants whose leader has already exited, with bounded cleanup.
  `META_API_KEY` is removed from the child's environment so the subscription is what
  is used, never an API account. The child gets only the minimal environment (D-039).
- **The agent cannot log in for you.** The gate treats `muse login`, `muse auth` and `muse logout` (also with
  flags before them, paths to the executable, or shell wrappers) and any read or write of `~/.config/muse/` as
  `credentials`, which always asks and cannot be covered by a standing grant or lowered by policy.

## Add a key by hand

Exo looks for a key by its variable name (for example `OPENCODE_GO_API_KEY`). It checks, in order:

1. The environment variable of that name (macOS and Linux).
2. macOS only: the Keychain, service `exo`, account = the variable name. Add one with:

       security add-generic-password -U -s exo -a OPENCODE_GO_API_KEY -w

   It prompts for the value and does not echo it. On Linux this step is skipped.
3. Linux only: the Secret Service, when `secret-tool` is installed. Add one with
   `secret-tool store --label="exo NAME" service exo account NAME`, which reads the value from stdin without echo.
4. The file `~/.config/exo/keys.env` (macOS and Linux), one `NAME=value` per line. It must be mode 0600
   (`chmod 600 ~/.config/exo/keys.env`). If it is looser, Exo refuses to read it and warns.

On Linux without `secret-tool`, create the keys file once:

    mkdir -p ~/.config/exo
    touch ~/.config/exo/keys.env
    chmod 600 ~/.config/exo/keys.env
    read -rs KEY; printf 'OPENCODE_GO_API_KEY=%s
' "$KEY" >> ~/.config/exo/keys.env

`read -s` does not echo the value. Check the mode with `ls -l ~/.config/exo/keys.env`; it should show `-rw-------`.
Set `EXO_KEYS_FILE` to use another path. (`exo providers add NAME` does all of this for you.)

Keys are never printed or logged. Never paste one into chat. `EXO_NO_KEYCHAIN=1` and `EXO_NO_SECRET_SERVICE=1`
turn those stores off (tests and sandboxes use them).

## Providers

| Name | Key variable | Base URL | Status |
|---|---|---|---|
| anthropic | ANTHROPIC_API_KEY | https://api.anthropic.com | works |
| openrouter | OPENROUTER_API_KEY | https://openrouter.ai/api/v1 | works |
| openai | OPENAI_API_KEY | https://api.openai.com/v1 | works |
| opencode-go | OPENCODE_GO_API_KEY | https://opencode.ai/zen/go/v1 | chat models only (see below) |
| nous | NOUS_API_KEY | https://inference-api.nousresearch.com/v1 | off: not verified |
| jev | TYPESAFE_API_KEY | https://api.typesafe.ai/v1 | decision role only; works; first decision route by default |
| laya | none | http://127.0.0.1:8000/v1 | decision role only; local; needs `laya-serve` (see below) |
| llamacpp, ollama, lmstudio, vllm | none | http://127.0.0.1 (ports 8080, 11434, 1234, 8000) | local |

OpenCode Go: Exo uses its OpenAI-compatible endpoint (`/chat/completions`): GLM, Kimi, DeepSeek,
MiMo and others. Its other models use `/responses` or `/messages` and are not served yet. Refs look
like `opencode-go:kimi-k3`. Pickers hide the `/responses` and `/messages` families (gpt, grok, muse,
claude, minimax, qwen); a hand-typed ref to one gets "not served by Exo's OpenCode Go adapter yet".
Free and preview routes (`step-5-preview-free`, `longcat-2.5-preview-free`) are served over
`/chat/completions`, but their upstream is flaky: seen 2026-10-12, the same request returned 429
"Endpoint is unavailable", 400 "missing tags" (an upstream routing error, not our request), and then 200.
Exo reports these as "upstream is unavailable right now" and treats them as retryable: the call falls back to the
next route. Nous routes (`nous:stepfun/step-5-preview:free`) flake the same way and get the same handling. Go also needs the
`x-opencode-session` header (Exo sends it).

Nous Portal: the base URL is in Nous's Hermes docs, but they describe OAuth-minted tokens, and it is
unclear whether a plain API key works. The entry is commented out in `catalog.toml` until checked.

Decision models (Jev, Laya) return probabilities, not text. They speak `POST {base_url}/systemone`
with `state`, `model` and typed `questions` (choice, score, noul); the reply has `answers`. Docs:
https://docs.typesafe.ai/api.md. Code: `exo/core/models/decision.py`. Use `ask_choice_result`
there; they go through the `decision` role. If the route is a chat model, the same call asks for a JSON
answer and reports the pick as probability 1.0 with `source="generative"`. A reply that is not that JSON,
or names an option outside the list, moves the call to the next route and cools nothing: it says nothing
about the key, and the main role often uses the same key. A transport failure on a chat route counts as one
strike, and one call asks each credential and model pair at most once. A decision model may only make
a gate stricter (D-015): callers use `tighten_only(current, proposed)`. Try it:

    exo decide "Is this safe to run?" --options safe,risky --state "rm -rf build/"

Jev: set `TYPESAFE_API_KEY` (`exo providers add jev`, env, macOS Keychain service `exo`, Linux Secret Service, or keys.env). It is the first default route of the
`decision` role (`["jev:jev-latest", "laya:laya"]`, owner ruling: base Laya was poor in a live check, Jev
answered sensibly). Without a key Jev is skipped and the role falls through to Laya, then aux, then main.
The gate's check on each tool call stops before main (D-081): it never asks a main route, and a failure there
cools only the model that failed, so a login or key that also serves main keeps working (`docs/GATE.md`).
Shapes were checked live on 2026-10-11 (7 calls). Score levels count from 0; a score can fall between
levels (1.43). Errors: 401 and 422 are not retryable; 429, 529, 5xx and network errors are.

Laya (local, Apache 2.0, https://github.com/NandhaKishorM/laya): the catalog entry is on by default as
the second `decision` route, after Jev, but Laya is not installed on this Mac today, so the route fails fast and the
role falls back to aux, then main. It is an encoder model, so llama.cpp and Ollama cannot serve it (no
GGUF); it needs its own server. To turn it on:

    pip install "laya[serve]"          # pulls torch; the install is several GB
    LAYA_PORT=8000 laya-serve          # default bind 0.0.0.0:8000; set LAYA_HOST=127.0.0.1 to keep it local

Weights download from Hugging Face on first use: about 0.65 GB (`laya-multilingual`) or 0.81 GB
(`laya`, English) each, about 2.3 GB for all three. Device is picked in the order CUDA, MPS, XPU, CPU,
so Apple silicon uses MPS. Model ids: `laya`, `laya-multilingual`, `laya-typed-decisions`. Base Laya is weak
zero-shot and over-confident (research/DECISION-MODELS.md); do not gate on its raw numbers. It was not
installed or downloaded here because torch plus weights is over 2 GB. If port 8000 clashes with vLLM,
set `LAYA_PORT` and override only `base_url` under `[models.providers.laya]`.

Add or change a provider in config under `[models.providers.NAME]` with `transport`, `base_url`,
`key_env`. A table for a catalog provider merges over the catalog entry key by key: the keys you set win and
the rest stay. So `base_url` alone keeps Laya's `decision` transport, `local` flag and decision-only `roles`,
and `read_timeout` alone keeps Anthropic's URL and key. A table that sets `base_url` uses only the `key_env`
and `headers` it sets, never the catalog's. With only `base_url = "http://127.0.0.1:1234/v1"` under
`[models.providers.openai]` (a LAN vLLM or LM Studio behind the `openai` name), the endpoint is keyless and no
OpenAI key is ever sent to it. To send a key along with your `base_url`, set `key_env` in the same table: the
catalog's variable or one of your own. A new provider needs a `base_url` (a CLI transport needs none). Without
one it gets no credential, its routes are skipped and the providers list shows it as `no base_url`, even when
its key is found. A seat's `[models.providers.NAME]` merges over config.json's table first, by the same rule,
and the result merges over the catalog (`docs/SEATS.md`).

## Roles

Each role is a list of `provider:model` routes. The first available one serves; the rest are fallbacks.
Override any role in `[models.roles]` in your config.

A provider whose `key_env` is a list of names has one credential for each name whose key is found. A key that
fails cools down on its own while the others serve the route. A key is known by its place in the list
(`openrouter#0`, `openrouter#2`), so its id stays put when another key is added or removed. A call that runs
across such a change then skips the key that failed, not the new one (exo-desktop #52).

### Route circuit breaker

A route that keeps failing is skipped instead of costing every turn a failed try. After `failures` consecutive
retryable failures (429, 5xx, timeouts, or an upstream-unavailable 400) the route is skipped for `cooldown_s`.
Then one trial call is allowed: success clears the route, failure skips it again for twice as long, up to
`max_cooldown_s`. State is in memory, per process. Key changes and a passing test reset it. If every route
of a role is skipped, they are all tried anyway. The trip is logged once: the attempt that opened the breaker
carries `cooldown_s` in the `model.call` event, and the TUI shows a notice. Config, in `[models.breaker]`:

    [models.breaker]
    failures = 2          # default 2
    cooldown_s = 600      # default 10 min
    max_cooldown_s = 3600 # default 1 h

| Role | Default | Falls back to |
|---|---|---|
| main | Anthropic Opus 5.5, then OpenRouter | none |
| aux | Haiku 5.5 | main |
| decision | Jev (`jev:jev-latest`), then local Laya (`laya:laya`) | aux, then main (the gate: aux only, D-081) |
| eval | empty, which means the `main` route | main |
| fast, standard, deep | Haiku, Sonnet, Opus | standard, then main |

### When every route fails

The error says why. When waiting cannot fix the cause, it also says what will, and the turn is not retried:

- The last route tried refused its key (HTTP 401 or 403): `anthropic rejected the key (HTTP 401: invalid
  x-api-key): fix ANTHROPIC_API_KEY or add a key in /providers`. A provider with no key, such as `claude-cli`,
  asks you to log in again instead. Some providers also send 403 for a refused request, such as flagged input.
  The provider's text in the parentheses says which. The key stays benched for its cooldown (5 minutes after a
  401). Calls in that time repeat the cause and that text, not a wait, unless another route of the role reopens
  sooner. Only the role's own routes count. A refusal on a model the role never asks, such as the gate's aux
  model (D-081), is not named in a main-role error.
- The last route tried is over its provider spend limit (HTTP 402): `anthropic is over its provider spend limit
  (HTTP 402: key_spend_limit_reached): raise the limit or top up the account, then test it in /providers`. A new
  key does not fix this, so the message does not point at the key field. The route stays benched for its cooldown
  and the router tries the next one; the notice says so and warns that the turn's cost is a floor (D-093).
- A local provider's server refused the connection: `ollama is not running at 127.0.0.1:11434: start it, then
  test it in /providers`. The address is the host and port of `base_url` only. A user or password in the URL is
  never shown, so it stays out of the session store. With no usable host the error says "its configured
  address".

Other failures (429, 5xx, timeouts, a remote network error) stay retryable and say when the next route reopens.
That time, and `retry_after`, come only from the role's own routes: a cooldown on another provider, or on a model the
role never asks (the gate's aux model, D-081), does not shorten the hint.
Only the last route tried decides. When an earlier route was down and the last one refused its key, the error
names the key and not the outage.
The two errors above still carry `retry_after`, the time the route reopens. A background summary that failed on
one tries again then instead of giving up on the span (`docs/MEMORY-MODEL.md`).
A passing test in `/providers` (`t`) or in the web Providers tab tells the running core to reload that provider.
Its cooldown and breaker clear, so the next message reaches it at once. An environment variable still wins over a
saved key: change it in the shell that starts Exo, then restart Exo. If no provider can serve the main role, the
terminal client's first run says what the default needs. A keyed provider needs a key. A local server must be
started, and a CLI must be installed and logged in.

## See what is set

    exo providers                 providers: status, where the key is, models, default
    exo models                    providers, key found (yes or no), route per role
    exo models --remote NAME      list the provider's models (network, only when you ask)
    exo config roles              the route per role only

No command prints a key value. A key from the environment, the Keychain (macOS), the Secret Service (Linux) or keys.env is read only by the real CLI;
tests build engines with `env={}` so a shell key never causes spend.
