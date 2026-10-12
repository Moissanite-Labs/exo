# Providers and keys

Exo works with any model provider. You choose which model serves each role.

## Add a key

Exo looks for a key by its variable name (for example `OPENCODE_GO_API_KEY`). It checks, in order:

1. The environment variable of that name.
2. The macOS Keychain, service `exo`, account = the variable name. Add one with:

       security add-generic-password -U -s exo -a OPENCODE_GO_API_KEY -w

   It prompts for the value and does not echo it.
3. The file `~/.config/exo/keys.env`, one `NAME=value` per line. It must be mode 0600
   (`chmod 600 ~/.config/exo/keys.env`). If it is looser, Exo refuses to read it and warns.

Keys are never printed or logged. Never paste one into chat.

## Providers

| Name | Key variable | Base URL | Status |
|---|---|---|---|
| anthropic | ANTHROPIC_API_KEY | https://api.anthropic.com | works |
| openrouter | OPENROUTER_API_KEY | https://openrouter.ai/api/v1 | works |
| openai | OPENAI_API_KEY | https://api.openai.com/v1 | works |
| opencode-go | OPENCODE_GO_API_KEY | https://opencode.ai/zen/go/v1 | chat models only (see below) |
| nous | NOUS_API_KEY | https://inference-api.nousresearch.com/v1 | off: not verified |
| jev | TYPESAFE_API_KEY | https://api.typesafe.ai/v1 | decision role only; adapter is a stub |
| llamacpp, ollama, lmstudio, vllm | none | http://127.0.0.1 (ports 8080, 11434, 1234, 8000) | local |

OpenCode Go: Exo uses its OpenAI-compatible endpoint (`/chat/completions`): GLM, Kimi, DeepSeek,
MiMo and others. Its other models use `/responses` or `/messages` and are not served yet. Refs look
like `opencode-go:kimi-k3`.

Nous Portal: the base URL is in Nous's Hermes docs, but they describe OAuth-minted tokens, and it is
unclear whether a plain API key works. The entry is commented out in `catalog.toml` until checked.

Jev returns probabilities, not text. Exo can hold the provider and key, but the adapter raises
"not implemented" and the router falls through to the next route.

Add or change a provider in config under `[models.providers.NAME]` with `transport`, `base_url`,
`key_env`.

## Roles

Each role is a list of `provider:model` routes. The first available one serves; the rest are fallbacks.
Override any role in `[models.roles]` in your config.

| Role | Default | Falls back to |
|---|---|---|
| main | Anthropic Opus 5.5, then OpenRouter | none |
| aux | Haiku 5.5 | main |
| decision | empty (local Laya is the planned default; until its adapter lands it uses aux) | aux, then main |
| eval | empty, which means the `main` route | main |
| fast, standard, deep | Haiku, Sonnet, Opus | standard, then main |

## See what is set

    exo models                    providers, key found (yes or no), route per role
    exo models --remote NAME      list the provider's models (network, only when you ask)
    exo config roles              the route per role only

No command prints a key value. A key from the environment or Keychain is read only by the real CLI;
tests build engines with `env={}` so a shell key never causes spend.
