# Core v2 interfaces (wave 1, fixed before the wave)

Source of truth: `src/exo/core/types.py` and `src/exo/protocol/schema.py`. Stdlib dataclasses only.
Notation: `x?` means `x | None = None`; `= v` is the default; `a/b/c` is a Literal. Change fields only
additively (new optional fields with defaults). Ask the lead before changing anything here.

## Core types (`exo.core.types`)

- `ContentPart`: type `text/image/reasoning`, text = "", data? (base64), mime?, url?, signature?
  (opaque reasoning token, send back unchanged). Helper `ContentPart.of_text(s)`.
- `ToolCall`: id, name, arguments: dict = {} (already parsed JSON).
- `ToolResult`: call_id, content: list[ContentPart] = [], is_error = False. Helpers `of_text()`, `.text()`.
- `Message`: role `system/user/assistant/tool`, content: list[ContentPart] = [], tool_calls: list[ToolCall]
  = [], tool_results: list[ToolResult] = [], id?, meta: dict = {} (never sent to a provider).
  Assistant messages carry `tool_calls`; `tool` messages carry one or more `tool_results`; system prompts
  are `role="system"` messages. Helpers `Message.system/user/assistant/tool(...)`, `.text()`.
- `Usage`: input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens (all 0),
  cost_usd?. Supports `+`.
- `ToolSpec`: name, description, parameters: JSON Schema dict = empty object, risk `low/medium/high` =
  low, toolset = "core".
- `ProviderEvent` (tagged by `kind`): kind `text/reasoning/tool_call/usage/done/error`, text = "",
  tool_call?, usage?, stop_reason? (`end/tool_use/max_tokens/interrupted/error`), message?, error?,
  retryable = False, status? (HTTP code), retry_after? (seconds, from the provider).
- `Provider` (runtime-checkable Protocol): `name: str`;
  `stream(messages, tools, **opts) -> AsyncIterator[ProviderEvent]` (an async generator).

Provider contract: deltas arrive as `text` / `reasoning`; tool calls arrive whole (`tool_call`, the
transport assembles argument deltas); `usage` may repeat, last wins; the stream ends with exactly one
`done` (with `stop_reason` and the assembled assistant `message`) or one `error` (with `retryable` and
`status` so the router can fall back). Common opts: `model`, `max_tokens`, `temperature`, `reasoning`,
`stop`; unknown opts are ignored.

Codec: `to_dict(obj)` and `from_dict(cls, data, strict=True)` work for every dataclass here and in the
protocol. Nested dataclasses, lists, optionals and Literals are decoded and checked. `strict=True`
rejects unknown fields; `strict=False` drops them. Use these for sessions.sqlite rows too.

## Protocol (`exo.protocol.schema`), `PROTOCOL_VERSION = 1`

Envelopes:
- `Command`: id (auto `c_...`), type, body. Build with `Command.of(body)`. `to_json()`, `from_json()`.
  Always strict: unknown type is `unknown_command`; unknown or missing body fields are `bad_body`.
- `Response`: id (the command's), ok = True, result: dict = {}, error: ErrorInfo?. Results are
  untyped JSON for now; reuse `SessionInfo` and core types inside them via `to_dict`.
- `Event`: id (auto `e_...`), seq, session?, type, ts (ISO 8601 UTC, auto), body. JSON key order is
  `id, seq, session, type, ts, body`. Build with `Event.of(body, seq, session)`. `from_json(s, strict=True)`;
  clients pass `strict=False` to accept newer servers (unknown fields dropped, unknown types kept as dict).
  Note: STANDALONE.md 5.2 drafted the payload key as `data`; it is `body`.
- `ProtocolError(code, message)`: codes `bad_json`, `bad_envelope`, `unknown_command`, `unknown_event`,
  `bad_body`.
- Shared shapes: `SessionInfo` (id, title = "", seat?, model?, cwd?, parent?, created = "", updated = "",
  message_count = 0); `ErrorInfo` (code, message, retryable = False).
- Registries: `COMMANDS` and `EVENTS` map type name to body class; each class has `.TYPE`. The
  `@command` / `@event` decorators refuse duplicates and enforce the cap of 30 each.

Commands (25): the 20 from STANDALONE.md 5.2 plus `session.undo`, `commands.list`, `models.set`,
`taste.list`, `taste.edit` from research/UI.md 4.2. Steer and queue are a mode of `turn.send`.

- `hello` Hello: protocol = 1, client = "", capabilities: list[str] = [], screen_cols?
- `session.create` SessionCreate: title = "", seat?, model?, cwd?
- `session.list` SessionList: limit = 50, seat?
- `session.get` SessionGet: session
- `session.fork` SessionFork: session, at_message?, title = ""
- `session.resume` SessionResume: session
- `session.undo` SessionUndo: session, turns = 1
- `turn.send` TurnSend: session, text = "", parts: list[ContentPart] = [], mode `send/queue/steer` = send
- `turn.interrupt` TurnInterrupt: session
- `approval.respond` ApprovalRespond: request_id, decision `allow/deny/allow_always`, note = "", client = "", binding = "", sig = ""
  (D-033: a `binding` that differs from the request's is refused as `stale`; a client outside the request's
  `approvers` is refused as `forbidden`; a `seat:<name>` client must sign with its seat key, and may not answer
  `allow_always`)
- `question.answer` QuestionAnswer: question_id, answer
- `command.run` CommandRun: name, args = "", session?. Built-ins: trace, findings, context, compact (the cap is full; new commands go here)
- `commands.list` CommandsList: session?
- `events.subscribe` EventsSubscribe: since = 0 (events with seq > since), session? (None = all)
- `search` Search: query, scope `sessions/store/all` = all, limit = 20
- `config.get` ConfigGet: key? (None = whole config)
- `config.set` ConfigSet: key, value: Any
- `models.list` ModelsList: no fields
- `models.set` ModelsSet: model, role = "main", session? (None = default)
- `tools.list` ToolsList: session?
- `skills.list` SkillsList: no fields
- `tasks.list` TasksList: session?
- `seat.list` SeatList: no fields
- `taste.list` TasteList: no fields
- `taste.edit` TasteEdit: action `add/update/remove`, rule_id?, text = ""

Events (21): the 20 from 5.2 plus `taste.updated` (D-012 inline taste).

- `session.created` / `session.updated`: session: SessionInfo
- `turn.started` TurnStarted: turn_id, model = ""
- `turn.completed` TurnCompleted: turn_id, stop_reason = "end", usage?
- `message.delta` MessageDelta: message_id, text
- `message.completed` MessageCompleted: message: Message
- `reasoning.delta` ReasoningDelta: message_id, text
- `tool.started` ToolStarted: call: ToolCall, risk = "low"
- `tool.output` ToolOutput: call_id, text (partial output)
- `tool.completed` ToolCompleted: result: ToolResult, duration_ms = 0
- `approval.requested` ApprovalRequested: request_id, summary, risk = "medium", call?, options =
  [allow, deny, allow_always], approvers: list[str] = [] (`principal`, `seat:<name>`; empty = any local
  client that is not a peer), binding = "" (sha256 of the exact outbound text, target and arguments; D-033)
- `approval.resolved` ApprovalResolved: request_id, decision, by = ""
- `question.asked` QuestionAsked: question_id, text, options: list[str] = []
- `context.compacted` ContextCompacted: before_tokens, after_tokens, summary_message_id?
- `handoff.written` HandoffWritten: path, summary = ""
- `task.updated` TaskUpdated: task_id, title = "", status = "", child_session?, detail = ""
- `usage.updated` UsageUpdated: usage: Usage, session_total?
- `decision.queued` DecisionQueued: decision_id, title, source = "", detail = ""
- `taste.updated` TasteUpdated: rule_id, action `add/update/remove`, text = "", path = ""
- `notice` Notice: text, level `info/warn` = info
- `error` Error: code, message, retryable = False, command_id?

## Package owners (wave 1)

`src/exo/core/models/` W1-A, `src/exo/core/tools/` W1-B, `src/exo/core/loop.py`, `sessions.py`,
`context.py` W1-C, `src/exo/core/plugins.py`, `slots.py`, `hooks.py`, `src/exo/upstream.py`,
`PROVENANCE.toml` W1-D, `src/exo/protocol/` (not `schema.py`) and `src/exo/server/` W1-E.
`types.py` and `schema.py` belong to the lead. Hermes notice: `LICENSES/hermes-agent-MIT.txt`.
