# Per-seat GitHub identity

Status: proposed 2026-10-13 for issue #106. No code. The choice needs the principal's decision because it
creates accounts or apps under the organization and may cost money.

## The problem

Every agent that writes to GitHub today uses the principal's own `gh` login. GitHub therefore credits the
principal with every issue, pull request, review, comment and push an agent makes. A signature line in the body is
the only sign that an agent did it. Three things follow:

- You cannot list one agent's work, or watch it change over time, from GitHub itself.
- Review cannot tell an agent's pull request from the principal's, and branch protection cannot require a human
  review of agent work, because GitHub sees one person.
- An agent's rights are the principal's rights. They cannot be narrowed to one repository or revoked for one
  agent without revoking the principal.

What we want: each seat writes as its own GitHub identity, with only the rights it needs, and its credential never
appears in a transcript, the trace, the ledger or a process list.

## Three ways to get there

### A. One GitHub App per seat

A GitHub App is a non-human identity owned by the organization. It appears as `exo-razi[bot]`. Exo holds the app's
private key (in the Keychain or Secret Service, by reference only) and, at the moment of a `gh` or `git` call,
signs a short JWT, trades it for an installation token, and places that token as `GH_TOKEN` in the call's
environment only. The token expires after one hour.

- Rights: chosen per app (for example issues, pull requests and contents, read and write) and per repository.
- Money: none. Apps are free and do not use an organization seat.
- Work: an organization owner registers one app per seat and installs it on chosen repositories. Exo needs a token
  minter (JWT signing with the app key) and a small cache so a turn does not mint a token per call.
- Limits: an app cannot be assigned an issue or be a requested reviewer, and a few user-only endpoints refuse app
  tokens. Commits pushed with `git` are not marked Verified unless Exo signs them; commits made through the API
  are signed by GitHub. Each app has its own rate limit, which also spreads load away from the principal's.

### B. One machine user account per seat

A machine user is an ordinary GitHub account run by software, created and owned by a human who answers for it.
Each seat logs in with a fine-grained personal access token stored in the Keychain.

- Rights: the token can be scoped to repositories and permissions; the account itself can be added to teams.
- Money: on a paid organization plan each machine user is a paid seat. On the free plan it costs nothing.
- Work: an email address, a password and two-factor setup per account, and token rotation (fine-grained tokens
  expire, up to a year at most unless the organization allows otherwise). Each is a real account to secure.
- Strengths: behaves exactly like a person. It can be assigned issues, requested as a reviewer, and sign commits
  with its own key so they show as Verified.

### C. One bot for all seats, with signed attribution

One GitHub App (or one machine user) acts for every seat. Each write carries the seat's name in a trailer
(`Exo-Seat: exo-razi`) and in the body, and Exo records the write in the ledger signed with that seat's key, as
messages already are (D-040).

- Rights: one set for all seats. Narrowing one agent means narrowing all.
- Money and work: one app, one key, one installation. The least setup.
- Weakness: GitHub still shows one author. Per-seat history needs Exo's ledger or a search on the trailer, and a
  trailer is a claim anyone with the bot's token could write. The ledger signature makes the claim checkable, but
  only by tools that read the ledger.

## Costs side by side

| | A. App per seat | B. Machine user per seat | C. One bot, signed attribution |
|---|---|---|---|
| Money | none | a paid seat each, on paid plans | none |
| Setup per seat | register and install one app | account, email, 2FA, token | none after the first |
| Credential lifetime | one hour, minted on demand | up to a year, rotated by hand | one hour (app) |
| Per-seat rights and revocation | yes | yes | no |
| GitHub shows the seat | yes, as `name[bot]` | yes, as a user | no |
| Assignable, requested reviewer | no | yes | no |
| Exo code needed | token minter, env injection, scrub | env injection, scrub | trailer, ledger link |

All three share the same Exo side: the `[github]` table in `seat.toml` (issue #106), the token reaching only `gh`
and `git` subprocesses through the existing `env_allow` path in `core/seats.py`, scrubbing in the trace and
transcript, author and committer variables set to the seat's identity, and the gate rules for public writes left
as they are. Bypass mode (#96) does not lower them.

## Recommendation

Choose A, a GitHub App per seat. It costs nothing, its tokens die within the hour, each seat's rights are set and
revoked on its own, and GitHub shows each agent as a bot, which is what reviewers need to see. The two things it
cannot do (be assigned an issue, be requested as a reviewer) matter less than they seem: the principal assigns
work over the bus, not on GitHub.

Start with one app for one seat, prove the acceptance tests in #106 on it, then register the rest. Keep B for a
seat that must be a requested reviewer, and only if the organization plan makes the seat cost acceptable. C is the
fallback if registering apps is not possible.

## What the principal decides

1. Approve option A (or choose B or C).
2. Who registers the apps: an organization owner must create and install each one.
3. Which repositories each seat may write to, and with which permissions.
4. Whether agent commits credit the principal with `Co-authored-by`.
