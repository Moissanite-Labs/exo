# Per-seat GitHub authentication

Each seat can use its own GitHub identity for bash tools and background jobs without changing the machine's `gh auth` login. The running core must contain this feature. Replacing desktop web assets alone does not enable subprocess credentials.

For write-only macOS Keychain setup in the web or desktop client, see [Secure credential setup](SECURE-CREDENTIALS.md). The CLI below also supports older explicit storage choices. Use the core's exact `EXO_HOME` and logical seat, not an agent's conversational name.

## Personal access token

Use a dedicated GitHub account or a fine-grained token with the repositories and permissions the agent needs. Account creation and real credential entry remain with the user.

```sh
exo github-auth configure --seat scout
exo github-auth status --seat scout
exo github-auth test --seat scout
```

Configure prompts invisibly. A trusted secret manager or pipeline can supply the token through standard input with `--token-stdin`. There is no token argument. Never paste a token into chat, a shell command or command-line arguments.

On macOS, the default is Keychain. Missing, disabled or refusing Keychain storage does not silently fall back to a file. The CLI can explicitly select `--store keychain`, `--store secret-service` or `--store file`. File storage is plaintext in Exo's existing 0600 keys file. On other platforms the existing provider setup default applies. The secure Credentials view permits only macOS Keychain.

Keychain account names include a hash of the resolved data home, seat and hostname. Binding metadata contains no token. The selected store is authoritative: host environment tokens and stale copies in other stores do not override it.

Token and disabled modes write only a public invalid fallback token to their private 0600 `hosts.yml`. The selected credential remains in its store and child environment. If a command removes the token variables but keeps this configuration directory, ordinary gh lookup fails authentication instead of selecting the machine's OS-keyring identity. An unsafe or unwritable fallback configuration refuses the spawn.

This is not a shell sandbox: replacing `GH_CONFIG_DIR`, explicit secure-storage lookup and other deliberate credential overrides remain outside this boundary.

Replacing a token in the same host and store affects the next tool or job spawn. Existing children retain their original credential until they exit. Changing mode, hostname or store requires removing the existing binding and completing cleanup first. A refused change leaves the current credential intact.

## Separate gh login

```sh
exo github-auth login --seat scout
```

This legacy CLI option runs interactive HTTPS login in a private seat-owned `GH_CONFIG_DIR`, skips SSH-key upload and deliberately uses `gh --insecure-storage`. Its token is plaintext in that directory's 0600 `hosts.yml`. This avoids adding an account to the host gh OS keychain. It is outside the secure Credentials view and is not a Keychain-backed login.

Exo reads only a private, unambiguous plaintext token for the selected hostname. Missing, permissive or ambiguous files fail closed. Multiple account switching in this directory is unsupported. Use a token binding instead.

Each seat has one active hostname binding. Add `--host git.example.com` for an enterprise host. Public GitHub and `*.ghe.com` use `GH_TOKEN`. Other hosts use `GH_ENTERPRISE_TOKEN`. Conflicting inherited tokens are cleared from bound child environments.

## Explicit host authentication and removal

```sh
exo github-auth configure --seat scout --mode host
exo github-auth remove --seat scout
```

Host mode uses the supplied tool environment after the common scrub. It does not restore GitHub tokens or `GH_CONFIG_DIR` from the core process environment. To pass those variables, use the existing `[tools] env_allow` policy. Explicit `env_deny` patterns still win, including `GITHUB_*`. An approved supplied value is preserved.

The normal host gh configuration selected through `HOME` or `XDG_CONFIG_HOME` remains available, without adding a token or config-directory override.

Unconfigured seats keep the common environment behavior for compatibility. To prepare a seat for a separate account without using host credentials, run `remove` first.

Removal writes a disabled binding before attempting deletion. A failed deletion reports `cleanup_pending` and a nonzero CLI exit, retaining the metadata needed to retry. It does not claim the saved token was deleted. Retry removal after resolving the store problem. An already absent Keychain item counts as removed.

A disabled binding allows unrelated bash commands and jobs. It uses a private gh configuration directory and public invalid credential sentinels to override ordinary gh credential lookup. The sentinels are not saved account credentials. Disabled mode does not fall back to the host login. Configure a token explicitly to enable GitHub access again.

`test` performs a read-only GitHub user lookup and returns a login or a generic error, without token output or upstream error bodies. `status` does not contact GitHub, but checking availability can access the selected secret store.

## HTTPS Git and boundaries

Bound children receive temporary Git configuration through `GIT_CONFIG_COUNT`. Host and repository configuration files are not edited.

For the selected HTTPS host, an empty helper resets the inherited helper chain, followed by `gh auth git-credential`. Inherited Git configuration environment overrides, askpass hooks and Git/gh tracing variables are cleared. Relevant HTTP extra headers are reset, including more-specific URL paths found in configuration at the starting working directory.

Header inspection reads configuration key names only, with a three-second timeout, a 64 KiB output limit and a 256-name limit. Matching URL keys containing user information, a query or a fragment are refused rather than copied into the child environment. Inspection failure refuses the spawn.

A configured token that is missing or unavailable fails before bash or a job starts. `tools.env_deny` wins, including when policy prevents installing the required credential or Git configuration variables. Provider subprocesses and code-search tools do not receive this credential automatically. Existing approval gates still apply.

This covers ordinary gh commands and HTTPS Git authentication for the selected host. SSH keys, URL-embedded credentials, explicit `git -c` or environment overrides, and changing directories inside a command can bypass that setup.

Arbitrary shell access remains trusted: code can use, transform or export its environment credential. Raw-value redaction removes known tokens from foreground output and bound job logs, including split reads. It is not a sandbox against encoded output or deliberate exfiltration.
