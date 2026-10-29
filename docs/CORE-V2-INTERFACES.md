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
  cost_usd?. Supports `+`. input_tokens is the uncached input on every transport. Cache reads and writes are
  counted apart, so the prompt is the sum of the three (D-080).
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
`status` so the router can fall back). A transport that sees the connection close without a finish event or
a stop reason yields the retryable error "stream ended without a response", even when text came first: a cut
stream is never a normal end. When an error arrives after text the user already saw, the loop stores that text
as an assistant message marked `meta.interrupted` and `meta.error`, as it does for an interrupt (no retry: text
was shown). Common opts: `model`, `max_tokens`, `temperature`, `reasoning`,
`stop`, `tool_choice`; unknown opts are ignored. `tool_choice="none"` keeps the same tools declared and turns tool
use off: Anthropic sends `{"type": "none"}`, OpenAI-compatible transports send `"none"`, and `claude-cli`, which
cannot say it, ignores it. `muse-cli` is text only and refuses any call that declares tools, so a wrap-up on that
route falls through to the next route, as a tool round does.

Round cap (issue #92): a turn that reaches `max_iterations` tool rounds (24) emits the `stopped after N tool rounds`
notice, then makes one more model call with `tool_choice="none"` and a transient `[harness]` prompt asking what was
done, what is open and the next step. The prompt is never stored. The reply is stored with `meta.wrap_up` (and the
route's `meta.model`); a tool call in it is dropped and never runs, and its signed thinking is not kept (it was
signed against the unstored prompt). `stop_reason` stays `max_iterations`. A failed wrap-up stores any shown text
as above, emits the same coded `error` as a failed round (`provider_error`, `model_spend_refused`,
`model_spend_stopped`) and sets the turn's error; another exception is a `harness.error` (`wrap-up`). An interrupt
ends the turn `interrupted`. The call is one more model call and spend-journal entry per capped turn;
`Loop(wrap_up=False)` turns it off.

Codec: `to_dict(obj)` and `from_dict(cls, data, strict=True)` work for every dataclass here and in the
protocol. Nested dataclasses, lists, optionals and Literals are decoded and checked. `strict=True`
rejects unknown fields and wrong-typed `int`, `float`, `str` and `bool` fields: a bool is not an int, an
int is a float, and null fits a primitive field only when it is optional. `strict=False` drops unknown fields
and takes primitive values as stored. The core reads sessions.sqlite messages and events with `strict=False`,
so a row an older core wrote still loads.

Under `strict=False` a Literal value the codec does not know is kept as sent, because a newer peer may add one.
`strict=True` rejects it.

## Protocol (`exo.protocol.schema`), `PROTOCOL_VERSION = 1`

Envelopes:
- `Command`: id (auto `c_...`), type, body. Build with `Command.of(body)`. `to_json()`, `from_json()`.
  Always strict. An unknown type is `unknown_command`, and so is a type that is not a string. Unknown,
  missing or wrong-typed body fields are `bad_body`. A malformed command never ends the connection or the
  stdio transport: the server answers it with an error `Response` and keeps serving. Over HTTP every
  `Response` comes back with status 200, an error one too. A fault the server did not classify, in the
  decoder, the replay or the engine, is `internal`: an `events.subscribe` `since` past SQLite's 64-bit range
  is one. Over HTTP, a request whose `Content-Length`, `since` query value of `GET /events` or
  `Last-Event-ID` header the server cannot use gets status 400 and a JSON `{"error": ...}` body, before any
  body is read or any stream opens. Each of the three must be ASCII digits up to 2^63 - 1, SQLite's largest
  integer (#264). Leading zeros are allowed. A word, a sign, a decimal point or a larger value is a 400. A
  length over 8 MB, within that bound, is 413. An empty `Last-Event-ID` counts as none. A number inside a
  command body still gets a typed `Response`, as the `events.subscribe` case above does. The first command
  starts the core's runtime (the provider readiness probe when no main role is configured, then the MCP
  servers). A start that raises is that command's error, `internal` unless classified, and the next command
  starts again. It does after a cancelled start too (exo-desktop #14), and after a start that failed once its
  only command was cancelled (exo-desktop #2). An MCP server that fails does not fail the start: it is recorded
  in its status and skipped.
- `Response`: id (the command's), ok = True, result: dict = {}, error: ErrorInfo?. Results are
  untyped JSON for now; reuse `SessionInfo` and core types inside them via `to_dict`.
- `Event`: id (auto `e_...`), seq, session?, type, ts (ISO 8601 UTC, auto), body. JSON key order is
  `id, seq, session, type, ts, body`. Build with `Event.of(body, seq, session)`. `from_json(s, strict=True)`;
  clients pass `strict=False` to accept newer servers (unknown fields dropped, unknown types kept as dict,
  unknown Literal values kept). `Client.events` skips and logs an event it still cannot decode, and the feed goes on.
  Note: STANDALONE.md 5.2 drafted the payload key as `data`; it is `body`.
- `ProtocolError(code, message)`: codes `bad_json`, `bad_envelope`, `unknown_command`, `unknown_event`,
  `bad_body`.
- Shared shapes: `SessionInfo` (id, title = "", seat?, model?, cwd?, parent?, created = "", updated = "",
  message_count = 0); `ErrorInfo` (code, message, retryable = False).
  `SessionInfo.status` is `live` while a turn runs and `idle` otherwise, in the results of `session.list`,
  `session.get` and `session.resume`.
- Registries: `COMMANDS` and `EVENTS` map type name to body class; each class has `.TYPE`. The
  `@command` / `@event` decorators refuse duplicates and enforce the cap of 30 each.

Commands (25): the 20 from STANDALONE.md 5.2 plus `session.undo`, `commands.list`, `models.set`,
`taste.list`, `taste.edit` from research/UI.md 4.2. Steer and queue are a mode of `turn.send`.

- `hello` Hello: protocol = 1, client = "", capabilities: list[str] = [], screen_cols?
- `session.create` SessionCreate: title = "", seat?, model?, cwd?
- `session.list` SessionList: limit = 50, seat?
- `session.get` SessionGet: session
  Its queued `steers` list contains message IDs. `Loop.steer` assigns a missing ID only after the running turn
  accepts the message, preserving a supplied ID. Snapshot, cancellation and delivered transcript use that same
  identity. Refusal leaves the message and queue unchanged.
- `session.fork` SessionFork: session, at_message?, title = ""
- `session.resume` SessionResume: session. It closes a tool round a crash left open. While a turn is running in
  the session it only reads, so a second window never writes into a live tool call
- `session.undo` SessionUndo: session, turns = 1
- `turn.send` TurnSend: session, text = "", parts: list[ContentPart] = [], mode `send/queue/steer` = send. A
  scheduled run's or a subagent's turn counts as running (their loops share the engine's per-session state).
  Into a running subagent's session every mode steers, and only the principal may send (D-070, D-052). While
  the child starts or finishes and takes no steer, the send fails with `conflict`
- `turn.interrupt` TurnInterrupt: session
- `approval.respond` ApprovalRespond: request_id, decision `allow/deny/allow_always`, note = "", client = "", binding = "", sig = ""
  (D-033: a `binding` that differs from the request's is refused as `stale`; a client outside the request's
  `approvers` is refused as `forbidden`; a `seat:<name>` client must sign with its seat key, and may not answer
  `allow_always`)
- `question.answer` QuestionAnswer: question_id, answer
- `command.run` CommandRun: name, args = "", session?. Built-ins: trace, findings, context, compact (the cap is full; new commands go here)
- `commands.list` CommandsList: session?
- `events.subscribe` EventsSubscribe: since = 0 (events with seq > since), session? (None = all). A live
  subscriber 4,096 unread events behind (`MAX_LAG` in `exo.server.engine`) has stalled: the core ends its stream
  after the last event it read, and the client resumes with `since` and misses nothing (D-079). In process, the
  terminal window and stdio subscribe again on their own, and their stream ends only when the core shuts down.
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
- `turn.started` TurnStarted: turn_id, model = "", soul = ""
- `turn.completed` TurnCompleted: turn_id, stop_reason = "end", usage?
- `message.delta` MessageDelta: message_id, text
- `message.completed` MessageCompleted: message: Message
- `reasoning.delta` ReasoningDelta: message_id, text
- `tool.started` ToolStarted: call: ToolCall, risk = "low"
- `tool.output` ToolOutput: call_id, text (partial output: what a tool printed since the last loop turn, D-079)
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
