# The action gate: approvals and the outbound scrub

`exo.gate` classifies every tool call and decides allow, log, ask or deny from the principal's
policies (`policies/gates.yaml`, `budget.yaml`, `posting.md`). The `exo-gate` plugin runs it on
each call, records the decision in the signed ledger, and turns an ask into the protocol event
`approval.requested`. This page covers what D-033 added: what an approval covers, who may give
it, how long "always" lasts, and the sweep for internal names in outbound posts.

## What an approval covers

An outward act is a post, message, email, social post, payment or account action, or anything
the gate marks public or irreversible. For those, the gate hashes the exact text, title, target,
tool and arguments (`approval_binding`). Text read from a `--body-file` counts too. The hash is
sent as `binding` on `approval.requested`.

- An answer covers that hash only. If the agent edits one character, the next call has a new
  hash and is asked again. This is the same rule as a review verdict that a new head sha voids.
- A client may echo `binding` in `approval.respond`. If it differs from the open request's hash
  the answer is refused as `stale`, so an approver who read an older draft cannot pass a newer
  one. A client that sends no binding keeps the old behaviour.

## Who may approve

`policies/approvals.yaml` maps a category to who may answer:

    approvers:
      default: [principal]
      categories:
        github_post: ["role:public-reviewer"]
        social_post: ["role:public-reviewer"]
    roles:
      public-reviewer: ["seat:my-lead-seat"]

Entries are `principal`, `seat:<name>` or `role:<name>`. The principal can always answer. With
no file, only the principal can, which is the behaviour of a single-user install. A peer or
ingress client never can (D-026). The allowed list is sent as `approvers` on
`approval.requested` so a client can show who must answer; the engine refuses anyone else as
`forbidden`.

A client answers as a seat by setting `client` to `seat:<name>` and signing the answer with that
seat's Ed25519 key: `sig = exo.core.approvals.sign_approval(key, request_id, decision, binding,
seat)`. The signature covers the request id, the decision, the request's binding hash and the
seat name, so it cannot be reused for another request, decision or text. An unsigned or badly
signed seat answer is refused as `forbidden`. The public key comes from the bus registry when
that document verifies, else from `<keys dir>/<seat>.pub`.

Any other local client answers as the principal. That identity is not signed: it is as strong as
the socket's file permissions (the same OS user).

A seat may approve one exact text with `allow`. Only the principal can answer `allow_always`; a
seat's `allow_always` is refused.

## Standing grants ("always")

An `allow_always` answer becomes a grant with a scope, an expiry and a ledger row
(`phase: grant`). Grants live in the principal store at `state/approval-grants.json` (mode 0600),
or at `$EXO_HOME/state/gate-grants.json` without a store, so they survive a restart. Each use is
logged with the grant id.

- Outward acts: the scope is the category, the target and the exact text hash. There is no
  unscoped "always" for an outward category. Default expiry 7 days (`always.outward_days`).
- Other acts: the scope is the category and the exact target. Default expiry 30 days
  (`always.days`).
- Credentials never get a grant; they ask every time.
- Only the principal can create a grant.
- Set a value to 0 to turn standing grants off for that kind.
- `/grants` (a `command.run` command, no new protocol command) lists live grants;
  `/grants revoke <id>` removes one and writes a ledger row (`phase: revoke`).

## The outbound scrub

Posts to public targets (`posting.applies_to`, by default GitHub posts, messages, email and
social posts) are swept for internal identifiers. A hit holds the post and lists each one, for
example `internal name: my-lead-seat` or `home path: ~/`. The gate builds the list itself:

- seat names from `$EXO_HOME/seats` and the bus registry (`$EXO_HOME/bus/registry.json`), and
  agent names from the store's `agents/`;
- home paths: `/Users/<name>`, `/home/<name>`, `~/`;
- model ids from the model catalog, `provider:model` refs, and `claude-*-<n>` and `gpt-<n>`;
- internal ids of the form `<prefix>_<16 hex>` (sessions, tasks, jobs, approvals);
- the user's own list: `policies/internal-names.txt` (one per line), `$EXO_INTERNAL_NAMES`, and
  `posting.scrub.names` and `posting.scrub.patterns` in `posting.md`.

**Matching tradeoff.** A name that is one plain word (a seat called `canary` or `scout`) matches
only as a whole token, in any case: "scouting" passes, "the scout said" is held. A name with a
hyphen, underscore or digit and four or more characters is distinctive, so it also matches
inside longer words and with any separator ("my lead seat", "my_lead_seat"). The cost of a false
positive is one extra ask; the cost of a miss is a leaked internal name in a public repo, which
cannot be taken back. So the sweep leans to holding. Put a word in `posting.scrub.allow` to stop
a false positive that keeps recurring, or rename the seat.

The sweep runs on public targets only (`posting.scrub.exposures`). Internal team chats and the
principal's own channels are not swept.

## The footer

`posting.footer` is the exact line an outbound post must end with. It is empty by default, so no
line is built in. A principal who wants a disclosure line sets it in `posting.md`, for example
`footer: "> Automated posting by agentic team with human oversight."`, and can limit it to public
targets with `footer_exposures: [public]`.
