# Peers: live agent sessions that message each other (D-026)

Inspired by Claude Code's cross-session messages: one running session tells another what it is doing, and
the other changes course at its next step. In Exo this works between Exo sessions, Hermes agents and bus seats.

## What you get

- `/peers` (TUI) or the `peers` tool lists live sessions on the machine: name, running or idle, folder.
  A seat with one session is just its name (`razi`); with several it is `razi/3fa91c2b`.
- `/send <peer> <text>` (you typing) or the `peer_send` tool (an agent) sends a message.
- A running target takes it at its next tool round. An idle target queues it for its next turn, and wakes
  only if its seat allows (`wake`).
- The receiver sees `peer message from <name>`, framed as a teammate, never as the user. The sender gets
  a receipt: delivered, held, released, denied or expired. The tool result says plainly when a message is
  held, so the model does not claim delivery or resend.

## Who may send: the receiving seat decides

| Sender | Default |
|---|---|
| you, typing `/send` from a client of the same principal | auto |
| another session of the same principal, same machine | auto |
| a different or unknown principal | hold |
| a bus seat (signed by its pinned key) | hold |
| bad or missing signature, or a bus seat whose key changed | deny (cannot be changed) |

Hold means an approval card (TUI and web) shows the sender and text. Allow delivers it; deny drops it;
nothing happens in 30 minutes and it expires. A held message never enters the model's context.

Change it per seat in the seat file (or `peers` in config.json):

```toml
[peers]
wake = true                      # a peer may start a turn on an idle session (default: off while a client is attached)
hold_minutes = 30
policy = { other = "deny" }      # by trust: principal, same-principal, other, remote (a bus seat)
rules = [{ from = "razi*", outcome = "auto" }]   # a bus seat is matched by its seat id
enabled = true
```

## Bus seats (D-040)

The exo-bus channel brings messages from other seats on the Exo Bus. It does not start turns itself. For each
inbox envelope it calls the `peers` command with action `bus`, and the core takes it from there
(`src/exo/bus/ingress.py`):

1. **Check the signature against the seat's pinned key.** The first message from a seat pins the key the bus
   registry lists for it (in `$EXO_HOME/keys/registry.json`). Every later message must verify against that pinned
   key. The relay's own key is never accepted as a seat. A message signed by any other key, or from a seat whose
   registry key has changed, is `unverified` and denied. To accept a seat's new key: `exo keys pin --seat SEAT PUB
   --replace`.
2. **Apply this seat's peer policy.** A verified bus seat has trust `remote`, held by default. The approval card
   says "Bus message from seat ..."; allow delivers it, deny drops it. A `rules` entry for the seat id can make it
   `auto` or `deny`.
3. **Deliver it as the seat, not as you.** It lands in the bus session for that chat, framed as `[bus message from
   <seat> ...]`, with author `bus:<seat>`. Memory capture and learning never read it as your words.
4. **Reply only when the policy said auto.** The channel posts the turn's final answer back to the bus only for a
   turn that a message the policy delivered on its own started. A held message you released, a denied one, and your
   own typing in a bus session post nothing. The agent can still answer with the `bus_post` tool, which the gate
   checks like any post.

## What a peer cannot do

It cannot approve a gate, change permissions or config, or speak for you. A message that tries (it names an
open approval id, or asks to approve a pending gate) is refused and shown to you. `approval.respond` from a
peer client is refused. The system prompt carries a standing line saying the same.

## How it works

- Each exod writes a presence file in `$EXO_PEERS_DIR`, else `$XDG_RUNTIME_DIR/exo/peers`, else
  `~/.exo-peers` (mode 0600, removed on exit, pruned when its pid is gone). `exo serve` with no running
  daemon in the TUI starts a small socket of its own so the TUI is also reachable. A Unix socket path must stay
  under 104 bytes: when a long `HOME` would push `exo-<pid>.sock` past that, the core uses
  `<tmp>/exo-<uid>/peers` (mode 0700) instead, and says nothing is lost but discovery by peers that use the default
  folder; set `EXO_PEERS_DIR` to a short path to share one.
- A message is a bus-format envelope signed with the sender seat's key. Sender session, target session,
  who wrote it and a nonce are inside the signed `attach`. The receiver checks id, signature (key from the
  sender's presence file), age and replay.
- Local delivery goes over the target daemon's socket with the `peers` protocol command (actions `list`,
  `send`, `deliver`, `receipt`, and `bus` for the bus channel; one command because the protocol has no command slots
  left). Events:
  `peer.message` and `peer.delivery`. Both land in the events table and in `exo trace`.
- Code: `core/ingress.py` (one path for anything outside your keyboard: policy, hold, deliver, receipt; peers, job
  notices and the bus channel use it, the scheduler and MCP Events can plug in later), `core/peers.py`,
  `core/peer_target.py`, `bus/ingress.py`, `gate.decide_peer`. Every message delivered this way carries its author
  (`peer:<name>`, `bus:<seat>`, `job`; MEMORY-MODEL.md "Who wrote it").

## Hermes

The `exo-bus` plugin carries a peers module. Turn it on per profile with `EXO_PEERS=1` or
`plugins.entries.exo-bus.settings.peers.enabled: true`. It starts a listener thread and a presence file
(seat = profile name), and adds the tools, `/peers`, `/peer-release <id>`, `/peer-deny <id>` and an etiquette skill.

- CLI, turn running: `agent.steer` (after the current tool batch, never an interrupt). Not `inject_message`:
  in the CLI that lands on the interrupt queue, which interrupts the turn.
- CLI, idle: queued, given to the model as context on its next turn. A peer starts a turn only when `wake`
  is on (`inject_message`).
- Gateway sessions: the hooks give no session key, so the plugin finds it through the gateway's own session
  store (private state, best effort, not tested against a live gateway). A running gateway turn is steered only
  if the plugin can reach its agent; otherwise the message queues. `inject_message` into a busy gateway
  session becomes a new turn after the current one (it is queued, never steered), and needs
  `allow_gateway_injection: true`. If injection is refused the message queues.
- Held messages are shown to the human in the CLI and answered with `/peer-release` or `/peer-deny`; there
  is no push notice into a gateway chat.
- The bus is still not a Hermes platform: that needs a gateway adapter that admits bus text as user input,
  which deserves its own review.

## Limits

Held messages live in memory: a restart drops them. The web client gets the events through the protocol but
has no `/peers` view yet. Bus sends are posted and not receipted. A bus seat's first key is trusted as the registry
serves it (trust on first use); pin it by hand first if the relay is not yours.
