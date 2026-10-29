# Swarm engine adapter

`GroupEngineAdapter` connects saved group members to authorized `CoreEngine` instances. The trusted host supplies a separate engine for each profile and explicit reply audiences. It passes `adapter.adapters()` to `GroupRuntime`, then calls `start()` and `wait()` on that runtime.

The adapter checks engine identity, stored session ownership and private session kind before execution. It performs one turn per invocation. GroupRuntime retains execution claims, concurrent scheduling, pause checkpoints and stop receipts. No message automatically starts another turn.

Workers in conductor mode reply only to the conductor. The conductor targets one worker per invocation. Socratic members can address other members. Input includes sender identity and human/member authorship as quoted JSON data.

Only a successful end acknowledges the submitted inputs. Failed, interrupted or cancelled turns leave them pending. Replies are routed before acknowledgement. A crash between those writes requires reconciliation and may have published a reply without consuming its input. There is no exactly-once promise or automatic retry.

This is an internal adapter, not an authentication boundary or OS sandbox. The host authorizes engines and contexts. It must not expose these Python objects directly to models. The adapter neither accesses credentials nor changes model selection, permissions or private-memory configuration.

## Validation

`tests/test_group_engine_adapter.py` covers binding rejection, conductor routing, concurrent starts, failure/interruption receipts and input acknowledgement. It also runs real CoreEngine turns with the fake provider. Live provider execution and client integration are not covered.
