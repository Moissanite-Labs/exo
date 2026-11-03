# Retained tool-output views (D-085)

**Status: In progress.** Stage 1 of issue #501 is implemented for review.

Large tool results can be shown to the model as short excerpts while the original
retained text stays in the session transcript. The `tool_output` tool reads exact
pages when the model needs more detail. This feature is off by default.

## Enable

```sh
exo config set tool_output '{"enabled":true}'
exo stop
```

Launch Exo again. This setting works with any harness profile, including `lean`,
without a memory plugin. `exo config set tool_output.enabled false`, followed by
a restart, removes the renderer and retrieval tool. It does not delete history.

Optional settings live in the same `tool_output` object:

| Setting | Default | Allowed range |
|---|---|---|
| `enabled` | `false` | Boolean |
| `threshold_chars` | `8000` | 1,024–100,000 |
| `preview_chars` | `2400` | 512–16,000; less than `threshold_chars` |
| `page_chars` | `12000` | 512–24,000 |
| `retention_messages` | `400` | 10–10,000 |

Enabled settings are validated before plugins or runtime resources start. A plugin
tool named `tool_output` causes a startup conflict. It is never silently replaced.
Configuration changes require a restart.

## What changes

The model sees a labeled excerpt with the tool name, call ID and a reference such as
`te1:m_...:0:...`. Head and tail text surround an omission marker. Distinct diagnostic
and source-label lines are retained. If those lines exceed the preview budget, the
result stays whole. Error results, images, multipart results and expansion pages
also stay whole. This is deterministic excerpting. No model summarizes the result.

`preview_chars` bounds the excerpt, with a small additional source/reference header.
The adapter considers at most 64 results and 1 MiB of text per context build, with
at most 100,128 characters in any eligible result. Later candidates stay unchanged.
These limits bound the extra work. They do not impose a new global prompt budget.

Stored messages, emitted `tool.completed` events, capture hooks and summary-tree
input keep their original retained text. Only copies in the provider-facing context
change. A result modified by another context provider stays unchanged unless it
still matches the visible stored result and its source call. Main and scheduled turns
use the same view. A delegated child gets the view only if its allowed tools include
`tool_output`.

## Expand a reference

The model calls `tool_output(reference, offset=0, limit=12000)`. The result is JSON
with `text`, `offset`, `next_offset`, `total_chars`, the source tool/message/call,
`is_error`, a SHA-256 of the complete sanitized text and an untrusted-data label on
every page. Use the returned `next_offset` until it is `null`. Offsets count Python
text characters, not bytes or tokens. Concatenated pages reproduce the retained
sanitized text. JSON escaping can shorten a page below the requested limit so that
control characters and Unicode cannot make the registry truncate the JSON envelope.

The runtime supplies the session. The tool accepts no session or file path. Normal
gate and executor dispatch still apply. The gate classifies supported `tool_output` calls as `read`,
so an explicit read-deny policy blocks them. A supported call names a reference in the `te1:` shape
that the view issues, with the index in ASCII digits and no leading zero. Any other string, such as a
file path, keeps the generic classification, and the tool refuses it. A test keeps the gate's pattern
equal to the tool's. The gate cannot see the configured `page_chars`.
It reads a `limit` up to 24,000, the top of the allowed range. The tool refuses a limit above the
configured value, and the call stays a read either way. A test keeps the gate's ceiling at the top
of that range. Unsupported argument shapes retain generic gate
classification and fail schema validation before execution. An executor may refuse this tool. Expansion
does not read arbitrary host files or bypass the selected executor.

Known provider keys are redacted before rendering and expansion, in addition to
the registry's existing redaction. If new redaction changes an original after its
reference was issued, the old reference is refused. Build a new view to get a new
reference. This feature does not promise to recognize arbitrary secrets in tool data.

## Ownership and retention

The first enabled context build atomically records the configured principal, engine agent and
current sequence boundary in that session's metadata. The first writer keeps ownership.
Initialization preserves unrelated metadata. Only subsequent tool results
qualify. Earlier results remain unchanged. This avoids assigning old data to whichever
principal happens to enable the feature. Ownership comes from application configuration,
not tool arguments, message text or a model's claim.

References work after restart for that same principal and session. Another session,
agent or configured principal cannot redeem them. Private sessions can expand their
own results. A parent or child cannot use each other's references. A fork starts a new
boundary and cannot redeem the parent's references or compress its copied old results.
Undo hides the result and revokes its reference. A reference also expires when its
message leaves the configured recent-message window. Missing, expired, hidden and
foreign references return an explicit unavailable error.
An expired excerpt is not kept. Once its message leaves the window, the view stops
shortening that result. While the context still holds the message, the model sees the
full original result again, and the old reference is refused. For example, with
`retention_messages` at 10, a 27,000-character result shows as an excerpt of about
2,600 characters. After 10 more messages, the view shows all 27,000 characters.
Expansion serves only results that the view could excerpt. It refuses a reference to an
error result, a result shorter than `threshold_chars`, a `tool_output` page or a result
without its source call, even when the digest matches.

The window limits this retrieval feature. It does not delete the source transcript or
change existing memory access. No duplicate output archive, database, cache, SDK,
network connection or worker is added. Metadata is one small binding per participating
session. When disabled, the feature module is not imported and no feature metadata is
written. Existing session storage and retention remain authoritative.

## Coverage and limits

This implements stage 1 of [#501](https://github.com/aska-digital/exo/issues/501).
It recovers only text already retained by Exo. Shell output keeps at most 30,000
characters before the registry. Registry output is capped at 100,000 characters
plus a truncation marker. Earlier discarded bytes cannot be recovered. Capturing
output before those limits requires a separate bounded capture design.

The adapter does not rewrite existing summaries or reduce the summarizer's input.
It does not enable tools for a restricted child, make trusted Python plugins safe,
or establish a sandbox between processes sharing the same user account.

## Extension and evidence

`exo.tool_evidence.EvidenceContext` decorates the public `TurnContext.build` interface.
Its optional `for_child(context, tools=names)` method wraps a child's context only when
expansion is available. Contexts without that method keep the previous child behavior.
`Evidence` uses `SessionStore`'s public message, sequence and metadata methods.
`update_meta` initializes an absent key and its sequence snapshot in one transaction.
Session locking and context updates use that same atomic metadata contract. Each
expansion reads the target message and preceding source call, without scanning history.

Run `tests/test_tool_evidence.py` for exact pages, restart, undo, principal/session/fork
isolation, redaction, diagnostic preservation, copied messages, real engine dispatch,
child restrictions, executor refusal, and the write order of a lock against the context's
metadata edits. `scripts/benchmark_tool_evidence.py --samples 5` measures fixed log,
JSON, diff, malformed-text and distinct-error fixtures, renderer time, storage growth, and enabled/disabled composition. It makes no model or network calls.
Measurements are engineering evidence, not evidence of better task success.

The [gate comparison](evidence/tool-output-gate-2026-10-23.json) saves all 62 focused cases,
the compared commits and reproduction commands. Across 87 policies it reports nine intentional
`unknown` to `read` lowerings for three supported argument shapes. Unsupported calls retain
generic protections. The comparison exits 1 for the intentional changes, as recorded in D-085.

Measured on 2026-10-23 with Python 3.11.17 on Linux in a shared container. Each fixture
was rendered 100 times with default settings. Times below are process CPU medians.
The root suite and gate comparison ran concurrently. These are shared-container observations,
not isolated startup-cost estimates. Source hashes identify the tested runtime after the
latest main changes and atomic metadata fix.

| Fixture | Original characters | Provider-view characters | CPU per render |
|---|---:|---:|---:|
| Build log | 60,048 | 2,672 | 12.740 ms |
| JSON | 35,790 | 2,645 | 9.738 ms |
| Diff | 27,025 | 2,645 | 9.183 ms |
| Malformed text | 15,000 | 2,645 | 4.911 ms |
| Distinct errors | 31,889 | 31,889 | 2.699 ms |

Five fresh-process startup samples per mode, with order alternating, measured median
composition at 292.910 ms disabled and 459.285 ms enabled. These are noisy local samples
without provider calls or service startup. The feature adds one registered tool.
The disabled samples did not import `exo.tool_evidence`.

All five fixtures added zero bytes to the database/WAL files across 100 renders.
The initial session binding was 78 bytes of JSON for the synthetic principal. Database
page allocation is separate from that logical size. No original output was duplicated.
The [raw measurements](evidence/tool-output-2026-10-23.json) include source fingerprints,
wall timings and every startup sample. Earlier output capture and task-quality evaluation
remain open work.

## Test the optional features together

After the skill discovery, operations inbox and working-notebook changes are also present, run:

```sh
.venv/bin/python scripts/smoke_optional_features.py
```

The script refuses missing features, uses temporary Exo state and blocks network calls.
It exercises the real gate with a fake model, retrieves an excerpt in pages, resumes notes
after restart, preserves workspace instructions and selected skills, and reads the inbox
without constructing the runtime. A second scenario checks disabled behavior.
The [recorded smoke results](evidence/optional-features-smoke-2026-10-23.json) identify
the tested local integration and feature commits. CI and the principal canary remain pending.
