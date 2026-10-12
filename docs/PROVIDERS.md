# Providers and keys

Exo works with any model provider. You choose which model serves each role.

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
- **Web client:** `exo web` opens the page with a one-time token in the address. The Providers tab has the same
  list, a masked key field, Test, Remove and a default model picker.

Where a new key goes: the macOS Keychain (service `exo`) on a Mac; on Linux the Secret Service through
`secret-tool` when it is installed, else `~/.config/exo/keys.env` (written mode 0600, atomically). Choose another
with `exo providers add NAME --store file`. If an environment variable of the same name is set, it wins, and Exo
says so.

What the test call is: OpenAI-style and local providers list their models (`GET /models`), Anthropic lists one
model, a decision provider answers one yes or no question, and `claude-cli` only checks the binary is on PATH.
Errors come back as a class: `no_key`, `auth`, `billing`, `not_found`, `rate_limited`, `server`, `timeout`,
`network`, `not_running`, `not_installed`, `bad_response`, `http`, `error`. The last result is kept in
`$EXO_HOME/state/providers.json` (no secrets) and shows as `verified` or `failed: <class>`; a new key clears it.

If no provider can serve the main role, `exo tui` and `exo web` say so and point here.

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
   `sessionStorage` (gone when the tab closes) and strips it from the address bar. A custom header also forces a
   CORS preflight, which the server never answers, so a cross-site page cannot send it.

The key travels once, in a POST body over loopback. The server does not log requests, never echoes the key and
never returns a stored value; the page clears the field and keeps the key in no storage. What this does not stop:
a program running as you can read the printed URL or the Keychain anyway. If a supervisor captures `exo web`'s
output, the token is in that log until the next start.

### The agent cannot use this

No tool gives the model these functions. The gate classes `exo providers add|remove|test|default`, `secret-tool`,
any shell or code that names `/api/providers` or `providers_setup`, and every Keychain read as `credentials`,
which always asks you and never gets a standing grant (D-033). Keys are write-only: no code path returns one.

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
https://docs.typesafe.ai/api.md. Code: `exo/core/models/decision.py`. Use `ask_choice` and `ask_score`
there; they go through the `decision` role. If the route is a chat model, the same call asks for a JSON
answer and reports the pick as probability 1.0 with `source="generative"`. A decision model may only make
a gate stricter (D-015): callers use `tighten_only(current, proposed)`. Try it:

    exo decide "Is this safe to run?" --options safe,risky --state "rm -rf build/"

Jev: set `TYPESAFE_API_KEY` (`exo providers add jev`, env, macOS Keychain service `exo`, Linux Secret Service, or keys.env). It is the first default route of the
`decision` role (`["jev:jev-latest", "laya:laya"]`, owner ruling: base Laya was poor in a live check, Jev
answered sensibly). Without a key Jev is skipped and the role falls through to Laya, then aux, then main.
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
set `LAYA_PORT` and override `base_url` under `[models.providers.laya]`.

Add or change a provider in config under `[models.providers.NAME]` with `transport`, `base_url`,
`key_env`.

## Roles

Each role is a list of `provider:model` routes. The first available one serves; the rest are fallbacks.
Override any role in `[models.roles]` in your config.

### Route circuit breaker

A route that keeps failing is skipped instead of costing every turn a failed try. After `failures` consecutive
retryable failures (429, 5xx, timeouts, or an upstream-unavailable 400) the route is skipped for `cooldown_s`.
Then one trial call is allowed: success clears the route, failure skips it again for twice as long, up to
`max_cooldown_s`. State is in memory, per process, and `exo providers` key changes reset it. If every route
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
| decision | Jev (`jev:jev-latest`), then local Laya (`laya:laya`) | aux, then main |
| eval | empty, which means the `main` route | main |
| fast, standard, deep | Haiku, Sonnet, Opus | standard, then main |

## See what is set

    exo providers                 providers: status, where the key is, models, default
    exo models                    providers, key found (yes or no), route per role
    exo models --remote NAME      list the provider's models (network, only when you ask)
    exo config roles              the route per role only

No command prints a key value. A key from the environment, the Keychain (macOS), the Secret Service (Linux) or keys.env is read only by the real CLI;
tests build engines with `env={}` so a shell key never causes spend.
