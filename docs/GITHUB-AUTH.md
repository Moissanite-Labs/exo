# Per-seat GitHub authentication

After upgrading the core to a version containing this feature, each seat can use its own GitHub identity for bash tools and background jobs. Setup does not change the machine's `gh auth` login. The currently running core must be upgraded separately. Changing a desktop's web assets alone does not enable subprocess credentials. Desktop credential setup is follow-up work. Use the CLI below with the core's exact `EXO_HOME` and seat.

## Personal access token

Use a dedicated GitHub account or a fine-grained token with the repositories and permissions the agent needs. Token creation and account provisioning remain with the user.

```sh
exo github-auth configure --seat scout
exo github-auth status --seat scout
exo github-auth test --seat scout
```

The configure command prompts invisibly. For a trusted secret manager or pipeline, `--token-stdin` reads the token from standard input. There is no token argument.

Do not paste a token into chat, a shell command, or a command-line argument. `--store keychain`, `--store secret-service` or `--store file` selects storage. The platform default is reused from provider setup. File storage uses Exo's existing 0600 keys file.

Account names include a hash of the resolved data home, seat and hostname. Binding metadata contains only mode, host and store, never the token.

The selected store is authoritative. Host environment tokens and stale copies in other stores do not override it. Changing a saved token takes effect on the next tool/job spawn, without changing the core process environment. Existing subprocesses retain their original credential until they exit.

## Separate gh login

```sh
exo github-auth login --seat scout
```

This runs an interactive HTTPS login in a private seat-owned `GH_CONFIG_DIR`, skips SSH-key upload, and uses `gh --insecure-storage` deliberately. The credential is plaintext in that directory's 0600 `hosts.yml`. The directory is private. This avoids writing an account into the host gh OS keychain.

Exo reads only a private, unambiguous plaintext credential for the configured hostname. Missing, permissive or ambiguous files fail closed. Multiple account switching in this directory is unsupported. use a token binding instead.

Each seat has one active hostname binding. For an enterprise host, add `--host git.example.com` during configure or login. Public GitHub and `*.ghe.com` use `GH_TOKEN`. Other hosts use `GH_ENTERPRISE_TOKEN`. Conflicting host tokens are removed from bound child environments.

## Explicit host authentication and removal

```sh
exo github-auth configure --seat scout --mode host
exo github-auth remove --seat scout
```

Host mode deliberately uses the machine's existing gh configuration and inherited GitHub authentication. Unconfigured seats retain the previous host behavior for compatibility. Removal deletes the seat's saved credential and private gh directory, then keeps an explicit disabled binding. It does not fall back to the host login. Re-enable by choosing a mode explicitly.

`test` performs a read-only GitHub user lookup and returns a login or a generic error, without upstream error bodies or token output. `status` does not contact GitHub.

## Boundaries

A configured seat with unavailable credentials fails before spawning bash or background jobs. It does not silently become the host user. The seat's `tools.env_deny` still wins. Provider subprocesses and code-search tools do not receive the credential through this feature. The normal tool and approval gates remain in place.

Known raw credential values are redacted from shell streaming/final results and from bound jobs before their logs are written, including values split across reads. Tokens are never placed in process arguments or saved binding metadata.

Arbitrary shell access is trusted: a command can use, transform or exfiltrate its environment credential. Exact-value redaction is not a credential sandbox. Use limited token permissions and normal approval controls.
