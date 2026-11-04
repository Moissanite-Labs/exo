# Peers: live agent sessions that message each other (D-026)

Inspired by Claude Code's cross-session messages: one running session tells another what it is doing, and
the other changes course at its next step. In Exo this works between Exo sessions, Hermes agents and bus seats.

## What you get

- `/peers` (TUI) or the `peers` tool lists live sessions on the machine: name, running or idle, folder.
  A seat with one session is just its name (`razi`); with several it is `razi/3fa91c2b`.
  Subagents and scheduled runs are not listed, and a message to a bare seat name never lands in one.
  A message that names a session lands only in one that exists here, is not a subagent and is not finished
  (locked). A scheduled run can still be named by its id. Any other name gets a denied receipt that names the
  session, before the policy runs, so nothing is held for you. The bus channel's session for a chat follows the
  same rule (exo-desktop #52). When such a message also tries to approve something, the warning you get shows in
  a live session, not in the one it named.
- `/send <peer> <text>` (you typing) or the `peer_send` tool (an agent) sends a message.
- A running target takes it at its next tool round. An idle target queues it for its next turn, and wakes
  only if its seat allows (`wake`).
- The receiver sees `peer message from <name>`, framed as a teammate, never as the user. The sender gets
  a receipt: delivered, held, released, denied or expired. The tool result says plainly when a message is
  held, so the model does not claim delivery or resend.

## Who may send: the receiving seat decides

| Sender | Default |
|---|---|
| you, typing `/send` from a client of the same principal, on a seat whose key this home holds | auto |
| another session of the same principal, on a seat whose key this home holds | auto |
| a different or unknown principal | hold |
| a seat of another home, pinned with `exo keys pin --peer` | hold |
| a local peer whose key this home does not hold (the card says `key not pinned`) | hold |
| a bus seat (signed by its pinned key) | hold |
| bad or missing signature, a key other than the one this home holds for that name, a sender name that is not a seat name, or a bus seat whose key changed | deny (cannot be changed) |

"Holds" means a key of this home, never one from a presence file. See "Peer keys" below (#132).

Hold means an approval card (TUI and web) shows the sender and text. Allow delivers it; deny drops it;
nothing happens in 30 minutes and it expires. A held message never enters the model's context. When the sender
named no session and none is live (running, or used within `active_hours`, 12 by default), the card belongs to no
thread: both clients show it in every thread, and allow opens a new thread of the seat for the message. If that
thread cannot open, the message closes as not delivered and a notice says why.

Change it per seat in the seat file (or `peers` in config.json):

```toml
[peers]
wake = true                      # a peer may start a turn on an idle session (default: off while a client is attached)
hold_minutes = 30
policy = { other = "deny" }      # by trust: principal, same-principal, other, remote (a bus seat)
rules = [{ from = "razi*", outcome = "auto" }]   # a bus seat is matched by its seat id
enabled = true
```

A presence file can claim any name. So a rule can deliver a local peer sooner than its policy only when this home
holds its key. A rule that holds or denies applies to every sender. A sender whose key is not held here gets its
rule or the policy for its trust, whichever is stricter.

## Peer keys (#132)

Each daemon's presence file sits in a folder that any program running as you can write. So the receiver never
trusts the key or the principal a presence file names. It checks each message against keys in its own home:

- **A key of this home.** For the receiver's own seat name, its own key. For a seat of this home, the key registry's
  entry for it (`$EXO_HOME/keys/registry.json`), else the `.pub` beside the seat's key file in `keys/`. A seat of
  this home has a seat file here, or its key file in `keys/`. A registry entry decides for its name: a key you
  replaced with `exo keys pin NAME PUB --replace` is refused, even while its old `.pub` is still in `keys/`.
- **A peer pin.** `exo keys pin --peer SEAT PUB` pins a seat of another home. That key checks the seat's messages
  and receipts, and a send to it: the text leaves only when the peer's presence entry verifies against a key
  pinned for that name — the entry carries the seat key's signature, so a file that copied the pinned key beside
  a socket of its own is refused — and the first answer, signed by the receiving seat like a later receipt, is
  verified against it before anything is published (#523, the sender's half of this rule). The first answer is
  signed as `kind: "reply"` and a later receipt as `kind: "receipt"`, and neither is taken for the other (#603
  review). It never signs a ledger
  (D-040). A key for a name that is no seat of this home counts as a peer pin too: one pinned with a plain
  `exo keys pin NAME PUB`, or a `.pub` copied into `keys/`.
- **No key for the name.** The presence file's key shows only that the signature is consistent. The message is held
  as `other`, and its approval card says `key not pinned`.

The trust level comes from this home too:

- A seat checked against a key of this home has the principal its seat file names. The receiver's own seat name has
  the receiver's principal. When that principal is the receiver's, the message is `principal` (you typed it) or
  `same-principal`.
- A key of this home with no seat file, or with a seat file that names no principal, has no principal of its own. An
  unseated daemon and a Hermes profile on this home are such keys. Such a key counts as the principal `config.json`
  names, read from the file when the receiver starts, never from `EXO_PRINCIPAL`. It reaches a daemon as that
  principal only when the daemon runs as that principal, or as none, and has no seat file that names a principal. A
  seat whose seat file names its principal holds it as `other`: `config.json` is not a seat file the receiver can
  tie to the key, and a program running as you outside the gate may change it (a tool call that writes it asks the
  principal since D-074, #125). To have such a key delivered at once, give it a seat file here that names its
  principal (`seats/<name>.toml` with `principal = "<id>"`).
- The principal a presence file names never raises trust, but it can lower it. When it is not the principal this
  home records for the sender, the message is `other`, as before #132. A presence file that names none stands for
  the principal `config.json` names. So in a home that serves ada and bob, ada's Hermes profile or unseated daemon,
  run with `EXO_PRINCIPAL=ada`, is `other` to bob's daemon, even one with no seat file. Two unseated daemons share
  the seat name `exo` and its key, so this is also what tells them apart.
- A seat checked against a peer pin is `other`, because this home has no record of whose seat it is. A `rules`
  entry for its name can deliver it.
- Once this home holds a key for a name, a message under that name signed by any other key is denied. That covers
  a seat of another home that has this seat's own name: two unseated homes are both `exo`. Pin the other one's key
  with `exo keys pin --peer exo PUB`, and its messages are held as `other`.
- A sender name that is not a seat name (lowercase letters, digits, `-` and `_`) is denied before any key is looked
  up, so a path in the name never reads a file. The sender is told `the sender is not a seat name`.
- A later receipt (released, denied or expired) must name the seat the message went to, come from that seat's
  daemon, and verify against a key this home holds for that seat. A receipt from a seat whose key is not held here
  is dropped, so the sender's client shows no later outcome for that message.

To pin a peer from another home, read its public key there: `cat <that home>/keys/<seat>.pub` (a Hermes profile's
key is in the `EXO_HOME` it runs with). Then, in this home, run `exo keys pin --peer <seat> <key>`. Messages from it
are held as `other` until a `rules` entry lets them through.

Seats that share one home hold each other's keys in `keys/`, so they deliver as before. A few setups are now held
or denied where they were delivered before. Each has a fix:

- **A key made again.** A seat's key file was deleted, and Exo made a new one. The registry still pins the old key,
  so the new one is denied (`the signature did not verify`). Pin the new key:
  `exo keys pin <seat> $EXO_HOME/keys/<seat>.pub --replace`.
- **A key outside `keys/`.** A seat file names a key elsewhere. The `.pub` beside it is not read, so the seat is held
  (`key not pinned`). Keep the key in `keys/`, or pin its public key: `exo keys pin <seat> <pub>`.
- **A name that is not a seat id.** A Hermes profile or `EXO_PEERS_SEAT` named `Work` or `my.agent` is denied (`the
  sender is not a seat name`). Use a seat id as the name.
- **A key with no principal of its own** (an unseated daemon, a Hermes profile). It is held as `other` by a seat whose
  seat file names a principal, and by a daemon run with an `EXO_PRINCIPAL` that `config.json` does not name. Give it
  a seat file here that names its principal, or set the principal with `exo config set principal <id>`.

## Bus seats (D-040)

The relay's message and inbox HTTP reads accept `since` from 0 to 2^63 - 1 and `limit` from 1 to 1,000
(defaults: 0 and 200). Public `/v1/verify` accepts `from` from 1 to 2^63 - 1 and `to` from 0 to 2^63 - 1;
omitting `to` verifies through the current head. Out-of-range or malformed numbers answer 422 (#455).
`to=0` remains accepted for an empty log, but an empty or inverted range answers ok false with
checked 0: it verifies nothing, so it must not read as a clean log. The exception is a verify with no
`to`, and `from` at its default of 1, on an empty log, which answers ok true with an empty marker: the
whole log was checked and there is nothing in it. `to` is clamped to the current
head, so the returned range matches what was checked. These bounds match SQLite's integer range and keep read pages bounded.

The exo-bus channel brings messages from other seats on the Exo Bus. It does not start turns itself. For each
inbox envelope it calls the `peers` command with action `bus`, and the core takes it from there
(`src/exo/bus/ingress.py`). Only a channel exod runs can do this: exod gives its channels an in-process link
stamped with the caller `channel`, which no socket, stdio or TCP connection ever gets (`docs/GATE.md` "Who
answered"). Any other caller, a tool subprocess on the socket or the principal's own client, is refused as
`forbidden`, so it cannot pin a seat's key or speak as a seat.

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
   turn that a message the policy delivered on its own opened: the turn's first message is that envelope. A held
   message you released, a denied one, and your own typing in a bus session post nothing. Neither does a message
   steered or queued into a turn that was already running (yours included), nor an auto turn that you or a job
   typed into. The agent can still answer with the `bus_post` tool, which the gate checks like any post.

The channel skips encrypted envelopes sealed to another seat, even when their audience is a shared group.
It also skips bodies that cannot be opened or verified. These envelopes advance the inbox cursor without
creating a session, approval or placeholder turn. The bus reader still displays an unreadable-body placeholder.

## What a peer cannot do

It cannot approve a gate, change permissions or config, or speak for you. A message that tries (it names an
open approval id, or asks to approve a pending gate) is refused and shown to you. `approval.respond` from a
peer client is refused. The system prompt carries a standing line saying the same.

## How it works

- Each exod writes a presence file in `$EXO_PEERS_DIR`, else `$XDG_RUNTIME_DIR/exo/peers`, else
  `~/.exo-peers` (mode 0600, removed on exit, pruned when its pid is gone). Malformed records,
  including a non-object JSON body or a pid that is not an integer, are skipped and pruned; one bad
  record does not hide other live peers. Internal `scan(prune=False)` skips them without deletion. `exo serve` with no running
  daemon in the TUI starts a small socket of its own so the TUI is also reachable. A Unix socket path must stay
  under 104 bytes: when a long `HOME` would push `exo-<pid>.sock` past that, the core uses
  `<tmp>/exo-<uid>/peers` (mode 0700) instead, and says nothing is lost but discovery by peers that use the default
  folder; set `EXO_PEERS_DIR` to a short path to share one.
- A message is a bus-format envelope signed with the sender seat's key. Sender session, target session,
  who wrote it and a nonce are inside the signed `attach`. The receiver checks id, signature, age and
  replay. The signature and the sender's principal come from this home, never from the sender's presence file
  ("Peer keys" above). The presence file says where a daemon listens. It is read only when it is a plain file
  in the peers folder, so the `daemon` an envelope names cannot point at a path or a FIFO. The open never waits,
  and the file type is checked on the open file, so a FIFO renamed into place cannot stall delivery either.
- Local delivery goes over the target daemon's socket with the `peers` protocol command (actions `list`,
  `send`, `deliver`, `receipt`, and `bus` for the bus channel; one command because the protocol has no command slots
  left). Events:
  `peer.message` and `peer.delivery`. Both land in the events table and in `exo trace`.
- Code: `core/ingress.py` (one path for anything outside your keyboard: policy, hold, deliver, receipt; peers, job
  notices and the bus channel use it, the scheduler and MCP Events can plug in later), `exo/peers.py` (the
  transport and `PeerService`, outside the core since D-067), `core/peer_target.py` (`EngineTarget`, the engine's
  ingress target), `bus/ingress.py`, `gate.decide_peer`. Every message delivered this way carries its author
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
serves it (trust on first use); pin it by hand first if the relay is not yours. On the machine itself the gate
guards where those keys come from: a tool call that writes `$EXO_HOME/bus/` or the keys folder asks the principal
(`docs/GATE.md` "The store is credentials", #210).

The peer keys of #132 stop a forged presence file from speaking as one of your seats, and only for programs that
go through the gate. A program running as you outside the gate can still read a seat's key file, or write `keys/`
and the seat files, and then speak as that seat. And any client of a daemon's socket that runs as you can ask it
to `peers send` (D-039). That message arrives as `same-principal` automation, never as you typing: that is by
design.

`config.json` names the principal a key with no seat file counts as toward a daemon whose seat file names none, and
that daemon reads it when it starts. A tool call that writes `config.json` asks the principal since D-074 (#125),
but a program running as you outside the gate can still change it. The key's presence file lowers that only when it
names another principal, and a tool call may write a presence file too. None of this reaches a seat whose seat file
names its principal.

A presence file still says where a daemon listens, and a program that goes through the gate may write one. A
forged file under a peer's name no longer receives the messages sent to that name: every daemon signs its presence
entry with its seat key, and before the text ships `send` re-reads the file and verifies that signature (over the
entry as written) against the keys this home pins for the name (the sender's half of the "Peer keys" rule),
sending to the socket of the entry that verified and nothing when no entry verifies — so the pinned key's public
half copied into a file beside a socket of its own gets no text either. The first answer to a send is signed by
the receiving seat, like a later receipt is; if it does not verify against the keys pinned for that seat here,
nothing is published and the send fails, though the text has already reached that socket. Between two current daemons
this is invisible; a daemon whose presence file or first answer is unsigned, as an older `exo`'s are, or signed by a key
made again that this home has not re-pinned (`exo keys pin --replace`), cannot be sent to while its seat's key is pinned
here—and a name no key is pinned for still follows the receive side's trust ladder, message and reply alike, on an
unsigned file like any other (D-026 amendments: #132, then #523).

The first answer is signed for any envelope, one that does not verify too, so that an honest sender still learns
why its message was refused. It is signed as `kind: "reply"` and a later receipt as `kind: "receipt"`: a first
reply offered to `receipt` is refused, so a socket client that names a held message's id in a junk envelope gets a
signed `denied` that a sender on this build or later refuses (the check is the sender's: an older sender still takes
it), and a receipt offered as a first reply is refused too. An older `exo`'s receipt names no kind and is still taken. A
first reply from a #523 build of `exo` names no kind either, and both sides tell it apart by the `mode` and `session`
fields under its signature, which no receipt has: `receipt` refuses it, and the send path takes it as a reply, so a send
to a #523 build still completes. That matters because the first reply is checked after the text has gone: when a send
fails on its reply, the peer may already have the message, and only the outcome is refused (D-026 amendment, #603
review).
