# Retained tool-output excerpt acceptance (#501)

This is a partial source recovery and independently reviewable building block for
#501. The earlier stage-1 implementation (#510, source `7b1a133a`) merged into a
feature branch, and its `tool_evidence.py` is absent from current main and #500.
Its deterministic diagnostic-preserving excerpt idea is implemented here without
transplanting the older session, gate or composition interfaces.

`exo.retained_excerpt.excerpt` accepts already retained, sanitized text and a
character budget. It preserves distinct diagnostic and source-label lines, keeps
head and tail, marks omissions and refuses compression if preservation cannot fit
or the bounded one-megacharacter scan would be exceeded. Short inputs also return
None: retain the original. Tests cover logs, JSON, diffs, repeated errors, Unicode,
malformed Unicode, refusal, bounds and deterministic results.

No runtime imports or invokes this module. Disabled runtime overhead is therefore
zero. This is not output archival, retrieval, redaction, semantic compression or
recovery of bytes discarded by tools. Before runtime integration, implement and
review exact paginated expansion, principal/session authorization, restart-safe
references, source/trust and call/result pairing, cancellation, pre-cap capture
and retention limits. Benchmark prompt size, encoded bytes, CPU, startup and disk
using real integration; these unit tests make no quality/performance claim.
