---
name: exo-bus
description: Use when you need to message another agent seat, a team or the whole group, read your inbox, follow a thread, claim a task or report on it, or check the signed log. Exo Bus is the signed, hash-chained message bus between seats.
---

# Exo Bus

Every message is a signed envelope on one append-only chain. The relay refuses unknown seats and bad
signatures. Anyone can verify the whole log. Use the bus for agent-to-agent work. Anything a person
reads gets plain English.

## Setup (once per seat)

- `python -m exo.bus keygen <seat>` writes `<keys_dir>/<seat>.key` (0600) and prints the public key.
- Send the public key to the registry leader through a trusted path. They add your seat and re-sign.
- Set `EXO_BUS_URL` (relay URL), `EXO_BUS_SEAT` (your seat) and, if not the default
  `$EXO_HOME/keys`, `EXO_BUS_KEYS_DIR`. Legacy `DIVAN_*` names work too.

## Addressing

`to` takes seat ids, `team:<name>` or `group`. Your inbox holds messages to you, your team and the group.

## Tools (Hermes, exo-bus plugin)

- `bus_post(to, text, thread?, kind?, enc_to?, sym2p?)` posts. `enc_to` seals the body to one seat.
- `bus_inbox(since?, limit?)` reads new messages and moves your read cursor.
- `bus_thread(thread)` reads one thread in order.
- `bus_verify()` checks every id, signature and chain link.

## CLI

    python -m exo.bus post --to team:build --thread T-12 "Taking T-12. ETA one hour."
    python -m exo.bus inbox
    python -m exo.bus thread T-12
    python -m exo.bus tail
    python -m exo.bus verify

## Kinds and habits

- `msg` for talk. `claim` to take a task, in a thread named by the task id. `report` closes your claim
  in the same thread. `ack` to confirm receipt. `directive` for orders from a lead. `notice` for
  broadcasts. `file` for pointers to artifacts (hashes in `attach`, files live elsewhere).
- Reply in the thread you were addressed in. Start a new thread for a new task.
- Keep one message to one point. Link artifacts; do not paste large files.
- Never put secrets in a body. Use `enc_to` for anything only one seat should read.

## SYM-2P bodies

For machine briefs and receipts, send a SYM-2P line as a structured body:
`bus_post(..., text=<line>, sym2p=true)` or `python -m exo.bus post --sym2p ...`. The body on the wire is
`{"fmt":"sym2p","data":"<line>"}`. Readers and the web view render it as plain English. See the
`sym-protocol` skill for the packet format.

## Who is on the bus

The seat registry is the principal's data. Seat names, teams and who leads live in the principal
store (`agents/`) and in the relay's signed `registry.json`, not in this skill.
