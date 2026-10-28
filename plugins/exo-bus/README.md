# exo-bus

Exo core plugin for Exo Bus (`exo.bus`): signed, hash-chained messages between agent seats. Wire
compatible with Divan Bus v0.1, so seats and relays already running keep working.

## What ships

- **Tools** (toolset `exo-bus`): `bus_post`, `bus_inbox`, `bus_thread`, `bus_verify`. Hidden until the
  profile sets `EXO_BUS_URL` and `EXO_BUS_SEAT`.
- **Channel** `exo_bus`: a protocol client (`exo_bus_hermes/adapter.py`, class `BusChannel`). It polls the
  seat's inbox and hands each message from another seat to the core's ingress path (`peers` command, action
  `bus`) for a session bound to the chat. A message to this seat opens a chat with the sender; one to
  `team:<name>` or `group` lands in that shared chat. The core checks the envelope against the seat's pinned key
  (never the relay's), marks it as written by `bus:<seat>`, and applies the peer policy: held for the principal
  unless a rule lets the seat through (D-040, `docs/PEERS.md` "Bus seats"). The final reply goes back as a signed
  envelope in the same thread only for a turn that an auto-delivered message opened. A message steered or queued
  into another turn posts nothing, and neither does a turn the principal typed into. Encrypted bodies sealed to
  another seat or not openable here advance the cursor without creating placeholder turns. Bus reader tools
  still display unreadable-body placeholders. Register
  it with the core's `register_channel`. The factory takes a link: exod passes its in-process `ChannelLink`
  (caller `channel`, the only caller the core takes a bus message from), and `InProcessLink(engine)` does the same
  for an embedded core.
- **Relay key:** the relay signs a post with its own key only for a seat whose key it does not hold (an MCP post),
  and marks it `{"relay": {"composed_for": seat}}` in `attach`. Readers never take the relay key as the seat's:
  `Client.signer_of` returns the seat's key or nothing.
- SYM-2P bodies (`{"fmt":"sym2p","data":...}`) reach the agent, and the relay's web view when it shows bodies, as
  plain English.

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
HTTP message and inbox reads accept `since` from 0 to 2^63 - 1 and `limit` from 1 to 1,000 (default 200).
`/v1/verify` accepts `from` from 1 to 2^63 - 1 and `to` from 0 to 2^63 - 1; omitting `to` uses the current head.
Malformed or out-of-range numbers answer 422, including negative limits (#455).
Under the Exo gate these files are the principal's: a tool call that writes `$EXO_HOME/bus/` (the registry, the
relay database and its `-wal`, `-shm` and `-journal` files) or the files `EXO_BUS_REGISTRY`, `EXO_BUS_DB` and
`EXO_BUS_KEYS_DIR` name is a `store_write` and asks the principal (`docs/GATE.md`, #210).
The `/mcp` endpoint mounts when the `mcp` package (v2) is installed; set `EXO_BUS_MCP=0` to turn it off.

The read-only web view at `/` takes no signed headers, so anyone who can reach the port can open it. It lists each
message's seq, sender, recipients, kind, time and thread, and hides the body, because the API gives bodies only to
signed seats. `EXO_BUS_WEB_BODIES=1` shows plaintext bodies there too: set it only on a relay that no one but the
seats can reach. Sealed (`enc_to`) bodies never show.

The relay refuses (422) an envelope that it would store in another form than the one signed: a `v` that is not the
integer 1 (`true`, `1.0`), a `thread` that is neither a string nor null, or a text field (`id`, `ts`, `from`,
`kind`, `body`, `prev`, `sig`) that is not a string. Such a row would make `/v1/verify` fail at that seq for good.

## Peers on Hermes (D-026)

`exo_bus_hermes/peers.py` makes a Hermes profile a peer: set `EXO_PEERS=1` (or `peers.enabled: true` under
the plugin settings) and the plugin starts a listener thread, a presence file in the shared peers directory,
the tools `peers` and `peer_send`, the commands `/peers`, `/peer-release` and `/peer-deny`, and the `peers`
skill. See docs/PEERS.md for what it can and cannot do (gateway sessions in particular).

The peers lifecycle adapter is Hermes-specific today. Exo's core currently fires only
`pre_tool_call`, `post_approval_response`, `post_tool_call`, `transform_tool_result`, `on_turn_start` and
`on_turn_end`; the peer adapter registers Hermes hooks beginning with `on_session_start`, which Exo rejects.
The `exo-bus` entry point catches and logs that registration error, so peer mode is incomplete on Exo. Keep
`EXO_PEERS` disabled under Exo until the adapter is ported to supported fire sites. The bus tools and channel
are separate from this peer lifecycle adapter.

Why the bus is still not a Hermes platform: Hermes has `register_platform` (a gateway adapter that turns
chat messages into user input behind its allowlists and pairing), not `register_channel`. A bus adapter would
be a few hundred lines and would admit text from other seats as user-level input, so it needs a security
review of its own. Not done here; bus messages reach Hermes through the `bus_inbox` tool, and peers cover the
live case.

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
