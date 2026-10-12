# Providers and keys

Exo works with any model provider. You choose which model serves each role.

## Add a key

Exo looks for a key by its variable name (for example `OPENCODE_GO_API_KEY`). It checks, in order:

1. The environment variable of that name (macOS and Linux).
2. macOS only: the Keychain, service `exo`, account = the variable name. Add one with:

       security add-generic-password -U -s exo -a OPENCODE_GO_API_KEY -w

   It prompts for the value and does not echo it. On Linux this step is skipped.
3. The file `~/.config/exo/keys.env` (macOS and Linux), one `NAME=value` per line. It must be mode 0600
   (`chmod 600 ~/.config/exo/keys.env`). If it is looser, Exo refuses to read it and warns.

On Linux, create the keys file once:

    mkdir -p ~/.config/exo
    touch ~/.config/exo/keys.env
    chmod 600 ~/.config/exo/keys.env
    read -rs KEY; printf 'OPENCODE_GO_API_KEY=%s\n' "$KEY" >> ~/.config/exo/keys.env

`read -s` does not echo the value. Check the mode with `ls -l ~/.config/exo/keys.env`; it should show `-rw-------`.
Set `EXO_KEYS_FILE` to use another path.

Keys are never printed or logged. Never paste one into chat.

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
like `opencode-go:kimi-k3`.

Nous Portal: the base URL is in Nous's Hermes docs, but they describe OAuth-minted tokens, and it is
unclear whether a plain API key works. The entry is commented out in `catalog.toml` until checked.

Decision models (Jev, Laya) return probabilities, not text. They speak `POST {base_url}/systemone`
with `state`, `model` and typed `questions` (choice, score, noul); the reply has `answers`. Docs:
https://docs.typesafe.ai/api.md. Code: `exo/core/models/decision.py`. Use `ask_choice` and `ask_score`
there; they go through the `decision` role. If the route is a chat model, the same call asks for a JSON
answer and reports the pick as probability 1.0 with `source="generative"`. A decision model may only make
a gate stricter (D-015): callers use `tighten_only(current, proposed)`. Try it:

    exo decide "Is this safe to run?" --options safe,risky --state "rm -rf build/"

Jev: set `TYPESAFE_API_KEY` (env, macOS Keychain service `exo`, or keys.env). It is the first default route of the
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

| Role | Default | Falls back to |
|---|---|---|
| main | Anthropic Opus 5.5, then OpenRouter | none |
| aux | Haiku 5.5 | main |
| decision | Jev (`jev:jev-latest`), then local Laya (`laya:laya`) | aux, then main |
| eval | empty, which means the `main` route | main |
| fast, standard, deep | Haiku, Sonnet, Opus | standard, then main |

## See what is set

    exo models                    providers, key found (yes or no), route per role
    exo models --remote NAME      list the provider's models (network, only when you ask)
    exo config roles              the route per role only

No command prints a key value. A key from the environment, the Keychain (macOS) or keys.env is read only by the real CLI;
tests build engines with `env={}` so a shell key never causes spend.
