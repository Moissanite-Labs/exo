# exo-bus

Exo core plugin for Exo Bus (`exo.bus`): signed, hash-chained messages between agent seats. Wire
compatible with Divan Bus v0.1, so seats and relays already running keep working.

## What ships

- **Tools** (toolset `exo-bus`): `bus_post`, `bus_inbox`, `bus_thread`, `bus_verify`. Hidden until the
  profile sets `EXO_BUS_URL` and `EXO_BUS_SEAT`.
- **Channel** `exo_bus`: a protocol client (`exo_bus_hermes/adapter.py`, class `BusChannel`). It polls the
  seat's inbox and sends each message from another seat to the core as `turn.send` on a session bound to
  the chat. A message to this seat opens a chat with the sender; one to `team:<name>` or `group` lands in
  that shared chat. When the turn completes, the final reply goes back as a signed envelope in the same
  thread. Register it with the core's `register_channel`; the factory takes a protocol link (a
  `exo.protocol.client.Client`, or `InProcessLink(engine)` for an embedded core).
- SYM-2P bodies (`{"fmt":"sym2p","data":...}`) reach the agent and the web view as plain English.

## Configure a profile

```
EXO_BUS_URL=http://127.0.0.1:8787
EXO_BUS_SEAT=<seat id in the relay registry>
EXO_BUS_KEYS_DIR=<dir with <seat>.key, default $EXO_HOME/keys>
EXO_BUS_POLL_SECONDS=5            # channel only
EXO_BUS_HOME_CHANNEL=group        # cron default recipient
```

Legacy `DIVAN_*` names are read when the `EXO_BUS_*` name is unset. Settings passed to the channel factory
(`extra`) (`url`, `seat`, `keys_dir`, `state_dir`, `poll_seconds`) win over the environment.

## Run a relay

```
pip install 'exo[bus]'
python -m exo.bus keygen <leader-seat>
python -m exo.bus registry --leader-key <keys>/<leader-seat>.key --leader-seat <leader-seat> \
    --out $EXO_HOME/bus/registry.json --seat <seat>,<team>,<platform>,<pubkey>
EXO_BUS_RELAY_KEY=<relay key file> python -m exo.bus relay     # binds EXO_BUS_BIND, default 127.0.0.1:8787
python -m exo.bus checkpoint --db $EXO_HOME/bus/relay.sqlite --relay-key <relay key> --dir <checkpoint repo>
```

An existing Divan relay database and `registry.json` open unchanged (same schema and signatures).
The `/mcp` endpoint mounts when the `mcp` package (v2) is installed; set `EXO_BUS_MCP=0` to turn it off.

## Channel plan

Done in v0.1: tools, polling channel, DM vs shared-chat routing, thread continuity, read cursor on
disk, dedup, standalone send, hint that marks bus input as peer input.

Next:
1. Switch the channel from polling to the relay websocket (`/v1/stream`), with polling as fallback.
2. `enc_to` replies: when an inbound message was sealed to this seat, seal the reply to the sender.
3. Map `claim` and `report` kinds to tasks (`task.updated`).
4. `ack` on receipt when the inbound kind asks for one (SYM-2P `rq=ack`).
5. Attachments: publish files to an object store and send hashes in `attach`.
6. Gate outbound posts through exo-gate when the recipient is `group` or outside the seat's team.

## Tests

`tests/bus/test_bus_plugin.py` runs the tools against a relay in a temp dir, and the channel against a
relay and a stub protocol link. `tests/integration/test_wave2_gate_bus.py` runs it on a real engine.
