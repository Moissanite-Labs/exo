---
name: sym-protocol
description: Use when validating or emitting SYM-2P/1.0 packets or blackboard objects. Stdlib validator with typed fail-closed diagnostics, brief/receipt packet templates, and a worked review-fix-verify-merge example.
---

# SYM-2P/1.0 — packet validator and object conventions

Canonical compact JSON over durable file artifacts is the only inter-agent
surface. This skill validates it and fails closed on anything ambiguous.

Provenance: the SYM-2P/1.0 architecture lock and input audit of the team that built it. The
library form ships in the `exo` package as `exo.protocols.sym2p`; this skill keeps the
stand-alone validator so an agent can check packets without the package. Party ids in the
examples and fixtures are generic role names (`orchestrator`, `builder`, `reviewer`, `control`).
A principal's real seat names live in the principal store (`agents/`).

This skill implements the lock; it does not modify architecture, doctrine,
control-plane, ops, or config, and it never touches a live session.

## Quick start

    python3 scripts/sym-validate.py --help                  # exits 0
    python3 scripts/sym-validate.py packet.json             # one packet
    python3 scripts/sym-validate.py examples/t6k45-review-fix-verify-merge.jsonl
    python3 scripts/sym-validate.py --kind object dispute.json
    python3 scripts/sym-validate.py --state receiver-state.json packets.jsonl
    python3 scripts/sym-validate.py --now 2026-09-17T03:00:00Z packet.json
    python3 scripts/sym-validate.py --strict-canonical packets.jsonl
    python3 scripts/sym-validate.py --canonicalize packets.jsonl
    python3 tests/run-tests.py                              # focused suite

Exit codes: `0` every document valid · `1` at least one invalid (typed
diagnostics printed) · `2` usage / IO / configuration error.

`.jsonl` is read one object per line (the wire carrier). Any other suffix is
tried as one whole-file object first, then line by line.

## The locked surface this validates

- Wire field `v` is the integer `2`. "SYM-2P/1.0" is the profile name, not a
  wire value. A major version other than 2 is rejected.
- Required on every packet: `v,id,tk,prj,f,t,a`. Everything else is
  act-conditional and omitted when absent — never emitted as `null`.
- Canonical key order is exact:
  `v,id,tk,prj,f,t,a,s,st,cf,err,cov,ev,ce,cond,ops,rq,base,exp,auth,ext`.
  Reordering, extra keys, and unknown keys are rejections
  (`additionalProperties: false` on the envelope).
- Grammar: `ID := [a-z][a-z0-9_-]{0,31}`; `REF := ID [":v" [1-9][0-9]*]`.
  A version never hides inside the identifier, and nothing may follow a value: an id,
  ref, hash or timestamp with a trailing newline is `ERR:SYN`.
- Enums are fixed and source-verbatim:
  acts `assign assert question answer propose accept reject challenge verify update escalate done`;
  requests `ack upd ver rev exec src exp full decide`;
  statuses `observed inferred assumed proposed disputed rejected verified`;
  error codes `ERR:SYN ERR:SEM ERR:REF ERR:VER ERR:EXP ERR:AUTH ERR:AMB ERR:POL`.
- Scores `cf`, `err`, `cov` are integers 0–99 and are routing signals only.
  Floats, booleans, and out-of-range values are rejected. Do not compare raw
  scores across heterogeneous profiles.
- `ev`/`ce` are unique arrays of REFs; `t` is a single recipient (a group send
  is N packets plus a routing-state row).
- `ops` (act `update`) are typed delta operations (`add|remove|set|move`) on a
  restricted JSON Pointer subset; wildcard mutation is rejected.

Blackboard objects (`--kind object`) carry the header block of the lock's
section 4: `id,type,v,tk,prj,by,at,hash,ttl,body` plus `sup/deps/acl/ext` and
the per-type surface. `hash` is `sha256:<64 hex>` over canonical content;
`ttl` is one of `ephemeral|task|project|regulated`; a resolved `dispute` must
carry a resolution record whose `positions_preserved` has the same length as
`positions` (dissent is never deleted).

## Fail-closed semantics

Every rejection is a typed diagnostic on stdout as
`<file>:<line>: <CODE>: <field>: <message>`. The validator never guesses, never
partially applies, and never falls back to a lenient interpretation.

| Situation | Result |
|---|---|
| invalid JSON, duplicate object key, BOM, non-UTF-8 | `ERR:SYN` |
| unknown enum, bad ID/REF grammar, float score, extra key, key-order break | `ERR:SYN` |
| `v` not exactly integer 2 | `ERR:SYN` |
| non-canonical whitespace under `--strict-canonical` | `ERR:SYN` |
| `update` without `base` or `ops` | `ERR:SYN` |
| challenge without `st:"disputed"`/counter-evidence; verify without evidence | `ERR:SEM` |
| resolved dispute with no resolution record, or a dropped losing position | `ERR:SEM` |
| `rq:"exec"` without an authorization envelope | `ERR:AUTH` |
| malformed authorization (bad scope/ref/timestamps, expiry before approval) | `ERR:AUTH` |
| `auth.expires_at` at or before `--now` | `ERR:EXP` |
| `base` older than the version in `--state` | `ERR:VER` (reports the receiver's current version; the packet must request `rq:"full"`) |
| `ext.err:"ERR:VER"` packet that does not request `rq:"full"` or omit `ext.have` | `ERR:VER` |
| `--require-refs` and an `ev`/`ce`/`s` ref is unknown | `ERR:REF` |
| multi-recipient `t` | `ERR:POL` |
| duplicate `(f,id)` in this run | `ERR:DUP` |

### About `ERR:DUP`

`ERR:DUP` is a validator-local code, not a wire code. The live protocol is
idempotent: a retry keeps the original `id` and payload hash and the receiver
returns the prior terminal response without re-running work. A static
validator has no prior terminal response to return, so it fails closed rather
than assuming the duplicate is a legitimate retry. Pass
`--allow-duplicates` when you are deliberately replaying a stream; the duplicate
is then printed as a warning and the exit code stays 0.

### Stale base, never a silent merge

Without `--state`, `base` is only syntax-checked (a run with no receiver
knowledge cannot know what is current). With `--state`, a base older than the
receiver's version yields `ERR:VER` and the fix is explicit: resend with
`rq:"full"` for a snapshot, or recompute the delta against the current version.
The validator contains no merge path, so none can be invoked accidentally.

### Protected semantics (P0–P9)

`cond/ev/ce` (safety, evidence, counter-evidence), dispute positions with
`dtype`/`need` (dissent, scope, provenance), and `err/cov/cf`, `auth`, `base`,
`hash`, `exp` (uncertainty, irreversibility, approval, integrity) are preserved
by the act-conditional checks above. Dropping a P-class field for compression is
a defect, not an optimization — encode it, do not delete it.

## Files

- `scripts/sym-validate.py` — the validator (stdlib only, Python 3.8+).
- `templates/brief-packet.json` — canonical `assign` brief packet.
- `templates/receipt-packet.json` — canonical `done` receipt packet.
- `examples/t6k45-review-fix-verify-merge.jsonl` — worked
  review → fix → verify → merge loop: inbound English scope, relay
  authorization, a stale-base attempt and its typed `ERR:VER` reply, the
  corrected delta, verification, a challenge plus dispute object, the authorized
  merge execution, and the closing `done` with a decision object holding the
  English user-facing summary.
- `tests/run-tests.py` and `tests/fixtures/**` — focused suite: valid fixtures
  pass, and malformed / stale / duplicate / protected-semantic fixtures fail
  with typed diagnostics.

Templates are pretty-printed for humans and stay valid JSON. The wire form is
compact: run `--canonicalize` to normalize a document before writing it as a
packet line, and `--strict-canonical` in review to prove it.

## English boundary

English is required at the user boundary (relay), in dispute records, and in
the authorization approval record, which holds the exact user-approved action
and scope. Packets carry a reference (`auth.ref` = `approval:<id>`), not the
private conversation. Keep the human-facing text inside object `body` fields or
`cond` strings — never invent prose fields inside a packet envelope.
