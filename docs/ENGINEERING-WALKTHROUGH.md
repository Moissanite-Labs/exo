# Exo engineering walkthrough

Exo is a standalone agent runtime with terminal and browser clients. This walkthrough follows one turn through
its code, then shows how memory, permission checks and experimental improvements fit together.

Source snapshot: main `8c9d1676a31b2c6980625c29c416aff976fae9ce`, inspected 2026-11-03. Paths below refer to
that tree. This is a reading route, not a claim that every deployment runs that revision. Start with the
[developer guide](DEVELOPER-GUIDE.md) to build an extension and the [docs map](DOCS-MAP.md) for the full index.

## 1. Compose the runtime. Keep the client outside it

[`exo.compose.create`](../src/exo/compose.py) selects the seat, plugins and executor and builds the engine.
[`CoreEngine`](../src/exo/core/engine.py) handles commands. The terminal and browser speak the
[shared protocol](../src/exo/protocol/schema.py), rather than owning a second memory database or agent loop.
The [repository workflow](REPOSITORY-WORKFLOW.md) keeps these shared mechanisms in Exo. Native shell and
packaging belong to Exo Desktop.

The architectural choice is to put model routing, context and tool execution behind the same runtime boundary.
A UI can change without becoming a second implementation of permissions or memory. It does not follow that a
shared-core test proves a native desktop build works: downstream integration needs separate evidence.

Read next: [core interfaces](CORE-V2-INTERFACES.md), [engine split](ENGINE-SPLIT.md).

## 2. Follow a turn from input to saved events

In [`Loop.send`](../src/exo/core/loop.py), the session must exist and remain open. The loop tags the message's
author and uses a per-session lock. A steer arriving while the turn accepts steers enters a queue instead.

`Loop._turn` repairs incomplete history, appends the input, emits the turn-start event and runs model/tool
rounds. `Loop._add` writes each message before publishing its completed event. At the end, the loop records
usage, timing and the stop reason, then runs applicable memory hooks and the end-of-turn observer.

This ordering matters after interruption: stored messages and an event stream have different jobs. A client
receives events to render progress. The store retains the record from which a session resumes. See
[`tests/core/loop/test_loop.py`](../tests/core/loop/test_loop.py), especially
`test_tool_rounds_and_events_written_before_publish` and `test_resume_after_restart_and_repair`.
These are local test cases with controlled providers, not proof of a successful live provider call.

Read next: [core](CORE.md), [trace](TRACE.md), [background jobs](JOBS.md).

## 3. Separate original messages from their compressed view

[`SessionStore`](../src/exo/core/sessions.py) records threads and messages. Agent log positions join ordinary
threads into one ordered history. [`AgentContext`](../src/exo/core/context.py) builds a summary tree over that
history. [`AgentLogView`](../src/exo/core/agentlog.py) exposes search and zoom back into original entries.
The [memory reference](MEMORY-MODEL.md) specifies the system prefix, tree frontier, verbatim tail, context
notes and recall that make up a model call.

This makes compression inspectable: a summary is a view over retained messages, not a replacement for their
source. Hash-chain verification checks the stored chain's consistency. It does not prove that a statement is
true, that a summary preserves every relevant fact, or that an untrusted writer could never rewrite a local
store. The [ledger authority reference](LEDGER-AUTHORITY.md) explains the separate authority boundary.

Private threads need their own treatment. They remain stored, but are excluded from other threads' memory
views and the principal learning path. The privacy guarantee is about those paths, not deletion from disk.
Read the memory reference's limits, including harness findings. Tests begin at
[`test_agent_log.py`](../tests/core/test_agent_log.py) and
[`test_agent_context.py`](../tests/core/test_agent_context.py).

## 4. Carry permission checks to the execution boundary

The model proposes a tool call. [`Registry.call`](../src/exo/core/tools/registry.py) applies the tool and hook
contract. The [gate](GATE.md) classifies the action and binds approval to the exact operation being reviewed.
The [executor contract](EXECUTORS.md) carries detached, immutable JSON arguments in an
[`ExecutionRequest`](../src/exo/core/tools/executor.py).

The important distinction is between a model's requested action, the principal's authority to allow it and the
trusted code that executes it. A peer message is input, not consent. An executor is trusted installed Python,
not an untrusted-plugin sandbox. Gate parsing also has documented limits. A passing test suite is not a proof
of OS isolation. Start with [ingress authority tests](../tests/security/test_ingress_authority.py) and
[approval identity tests](../tests/security/test_approval_identity.py), then the gate's “Known limits.”

## 5. Improve a harness without silently activating its proposals

[Harness² experiments](integrations/HARNESS2.md) are an instructive extension: source research becomes a small,
explicit API rather than a claim that the whole upstream system has been adopted.
[`run_cycle`](../src/exo/improvement/harness2.py) proposes general and domain notes, probes behavior, composes
both candidates and probes again. Scores and reference answers stay outside editor contexts. Exo's evaluation
gate decides whether a proposal is retained. The [offline CLI](../src/exo/improvement/offline.py) adds disjoint
held-out fixtures and exports evidence for review.

The resulting proposal is not active policy, principal intent or signed-ledger authority. Synthetic fixture
success tests this mechanism, not general agent intelligence or production gains. No live effectiveness or
benchmark advantage is claimed. Reproduce the examples and focused tests in the integration reference.

## 6. Read the contribution, not just the component names

Exo combines a persistent, inspectable history with replaceable model transports, explicit action authority
and measured improvement proposals. Its engineering contribution is in the contracts between these parts:
what survives a restart, what enters a model call, who may approve an action and which evidence can promote a
change. The [source and adaptation register](SOURCES.md) separates reused code, method adaptations and
inspiration so those contributions remain distinguishable from upstream work.

For a new claim, attach the source, the changed mechanism, its test and the limit the test does not establish.
For a performance claim, also attach the baseline, workload, model/version, environment, sample size, cost and
reproduction command. Until those exist, describe the mechanism without a speed, cost or quality promise.
