# Session summary drill-down prototype

Refs #676. `exo.summary_zoom.SessionSummaryView(store, caller_session, agent)` is a read-only Python API for
session-scoped summary trees already stored in `sessions.sqlite`. A host must establish the caller session
and agent; passing model-selected identities is not authentication. No runtime tool is registered yet.

`describe(node_id)` returns the stored summary, its span and immediate binary children. Child indices,
lanes and contiguous spans must agree before ancestry is asserted. Pending/failed/skipped statuses remain
visible rather than being described as completed summaries. `sources(node_id, after=0)` pages visible original
message references, at most 16 per page. `source(node_id, seq, page=1)` opens the exact stored message JSON,
including original content parts, tool calls/results and metadata, in 30,000-character pages. Concatenating
pages reconstructs the payload. SHA-256 identifies that payload; it does not verify the ledger signature or
prove a summary faithfully represents the source. The original session ID, message ID and sequence remain
attached to each source. Undo-hidden messages are unavailable. Calls never save or regenerate summaries.

```python
from exo.summary_zoom import SessionSummaryView

view = SessionSummaryView(store, authenticated_session, authenticated_agent)
parent = view.describe(summary_id)
children = parent["children"]
references = view.sources(summary_id)
original = view.source(summary_id, references["messages"][0]["seq"])
```

Scope is deliberately narrow: ordinary owned sessions only, excluding private threads and descendants at
any depth. Scope is checked again on each read. Foreign nodes and agent-wide `agent:<id>` trees are refused.
Agent-wide trees use log positions, whereas this API uses session message sequences; substituting one for
the other would attach incorrect provenance. Existing `memory_zoom` remains unchanged.

Remaining work for #676 includes a privacy-aware agent-log tree adapter, runtime/tool and UI navigation,
imported histories and their provenance/ownership model, search integration, and a complete import-to-zoom
acceptance flow. This draft is a tested session-tree slice and does not close that broader issue.
