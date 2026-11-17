# Swarm engine adapter

`GroupEngineAdapter` connects saved group members to authorized `CoreEngine` instances. The trusted host supplies a separate engine for each profile and explicit reply audiences. It passes `adapter.adapters()` to `GroupRuntime`, then calls `start()` and `wait()` on that runtime.

The adapter checks engine identity, stored session ownership and private session kind before reading input, then rechecks after the awaited checkpoint before calling the engine. It performs at most one turn per invocation. Empty pending input causes no model call, reply or consumption. GroupRuntime retains execution claims, concurrent scheduling, pause checkpoints and stop receipts. No message automatically starts another turn.

Workers in conductor mode reply only to the conductor. The conductor's one-worker audience is fixed when the adapter is constructed; it cannot choose a different worker per turn. Socratic members can address other members. Input includes sender identity and human/member authorship as quoted JSON data. Engine turns currently use CoreEngine's default `system` author because this adapter passes no ingress metadata. That label grants no human approval authority and does not change the engine's gate checks.

Only a successful end acknowledges the submitted inputs. Failed, interrupted or cancelled turns leave them pending. Replies are routed before acknowledgement. A crash between those writes requires reconciliation and may have published a reply without consuming its input. There is no exactly-once promise or automatic retry.

A successful end with no reply or only whitespace still consumes its submitted
inputs, without publishing a blank group message. Nonblank reply text is preserved.
An interrupted result without a supervisor stop request reports `failed`.
A requested stop reports `stopped` only after the engine call exits and cleanup
finishes, including an engine that catches cancellation and returns `interrupted`.

An empty invocation may still complete the runtime's existing execution claim;
it does not wait for later input or release the claim. The host must queue intended
input before starting. #969's execution/reuse decisions and #1161's message-size
policy remain separate; this adapter does not resolve them.

This is an internal adapter, not an authentication boundary or OS sandbox. The host authorizes engines and contexts. It must not expose these Python objects directly to models. The adapter neither accesses credentials nor changes model selection, permissions or private-memory configuration.

## Validation

`tests/test_group_engine_adapter.py` covers stored owner/kind checks, shared engines,
foreign contexts, binding changes across construction and pause, conductor routing,
failure/interruption receipts, cleanup before stop receipts, and blank replies.
It also runs real CoreEngine turns with the fake provider with and without input.
Live provider execution and client integration are not covered.
