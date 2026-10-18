# Per-seat GitHub identity

Status: proposed 2026-10-13 for issue #106, revised 2026-10-18 after review. The principal decides whether to adopt
it. No code exists for any of it. This note was checked against main at `c937a85` and describes nothing as existing
unless it exists there. "Not confirmed" marks what could not be checked. The last two sections say how each claim
about main and about GitHub was checked. Three claims in the text of #106 are corrected here: its item 3 (the token
reaches only `gh` and `git` through the existing `env_allow` path), the bypass sentence in its item 4, and the `ps`
line in its acceptance test.

## The decision

Every seat that writes to GitHub today uses the principal's own `gh` login. (A seat is one named agent, see
`docs/SEATS.md`.) GitHub therefore credits the principal with every issue, pull request, review, comment and push an
agent makes, and a signature line in the text is the only sign that an agent did it. As a result:

- GitHub cannot list one agent's work or show how it changes over time.
- GitHub's review rules cannot treat agent work differently from the principal's, because GitHub sees one person.
- An agent holds the principal's full rights. They cannot be narrowed to one repository, or withdrawn from one
  agent, without withdrawing them from the principal.

The goal is that each seat writes under an identity of its own, with only the rights it needs. Its credential stays
out of transcripts, the trace and the ledger, and out of the environment and arguments of every process that does not
need it. The options are A (a GitHub App for each seat), B (a machine account for each seat) and C (one shared bot).

**What it changes.** GitHub shows agent work apart from the principal's. A seat has the rights chosen for it, on the
repositories chosen for it. Under A and B one seat can also be cut off without touching the principal or the others.

**What it does not change.** The principal and every seat run as one user on one machine. No OS sandbox isolates tool
processes on main (the required-executor interface, #31 and `docs/EXECUTORS.md`, is where an OS supplies one). So a
program a seat runs can read the environment of other processes of that user, and any file that user can read, if it
goes looking. A narrower credential limits what a thief can do with it. It does not prevent the theft. The gate (Exo's
check on every tool call, `docs/GATE.md`) asks before the obvious forms and is not a boundary between processes of one
user (D-039 "Limits").

**What it takes.** Seven pieces of new Exo work, listed under "What has to be built first", and an eighth if the
principal chooses Exo-enforced review (decision 4). None is on main. All three options need them, except that B has
no minter (step 6). The options differ mostly in what gets registered at GitHub. The alternative to all three is to
change nothing, which keeps the three problems above.

### The options

Money below assumes the free plan the organization is on today (see "Human review once seats are bots").

| | A. One app per seat | B. One machine account per seat | C. One shared bot for all seats |
|---|---|---|---|
| What it is | A GitHub App: a robot identity the organization owns, with no password and no paid seat. | An ordinary GitHub account that software signs in to. A human owns it and answers for it. | One app (or one machine account) that every seat uses. Each post would carry a note naming the seat, and Exo would record it in its signed ledger. |
| Money | Free. | Free for one machine account per person or company. More need other owners or paid accounts (GitHub terms). On the Team plan (GitHub's paid organization plan) a machine account in the organization takes a paid seat. | Free. |
| Long lasting secret | The app's private key. It does not expire and works until someone deletes it at GitHub. | A personal access token. It can be set never to expire, unless the organization sets a limit. | As A (one key) or as B (one token). |
| Secret used for each call | A token that lasts one hour and carries only the repositories and rights chosen when it is made. | The long lasting token itself. | As A or as B. |
| One seat's rights and cut-off | Yes, once the fallbacks are closed (step 3). Deleting the key stops new tokens. A token already made may stay valid for up to an hour unless Exo revokes it. | Yes, once the fallbacks are closed. Revoking the token cuts the seat off. | No. One set of rights and one cut-off for all seats. |
| What GitHub shows | `name[bot]` for each seat, under a public name (decision 3). | A user for each seat. | One author for every seat. The seat is a claim in the text. |
| Assigned an issue, asked to review | Not confirmed. GitHub's pages on assigning and requesting reviews do not mention apps. | Yes. | As A or as B. |
| Commits carry GitHub's Verified badge | Only commits made through the GitHub API with no custom author or committer. Commits pushed with `git` carry no badge. | Yes, if a signing key is registered on the account. | As A or as B. |
| New Exo work | Steps 1 to 7. Step 6 is a minter. It needs an RSA signing library, which is a dependency decision, and it lives in a plugin. | Steps 1 to 5 and 7. No minter, so no signing library. | As A (app) or as B (machine account), plus the note and the ledger link. |

### The weakest option, and the case against the call

B is the weakest default. Every seat needs a human owner, an email address, a password and two-factor setup. GitHub
allows one free machine account per person or company. The token can be set never to expire. The case for B is that
it behaves like a person. A seat can be assigned issues and asked to review, and its commits can show Verified. If
seats must appear in those places, B needs no extra work at GitHub. The price is accounts to secure and pay for.
Work is assigned in Exo, by a client or by a lead seat over the bus, and not by GitHub assignment, so A's possible gap
there matters less. That does not cover review requests, which only B is known to support.

The case for C is cost. A seat no longer holds the principal's login, GitHub shows agent work apart from the
principal's, and branch rules can treat the bot differently. C cannot cut off one seat without the others, and GitHub
itself holds no per-seat history.

### The call

Option A, started on one seat. A costs one more app and one more key per seat than C, and buys per-seat rights and
cut-off. The Exo work is the same for A and C, so the pilot commits to neither: a second seat can get its own app (A)
or reuse the first (C). Approving it approves the build steps and a one-seat pilot, and nothing about the other seats
yet.

### What else the principal decides

Decision 1 is the option above. Each of the rest has a recommended answer.

2. **May a seat fall back to the principal's login when its own credential fails?** No means the seat stops and says
   why. Yes keeps work moving, but the action goes out under the principal's name with the principal's rights, which
   is today's problem again. Recommended: no. The price is that a GitHub outage or a locked key store stops that
   seat's GitHub writes until it is fixed.
3. **What public name does each seat get?** GitHub shows an app's or account's name as the author of every post and
   commit, and the outbound scrub (D-033, the check that holds public posts naming internal things) never reads that
   metadata. D-033 treats seat names as internal. So under A and B a seat's name becomes public through the author, and
   under C through the note in each post. Either each seat gets a public alias that is not its name, kept in the seat
   file, or seat names become public, which needs `posting.scrub.allow` entries or `seat_names` turned off.
   Recommended: aliases. GitHub requires app names to be unique across GitHub and at most 34 characters, so a seat
   name may be taken anyway.
4. **How is human review of agent pull requests enforced?** GitHub can enforce it, which needs a public repository or
   the Team plan. Or Exo enforces it, with a gate category that only the principal answers (step 8), and GitHub would
   not refuse an app that has write access. See "Human review once seats are bots". Recommended: Exo now, GitHub when
   the repository or the plan allows.
5. **Who registers and installs the apps?** An organization owner can register an app under the organization, and so
   can a member the organization has made an App manager for all its apps. An App manager can change an app's
   settings but cannot install or uninstall it. An owner can install it. So can a repository admin, on the
   repositories they administer, when the app asks for no organization permission and no Repository administration
   permission, unless an owner restricts installs to owners (GitHub). The proposed rights (decision 6) are of that
   kind. Recommended: an organization owner registers and installs each one, with the setting "Only on this
   account".
6. **Where may each seat write, and with which rights?** Proposed start: the Exo repository only, with Contents, Pull
   requests and Issues at write and nothing for Actions, Workflows or Administration. Pull requests write includes
   approving reviews, because GitHub has no separate permission. Contents write lets the app push to any branch GitHub
   does not protect, and on this repository none can be protected today (see "Human review once seats are bots").
7. **Do agent commits credit the principal as co-author?** Yes puts the commits on the principal's profile and mixes
   agent work back into author filters. No leaves the bot as the only author. Recommended: no.

Accepting every recommendation means: A on one seat, no fallback, public aliases, Exo-enforced review for now, an
organization owner registers the apps, the proposed repository and rights, and no co-author.

Next step on approval: open one issue per build step, correct #106 to match this note, ask an organization owner to
register one app for the pilot seat, and run the pilot on a scratch repository in the organization.

## What a process of the same user can see

- **Environments are readable.** On macOS 27.0.1 a child started with a fake sentinel variable showed it under
  `ps eww -p <pid>` and `ps -E -p <pid>` when the child was a Python from a virtual environment, and did not when it
  was Apple's `/bin/sleep` (checked 2026-10-18). Whether a program hides its environment depends on the program. `gh`
  and `git` were not tried, so the design assumes any process that holds the token can be read. On Linux the same data
  is in `/proc/<pid>/environ`, which was not tried here.
- **Seats can read each other.** Each seat is its own `exo serve --seat` process of one user (`docs/SEATS.md`). A token
  that sits in an environment or a file the user can read can be read from another seat's tool call. It is not among
  that reader's known secrets (step 4), so it would reach that reader's transcript, and it is usable for up to an hour.
- **Child programs and hooks read it too.** A git hook or a `conftest.py` that the agent wrote earlier runs as the same
  user inside a later `git push` or `pytest` call, and can read whatever that call can (D-038 and `docs/GATE.md`,
  "Known limit"). An OS sandbox (#31) is the fix, and none ships on main.
- **The gate allows the read today.** `ps` is in `READ_PROGRAMS` and its environment flags are not checked (step 1).
  Direct dumps (`printenv GH_TOKEN`, `echo $GH_TOKEN`, `cat /proc/<pid>/environ`) are `credentials` and ask.

## What has to be built first

Each step gives what main does today, what to build and the risk. Steps 1 and 8 change only
`src/exo/gate.py`, which the core line budget does not count. Steps 2, 3, 4, 5 and 7 touch counted files
(`src/exo/core`, `src/exo/protocol`), and step 3 also touches the gate. Main stands at 15,076 of 15,813 counted lines
(D-077), so 737 are left. The size of those changes is not measured.

### Step 1. Close three gate gaps that exist today

- **Today.** `ps` is a read program and its environment flags are not checked, so `ps eww -A`, `ps -E -A` and
  `ps axe` are allowed. A runner prefix turns an environment dump into a read: `uv run printenv GH_TOKEN` and
  `poetry run printenv GH_TOKEN` classify `read` (#155). `git remote add`, `set-url` and `remove` classify `read`, so
  a seat can point a remote at SSH without an ask. The first and third have no issue as of 2026-10-18.
- **Build.** Class `ps` with the environment flags (`e` in BSD style, `-E`, and `-e` in macOS legacy mode) as
  `credentials`. Class the three `git remote` forms as a write. #155 has its own fix in progress (PR #164).
- **Risk.** In its default mode on macOS `ps -e` lists every process (`man ps`), so the rule has to read the flag style
  or it asks on harmless listings. Stricter classes mean more asks.

### Step 2. Deliver the token without putting it in the environment

- **Today.** Nothing delivers a per-call token. `[tools] env_allow` (parsed in `src/exo/core/seats.py`) names
  variables that stay in the copy of the core's environment that `tool_env` builds for every bash call, every
  background job and every `code_find` call. It keeps only a variable the core already holds and does not know which
  program runs. Using it would put the token in every tool call's environment, and in a job's environment from the
  moment the job starts. `docs/SEATS.md` describes it as variables "bash and jobs may keep".
- **Build.** A mechanism that adds what one call needs, after the scrub, and meets these requirements.
  - R1. The token is in no environment variable and no argument list of the shell, of background jobs or of
    `code_find`. If a design must put it in one short-lived process, the build PR says which.
  - R2. A write fails closed when no token can be made (step 3).
  - R3. It works inside a background job that outlives the token's hour ("When it fails").
  - R4. Every minted token goes through `remember_secret` (step 4).
  - R5. It is applied after the environment scrub. The scrub drops every `GH_` variable, `GH_CONFIG_DIR` included, and
    git's `GIT_CONFIG_COUNT` family and `GIT_CONFIG_PARAMETERS` unless `env_allow` names them (#159, fixed by #176), so
    a setting made before the scrub would be lost.
- **Two candidates.** Both are new and neither has been tried.
  - (a) A private `gh` configuration folder per seat, with a 0600 `hosts.yml` that holds the token, and a private git
    config whose credential helper is `gh` (what `gh auth setup-git` configures). The core would rewrite the file
    before the token has 10 minutes left and remove it at stop. No variable would carry the token, `gh` and `git`
    would need no wrapper, and a long job would read the fresh file at its next call. The token would sit in a file
    the same user can read for as long as the core runs. Name the folder so the gate's credential-path rule covers it
    (the rule already covers `.config/gh/hosts.yml`).
  - (b) A credential helper for `git` and a wrapper for `gh` that ask the core over a private socket at the moment of
    use. The token would not be at rest. The wrapper would put it in `gh`'s own environment for the length of the
    call, and any process of the user that can reach the socket could ask. A wrapper on `PATH` is skipped by a full
    path, so step 3 must hold either way.
  - Recommended for the pilot: (a). Not confirmed: that `gh` accepts an installation token from `hosts.yml` as it does
    from `GH_TOKEN`. The pilot checks it and falls back to (b) if it fails.
- **Risk.** New code between the core and every tool process.

### Step 3. Close the fallbacks, so a missing token means no write

- **Today.** Every seat already writes as the principal. A design that only added a token would leave that path open.
  `git` never reads `GH_TOKEN` (git's manual names no such variable). Without `GH_TOKEN`, `gh` reads its stored login
  from its configuration folder (`GH_CONFIG_DIR`, else `$XDG_CONFIG_HOME/gh`, else `$HOME/.config/gh`), and GitHub's
  `gh` manual says the variable "takes precedence over previously stored credentials". A private `GH_CONFIG_DIR` does
  hide a stored login: with an empty one, `gh` 2.102.0 on macOS 27.0.1 reported no login although its login is in the
  Keychain (last section). `tool_env` keeps `HOME`, `XDG_CONFIG_HOME`, `SSH_AUTH_SOCK` and `GIT_ASKPASS`. On the
  machine this note was revised on, the system git config (Xcode's) sets the `osxkeychain` credential helper. The gate
  logs `git push` and `gh pr merge` and does not ask. So when minting fails, when the app is uninstalled, or when a
  command runs `env -u GH_TOKEN gh ...`, `gh` writes as the principal. A push to an SSH remote uses the principal's SSH
  agent and keys the same way.
- **Build.** For a seat with a `[github]` table:
  - Point `GH_CONFIG_DIR` and `GIT_CONFIG_GLOBAL` at seat private paths and set `GIT_CONFIG_NOSYSTEM`, so the
    principal's `gh` login, global git config and system helpers are not read.
  - Reset `credential.helper` to empty and then set it to Exo's own (git's manual: an empty value resets the list).
  - Withhold `SSH_AUTH_SOCK`, `GIT_SSH` and `GIT_SSH_COMMAND`. Add `url.<base>.insteadOf` rules that rewrite SSH
    remotes to GitHub (the `git@` form and the `ssh://` form) to HTTPS.
  - No token means no call. A `gh` or `git` write fails with an authentication error, and the seat's reply says why.
  - Make the gate ask, as `credentials`, before a command that sets or unsets `GH_TOKEN`, `GITHUB_TOKEN`,
    `GH_CONFIG_DIR`, `SSH_AUTH_SOCK` or a `GIT_CONFIG_*` variable.
- **Risk.** Flows that quietly lean on the principal's login break, such as SSH clones of private repositories and
  `gh` reads of repositories the app is not installed on. The principal's own shell is untouched, because the changes
  apply only to processes a seat starts. A deliberate bypass by code running as the same user (the real
  `~/.config/gh`, the Keychain, `~/.ssh`) stays possible until an OS sandbox exists. The gate already asks for the
  obvious reads (`gh auth token`, `cat ~/.config/gh/hosts.yml`, `ssh-add -l`).

### Step 4. Redact every minted token

- **Today.** Tool results are cut only for values the process knows: values it looked up, the keys file, and its own
  secret-named variables (`src/exo/core/models/keys.py::known_secret_values`). A token minted at run time is not
  among them, so `redact_secrets("push ok ghs_...", known_secret_values())` returned it unchanged, and `[redacted]`
  after `remember_secret(token)`. The trace also cuts by shape (`gh[pousr]_` plus 12 letters or digits,
  `src/exo/core/trace.py`). GitHub says it began a staged rollout of the shape `ghs_<app id>_<JWT>` for new
  installation tokens on 27 April 2026. A synthetic string of that shape was not cut by the trace pattern, and the
  similar pattern in `src/exo/config.py` (which refuses secrets in config values) did not match it. A 40 character
  `ghs_` string was cut and matched.
- **Build.** Call `remember_secret` on every token as it is minted. That is an exact match and works for any shape.
  Widen both shape patterns to the new shape. Optionally add a shape pass to tool results, which also covers a token
  another seat's process minted.
- **Risk.** A shape pass cuts any text that looks like a token, including a quoted example. Exact matching has no such
  cost.

### Step 5. Show and record which identity a post goes out as

- **Today.** The approval hash covers category, tool, target, title, text, command and arguments, not the
  environment (`src/exo/gate.py::approval_binding`). An approval given for a bot post also covers the same call going
  out as the principal.
- **Build.** Show the identity the call will use on the approval card, taken from the seat's configuration and the
  credential in use and never from model text. Include it in `approval_binding`. Record the app, its installation (the
  app as installed on the organization), the rights and the token's expiry in the trace and the ledger, and never the
  token.
- **Risk.** One more optional field on the approval request, as D-033 added `binding`, and a change in each client
  that draws the card. A client that does not show it is still protected by the hash.

### Step 6. The minter (a plugin)

- **Today.** Exo has no RSA, JWT or `openssl` code. The base dependencies are `pynacl` and `httpx`. `pyjwt` and
  `cryptography` are in `uv.lock` only through the optional `mcp` extra.
- **Build.** A minter that signs a GitHub App JWT (a signed proof of identity: RS256, valid for at most 10 minutes,
  issue time set 60 seconds back, as GitHub recommends), trades it for an installation token limited to the seat's
  repositories and rights, and caches it. It can revoke a token at the end of use with `DELETE /installation/token` if
  the delivery design allows. The dependency choice: `cryptography` as a new optional extra in `pyproject.toml`, as
  `bus` and `mcp` are today (recommended, so the base install keeps two dependencies), or `openssl` as a subprocess (no
  new Python dependency, but the key needs a pipe or a short-lived file). The code lives in a plugin outside the
  counted core.
- **Key storage and rotation.** Keep the private key in the Keychain or the Secret Service by reference, as provider
  keys do (`src/exo/core/models/keys.py`). A process of the same user can read that item (D-039 "Limits") and can mint
  tokens for as long as the key exists. GitHub allows up to 25 keys per app and advises several so a key can be rotated
  without downtime. The plan: add a key, switch the seat to it, confirm a mint, delete the old key. Do it every 90 days
  (proposed) and whenever someone with access to the machine leaves.
- **Risk.** A new signing path. Use the library and never hand-written RSA. The key is in the core's memory while it
  signs.

### Step 7. The `[github]` seat table and the core seam

- **Today.** `src/exo/core/seats.py` parses no `[github]` table, and no hook lets a plugin set the environment of a
  call of bash, a job or `code_find`.
- **Build.** A `[github]` table with `app_id` (or client ID), `installation_id`, `key` (the name of the Keychain or
  Secret Service item, never the PEM text), `repositories`, `permissions` and `alias` (the public name). A small hook
  in the core so a plugin can supply what a call needs after the scrub.
- **Risk.** Core lines against the budget. The hook is small and unmeasured.

### Step 8 (only if decision 4 is Exo-enforced). A gate category for approvals and merges

- **Today.** `gh pr review --approve` and `gh api .../reviews -f event=APPROVE` classify `github_post`, which asks the
  principal by default. A principal who lowers `github_post` lowers approvals with it. `gh pr merge` is `vcs_merge` and
  only logged.
- **Build.** A category for approving reviews and merges that only the principal answers and no standing grant covers,
  like `store_write` (`PRINCIPAL_ONLY` and `NO_GRANT` in `src/exo/gate.py`).
- **Risk.** The seat holds a token that can approve, so this is best effort against a seat that tries. It is not a
  boundary.

## When it fails

In every case no `gh` or `git` call should go out as anyone else, the seat's reply should name the cause, and the
failure should be a trace row.

- **A token expires inside a long job.** Tokens last one hour. Jobs default to two (`max_wall_s` is 7200), and a job's
  environment is built once when it starts (`src/exo/core/jobs.py`). A token in the environment cannot work here.
  A file or helper that is read at use time would work while the core runs. With the core stopped, the next call in
  the job would fail with an authentication error.
- **A cached token is nearly spent.** A token with fewer than 10 minutes left (proposed) should be replaced before use.
- **Minting fails.** Network down, a GitHub error, or a rate limit. An installation has its own hourly limit, at least
  5,000 requests. GitHub answers 403 or 429 and names the reset time, and Exo should wait for it instead of retrying in a
  loop.
- **The key store is locked or the key is missing.** The Keychain lookup times out after 3 seconds
  (`KEYCHAIN_TIMEOUT` in `src/exo/core/models/keys.py`), and the mint would fail.
- **The key is deleted at GitHub, or the app is uninstalled or loses a repository.** The token request would fail with
  GitHub's own error, which the reply should pass on.
- **The machine clock is far off.** GitHub recommends setting the JWT's issue time 60 seconds back to allow for drift,
  so a larger error would make the mint fail. The reply should say to check the clock.

## Human review once seats are bots

The case for separate identities leaned on branch rules that require a human to review agent work. Today:

- An app with Pull requests write can submit reviews, including approvals. GitHub lists
  `POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews` under Pull requests write for installation tokens, and the
  review `event` can be `APPROVE`. Whether GitHub counts an app's approval toward required approvals is not stated on
  its page about protected branches, which speaks of "people with write permissions" and of code owners. Not confirmed.
- As of 2026-10-18 the organization is on GitHub's free plan and the repository is private (both read from the API
  that day). GitHub lists required reviewers, protected branches and code owners under the Team plan for private
  repositories. The same day the API answered 403 ("Upgrade to GitHub Pro or make this repository public to enable
  this feature") to a request for this repository's branch protection and to one for its rulesets.
- With no protected branch, an app that has Contents write can push to `main` itself.
- On main, `gh pr review --approve` asks, while `git push` and `gh pr merge` are only logged.

GitHub can enforce the rule with a public repository or the Team plan, a code owners file that lists no apps, and a
rule that requires a code owner's approval. Exo can enforce it with step 8, plus `vcs_push` and `vcs_merge` raised to
ask for these seats.

Any bypass mode (#96, a proposal that is not on main) must keep public GitHub writes (`github_post`) and step 8's
category asking. The floor the gate has today (irreversible, public and third party, in `decide`) does not cover
`github_post`, which is costly, so a bypass built only on that floor would lower it. #96 should name `github_post`.

## Pilot and acceptance tests

Start with one seat on a scratch repository, then register the rest. The pilot passes when:

- With minting disabled, `gh pr comment`, `gh api -X POST` and `git push` by the seat all fail with an authentication
  error, for an HTTPS remote and for an SSH remote, and `env -u GH_TOKEN gh pr comment` fails or asks.
- With minting enabled, GitHub shows the bot as the author of a comment and a pull request and as the pusher of a
  branch.
- A scan of `ps eww` during a `gh` call, and of the transcript, the trace and the ledger after it, finds the token
  only where the chosen design says it may be (R1).
- A token printed by a tool call is `[redacted]` in all three, in the old shape and in the new one.
- A background job that runs `git push` at minute 90 succeeds while the core runs and fails closed after it stops.
- Deleting the app's key at GitHub stops new tokens at once and the other seats keep working.
- The seat's approving review is refused by Exo (step 8). Where a public scratch repository or a Team organization
  is available, the same test shows whether GitHub counts an app's approval.
- A commit pushed with `git` carries no Verified badge, and one made through the API with no custom author carries it.
- The seat's usual commands (`gh pr create`, `gh pr comment`, `gh issue create`, `gh api`) all work with an
  installation token.

## Not confirmed

- Whether an app can be assigned an issue or requested as a reviewer. GitHub's pages on both do not mention apps.
- Whether GitHub counts an app's approving review toward required approvals.
- Whether a bot can register a signing key for commits pushed with `git`. GitHub's page on bot signatures is silent.
- Whether `gh` accepts an installation token from `hosts.yml`. That needs an installed app. Also whether an empty
  private configuration folder hides a login kept in the Secret Service on Linux. Only the macOS Keychain was tried.
- Which token shape the pilot app receives. GitHub describes a staged rollout of `ghs_<app id>_<JWT>`.
- Whether commits made with the bot's noreply identity link to the bot's profile.
- Linux: that `/proc/<pid>/environ` is readable by another process of the same user. Not tried here.

## How the claims about main were checked

Run on 2026-10-18 on main `c937a85`, from a checkout where `exo.__file__` points inside the checkout. The same
probes gave the same results on main `35b7de8`. Shell text was classified with `exo.gate.classify` and `decide` on the
default policy and never run. The first column is the shell text, the second is the `classify` category and the third
is the `decide` outcome.

```
ps eww -A | grep -o 'GH_TOKEN=[^ ]*'               read          allow
ps -E -A                                           read          allow
ps axe                                             read          allow
uv run printenv GH_TOKEN                           read          allow
printenv GH_TOKEN                                  credentials   ask
echo $GH_TOKEN                                     credentials   ask
cat /proc/1234/environ                             credentials   ask
git push ssh://github.com/o/r.git main             vcs_push      log
env -u GH_TOKEN gh pr merge 12                     vcs_merge     log
env -u GH_TOKEN gh pr comment 12 --body hi         github_post   ask
git remote set-url origin ssh://github.com/o/r.git read          allow
gh pr review 12 --approve                          github_post   ask
gh auth token                                      credentials   ask
cat ~/.config/gh/hosts.yml                         credentials   ask
ssh-add -l                                         credentials   ask
```

- `tool_env` with a synthetic core environment and `allow=["GH_TOKEN"]` returned `GH_TOKEN`, `HOME`,
  `XDG_CONFIG_HOME`, `SSH_AUTH_SOCK`, `GIT_ASKPASS` and `GIT_SSH_COMMAND`, and dropped `GH_CONFIG_DIR`. With an empty
  base environment it returned no token, so `env_allow` adds nothing the core does not hold.
- `redact_secrets` on a fake `ghs_` token: unchanged before `remember_secret`, `[redacted]` after.
- `core.trace.scrub` on a fake 40 character `ghs_` token: cut. On a fake `ghs_1234567_<JWT-shaped>` string of 88
  characters: not cut.
- `check_scrub` on `Exo-Seat: scout-two` with a seat named `scout-two`: `internal name: scout-two` on public exposure,
  none on internal. A `gh pr comment` with that trailer, with `github_post` set to allow and `escalate.third_party`
  off, still asks (rule `posting`), and with the alias `agent-one` it is allowed. With only `github_post` set to allow
  both ask, because the post affects a third party (rule `escalate`), and the trailer adds its reason.
- `approval_binding` returned the same hash with and without `GH_TOKEN` in the process environment.
- `man git`, `man git-credential` and `man gitcredentials` name neither `GH_TOKEN` nor `GITHUB_TOKEN` (git 2.54.0).
- `tests/integration/test_wave1_acceptance.py::test_core_line_budget` printed 15,076 counted lines, ceiling 15,813,
  headroom left 737.
- `src/exo/consolidate.py` sets `commit.gpgsign=false` for its own commits, and `src/exo/update_verify.py` only
  verifies. Exo has no code that signs git commits.
- Read from the API with `gh` on 2026-10-18: the organization's plan is `free`, the repository is private, and
  `gh api repos/<owner>/<repo>/branches/main/protection` and `.../rulesets` both answered HTTP 403 ("Upgrade to GitHub
  Pro or make this repository public to enable this feature").
- `gh` 2.102.0 on macOS 27.0.1, whose login is in the Keychain (`gh auth status` says so): `gh api user` with an empty
  `GH_CONFIG_DIR` printed "To get started with GitHub CLI, please run: gh auth login". With `GH_TOKEN` set to a
  placeholder it failed with "Bad credentials" and did not fall back to the stored login.

GitHub's pages read for this note, on 2026-10-18: GitHub Terms of Service (B.3), "Managing your personal access
tokens", "Managing private keys for GitHub Apps", "Generating an installation access token for a GitHub App",
"Generating a JSON Web Token (JWT) for a GitHub App", "About authentication to GitHub" (token formats),
"About commit signature verification" (bots), "Registering a GitHub App", "Roles in an organization",
"Adding and removing GitHub App managers in your organization", "Installing a GitHub App from a third party",
"Limiting OAuth app and GitHub App access requests and installations", "Deciding when to build a GitHub App",
"GitHub's plans", "About protected branches", "Rate limits for the REST API", "Permissions required for GitHub Apps",
"Assigning issues and pull requests to other GitHub users", "Requesting a pull request review",
"People who consume a license in an organization", and the `gh` manual pages `gh help environment` and
`gh auth setup-git`.
