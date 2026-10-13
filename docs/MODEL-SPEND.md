# Principal model-spend admission

This opt-in core mechanism admits routed model calls against daily and monthly
USD ceilings **under explicitly pinned billing contracts**. It is not a guarantee
about a provider changing its prices or violating its token limits. No live keys,
calls, or charges are needed by the tests. Exo OS must separately install a
protected policy/journal and set `EXO_MODEL_SPEND_REQUIRED=1`; this change does not
modify an OS image or enable spending.

## Contract and configuration

In ordinary core, the principal owns `models.spend` in `config.json`. Seat overrides
cannot replace it. A protected service instead sets `EXO_MODEL_SPEND_POLICY` to an
absolute path on persistent storage, such as `/var/lib/exo/model-spend/policy.json`.
This source replaces the entire user-configured spend policy and implies required
mode. The file and every directory ancestor must be root-owned, without group or
other write permission or symlinks. The loader opens checked directory descriptors
and the final file with `O_NOFOLLOW`, verifies ownership/type on the opened file,
and reads at most 64 KiB. Duplicate fields, unknown policy/price fields and malformed
data fail closed. Protected caps/rates are plain nonnegative decimal strings, with
at most 18 fractional digits and a maximum of 1,000,000 USD; exponents are refused.

Every Router constructor resolves this source, including direct construction.
Explicit journal injection is refused when a protected source is selected. Config
or seat edits cannot redirect its journal, replace its principal, add price contracts
or raise its caps. A router keeps its immutable policy snapshot until restart;
changing the service environment cannot downgrade an already guarded router.
An empty, missing, invalid or unreadable selected file never falls back to user config.
Root policy and accounting should live outside root-filesystem rollback together.

Engine startup resolves the seat principal before checking policy and importing
plugins. An explicit environment principal conflicting with the seat is refused.
Every seat for that principal must use the same journal and UUID. All routes
share both caps. This model-only budget is separate from the gate's tool-cost
policy (`budget.yaml`); it does not claim combined purchase/tool/model accounting.

A trusted operator first calls `SpendJournal.provision(path, principal)` from
`exo.core.models.spend`. Provisioning creates the file exclusively with mode 0600
and returns a UUID to pin in policy. Its directory must already exist. Runtime
opens SQLite with `mode=rw`; it never creates, migrates, resets, or replaces a
missing journal. Example **synthetic test configuration**, not advertised prices:

```json
{
  "principal": "alice",
  "models": {
    "spend": {
      "principal": "alice",
      "journal": "/protected/alice/model-spend.sqlite",
      "journal_id": "UUID-returned-by-provision",
      "daily_usd": "0.50",
      "monthly_usd": "5.00",
      "routes": {
        "openai:gpt-4.1-2025-04-14": {
          "contract": "text-tokens-v1",
          "base_url": "https://api.openai.com/v1",
          "model": "gpt-4.1-2025-04-14",
          "transport": "openai",
          "input_bound": 1047576,
          "max_output": 100,
          "input": "1.00",
          "output": "10.00",
          "cache_read": "0.10",
          "cache_write": "1.25"
        }
      }
    }
  }
}
```

All four prices are explicit USD per million tokens. This configuration attests
that reasoning is included in output tokens and that
these four token rates bound **all** charges for this text-only request, including
long-context premiums, cache writes and any relay markup. Use worst-case rates
when a price tier varies. A low input estimate is not a valid `input_bound`: code refuses a value below the
checked-in full native context ceiling, including wrappers and tool schemas.
Unknown contracts must remain absent; there is no automatic catalog-price or
"probably free" exemption. Endpoint, wire model, transport and price snapshot
must be reviewed when the provider changes. Model aliases that resolve to a
different wire model are refused.

The initial admitted transports are the exact built-in Anthropic Messages and
OpenAI Chat Completions classes. The checked-in native inventory, verified against
primary model/context documentation on **2026-10-13**, admits only:

| Native endpoint | Exact wire model | Minimum input reservation | Maximum output |
| --- | --- | ---: | ---: |
| `https://api.openai.com/v1` | `gpt-4.1-2025-04-14` | 1,047,576 | 32,768 |
| `https://api.anthropic.com` | `claude-opus-5-5` | 1,000,000 | 128,000 |
| `https://api.anthropic.com` | `claude-sonnet-5-5` | 1,000,000 | 128,000 |
| `https://api.anthropic.com` | `claude-haiku-5-5` | 1,000,000 | 128,000 |

Sources: [GPT-4.1 and its dated snapshot](https://developers.openai.com/api/docs/models/gpt-4.1),
[Claude exact IDs and limits](https://platform.claude.com/docs/en/models/overview),
[Claude context and billing token accounting](https://platform.claude.com/docs/en/build-with-claude/context-windows).
User configuration cannot extend this inventory. Whole native context is reserved
even for a one-word prompt. Unsupported aliases, relays/custom endpoints, beta or
custom headers (including a session-header override), other protocols, CLI
subscriptions, images, custom provider subclasses and typed decision endpoints
are refused. Extending support requires a reviewed code change with evidence;
compatibility or an operator-supplied estimate cannot enable a route.

The adapter builds the complete request once, with a bounded `max_tokens` (or
OpenAI `max_completion_tokens`) and passes that same body to HTTP. Requests above
the configured output maximum fail before HTTP. Reservation is deliberately
conservative: full input bound times the highest input/cache rate plus bounded
output times output rate. No approximate tokenizer is used. Prices are frozen
copies; modifying catalog prices or returning `cost_usd` cannot reduce a charge.
HTTPX is used directly (no provider SDK). Its default transport has zero retries;
there is no router fallback or retry after a capped transport failure. Injected
clients/transports are trusted host code, used by offline tests.

Protocol references checked during implementation:
[Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create),
[Anthropic cache token accounting](https://platform.claude.com/docs/en/build-with-claude/prompt-caching),
[OpenAI maximum completion tokens](https://platform.openai.com/docs/api-reference/chat/create).
No price is obtained from these documents or guessed by the mechanism.

The single offline exemption is the exact installed `RuleFake` class. The boundary
calls its class implementation, including its built-in rule selection, rather than
an instance-replaced method. Subclasses, `FakeProvider`, names such as `fake`, local
HTTP endpoints and advertised zero prices receive no exemption. Offline calls require
an identity-valid, unstopped journal in guarded mode, report zero model cost, and
create no paid reservation. Tool calls still pass the normal stop checks, gate and
executor. A policy with zero daily/monthly caps and an empty `routes` object enables
this deterministic offline operation without admitting a paid model. Adding a key
alone cannot enable paid calls under that policy.

## Durable admission and settlement

`BEGIN IMMEDIATE` serializes admission across connections/processes. Amounts are
integer nano-USD; reservations/charges round up and ceilings round down. UTC day
and month are used. Successful final usage settles the reservation using pinned
rates; usage counters must be complete, nonnegative integers within the contract.
OpenAI cached input is subtracted from fresh input; Anthropic cache counters are
additional. Reasoning cannot exceed the output total. Tool-use chunks cannot run
until final usage settles successfully.

An unresolved reservation counts in every subsequent day and month until explicit
reconciliation. Abrupt process death, such as SIGKILL, can prevent the failure handler
from recording a stop. A surviving reservation alone does not stop new calls while
the caps have sufficient remaining headroom; it continues counting at its full bound.
This mechanism does not claim that every uncertain outcome universally stops work.
Settled calls spanning periods count once in each admission and
completion period (one row, no double count within a month). The journal persists
the latest observed UTC time and refuses backward movement; clock correction must
precede resumption. Forward clock jumps are outside what local UTC can prove.

A handled refusal or ambiguous failure persists a stop reason. Errors, cancellation,
missing usage and invalid accounting retain the full reservation. Restart does
not clear them. Subsequent admission and loop tool boundaries check the stop.
Already-running tools cannot be revoked by a concurrent background stop; already
admitted model calls have their own reservations. Pending calls are never assumed
free merely because HTTP failed. Refusals are nonretryable and explicitly ask the
principal to review; a generic approval answer cannot raise a cap or clear a stop.

## Coverage and trust boundary

- Main loop, delegate tiers, title/aux calls, default/background/tree summaries,
  memory compaction, learning and generative decision fallbacks all use the engine
  router and its single principal journal.
- Typed decision API calls bypass streaming, so capped mode refuses them before
  HTTP. The gate propagates this refusal as a block instead of retaining an allow.
- Live onboarding/provider verification probes bypass routing, so capped/required
  mode refuses them, including Muse CLI probes. Metadata listing and loopback
  reachability probes do not perform generation.
- Capped router admission rejects CLI, unpriced routes and custom provider types,
  with only the exact deterministic RuleFake exemption above.
  Direct `Router` use without a journal cannot generate in required mode.

Trusted installed plugins still run host Python and can make arbitrary network
calls, as can separately invoked raw adapters or unrelated processes. Code-search,
external purchased services, embedding adapters not routed here and tool purchases
are not metered by this mechanism. OS enforcement must keep model tools away from
host keys, policy and journal and control the trusted installed plugin inventory.
This is not a network billing firewall or an account-wide provider quota.

An exod-owned journal is writable by trusted installed plugins and other processes
running as the same OS user. Root ownership protects the policy, not accounting
against those peers. Protect the service environment and installed host code;
arbitrary trusted Python can bypass these interfaces entirely.

Missing files, corruption, UUID/principal substitution, and backward time fail
closed. UUID pinning does **not** detect restoration of an older valid snapshot
with the same UUID. Root/host rollback resistance requires an independent monotonic
authority/checkpoint; the existing signed principal-ledger authority is not yet
integrated with this journal. Do not claim rollback-proof spend accounting.

## Principal review and policy changes

Opening an identity-valid journal with a recorded stop is permitted so a restarted
daemon can serve transcripts and status. It does not clear the stop: every model
admission, including RuleFake, and existing loop tool boundaries still refuse work.
`models.list` includes a read-only `spend` object with state (`offline`, `ready`,
`stopped`, or `unavailable`), principal, policy source, daily/monthly caps, current
day/month settled charges, pending reservation total/count, and the stop reason.
USD values are exact nine-place decimal strings. Pending totals are additional to
the settled-period totals. Unavailable accounting reports no invented zero balance.
There is no reset, cap approval or reconciliation operation on this interface.
Administrators can also call `SpendJournal.inspect(policy)` for the same read-only
accounting snapshot. This helper and `status()` open SQLite with `mode=ro`; they do
not request a writer transaction, update observed time, or create state. Normal
engine construction retains a `mode=rw` identity transaction so SQLite can recover
an existing hot rollback journal after a crash, preserving committed reservations
and rolling back incomplete transactions. This native recovery never provisions
or resets the journal identity. Read-only inspection may refuse until that recovery
has run under the journal's owning service user.

Stop engines before reconciliation. Inspect `identity` and `calls` read-only and
obtain provider billing evidence for uncertain calls. There is no expiry/release
operation and no model-callable administrative tool. A trusted operator can call:

```python
SpendJournal.operator_review(policy, settlements={reservation_id: "confirmed USD"}, resume=True)
```

This explicit API records confirmed charges, retaining original reservations and
prices. It refuses to resume while any uncertain calls remain. It can record a
charge exceeding the old bound (a violated provider contract), but never raises
the configured caps; further admission still checks all recorded charges. For a
pre-send refusal with no pending reservations, `operator_review(policy,
resume=True)` explicitly clears the stop after the underlying issue is corrected.
This low-level API is an operator procedure, not a finished budget-review UI.

Policy edits require a principal/admin action and engine restart/revalidation.
The runtime never modifies policy or inherits a new budget from a seat. Reducing
caps does not erase earlier usage. Removing `models.spend` opts ordinary core out;
`EXO_MODEL_SPEND_REQUIRED=1` prevents this downgrade by refusing startup. A protected
OS service must own its environment and select the root-owned policy using
`EXO_MODEL_SPEND_POLICY`. Runtime never provisions a missing policy or journal.
OS first provisioning must be exclusive and limited to a demonstrably fresh store;
missing/partial established state requires explicit administrator repair, not reseeding.
