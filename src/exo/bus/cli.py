"""`python -m exo.bus`: seat client, relay server and operator tools.

  keygen <seat>                         write <keys_dir>/<seat>.key (0600) and print the pubkey
  pubkey                                print my pubkey
  post --to X [--to Y] [--thread T] [--kind K] [--enc-to SEAT] [--sym2p] TEXT
  inbox [--since N]                     messages to me, my team, or the group
  tail                                  follow my inbox live
  thread ID | head | verify
  relay                                 run the relay (needs the `bus` extra)
  registry --leader-key K --leader-seat S --out F [--from F] [--seat s,team,platform,pub[,role[,since]]] [--remove S]
  checkpoint --db F --relay-key K --dir D [--cosign NAME=KEY] [--no-stamp] [--git-push]

Seat: --seat or EXO_BUS_SEAT. Relay URL: --url or EXO_BUS_URL. Keys: --keys-dir or EXO_BUS_KEYS_DIR
(default $EXO_HOME/keys). Legacy DIVAN_* names are read too. Add --json to print raw envelopes.
"""
from __future__ import annotations

import argparse
import json
import sys

from .client import BusError, Client, format_line, keygen
from .config import ClientConfig, RelayConfig, env


def _show(cli: Client, envs, as_json: bool, reg=None, relay_pub=None) -> None:
    for e in envs:
        if as_json:
            print(json.dumps(e, ensure_ascii=False))
        else:
            reg = reg if reg is not None else cli.registry()
            if not e.get("enc") and not cli.signer_of(e, reg):
                text = "<unverifiable plaintext body>"
            else:
                text = cli.text(e, reg, relay_pub)
            print(format_line(e, text))


def build_parser() -> argparse.ArgumentParser:
    cfg = ClientConfig.from_env()
    ap = argparse.ArgumentParser(prog="python -m exo.bus", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=cfg.url)
    ap.add_argument("--seat", default=cfg.seat)
    ap.add_argument("--keys-dir", default=str(cfg.keys_dir))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen").add_argument("seat")
    sub.add_parser("pubkey")
    p = sub.add_parser("post")
    p.add_argument("--to", action="append", required=True)
    p.add_argument("--thread")
    p.add_argument("--kind", default="msg")
    p.add_argument("--enc-to")
    p.add_argument("--sym2p", action="store_true", help="TEXT is a SYM-2P line; send as a structured body")
    p.add_argument("text")
    sub.add_parser("inbox").add_argument("--since", type=int, default=0)
    sub.add_parser("tail")
    sub.add_parser("thread").add_argument("id")
    sub.add_parser("head")
    sub.add_parser("verify")
    sub.add_parser("relay")
    r = sub.add_parser("registry")
    r.add_argument("--leader-key", required=True)
    r.add_argument("--leader-seat", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--from", dest="previous")
    r.add_argument("--seat", dest="seats", action="append", default=[])
    r.add_argument("--remove", action="append", default=[])
    c = sub.add_parser("checkpoint")
    c.add_argument("--db", default=env("DB"))
    c.add_argument("--relay-key", default=env("RELAY_KEY"))
    c.add_argument("--dir", default=env("CHECKPOINT_DIR"))
    c.add_argument("--cosign", action="append", default=[], help="NAME=KEYFILE, repeatable")
    c.add_argument("--no-stamp", action="store_true")
    c.add_argument("--git-push", action="store_true")
    return ap


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    a = build_parser().parse_args([x for x in argv if x != "--json"])
    cli = Client(a.url, a.seat, keys_dir=a.keys_dir)
    try:
        if a.cmd == "keygen":
            _, pub = keygen(a.seat, a.keys_dir)
            print(f"{a.seat} {pub}")
        elif a.cmd == "pubkey":
            print(cli.pubkey)
        elif a.cmd == "post":
            if a.sym2p:
                print(json.dumps(cli.post_sym2p(a.to, a.text, a.kind, a.thread, enc_to=a.enc_to)))
            else:
                print(json.dumps(cli.post(a.to, a.text, a.kind, a.thread, a.enc_to)))
        elif a.cmd == "inbox":
            _show(cli, cli.inbox(a.since), as_json, relay_pub=cli.head().get("relay_pubkey"))
        elif a.cmd == "thread":
            _show(cli, cli.thread(a.id), as_json, relay_pub=cli.head().get("relay_pubkey"))
        elif a.cmd == "head":
            print(json.dumps(cli.head()))
        elif a.cmd == "verify":
            result = cli.verify()
            print(json.dumps(result))
            return 0 if result["ok"] else 1
        elif a.cmd == "tail":
            reg, relay_pub = cli.registry(), cli.head().get("relay_pubkey")
            try:
                for e in cli.tail():
                    _show(cli, [e], as_json, reg, relay_pub)
                    sys.stdout.flush()
            except KeyboardInterrupt:
                pass
        elif a.cmd == "relay":
            from .relay import serve
            serve(RelayConfig.from_env())
        elif a.cmd == "registry":
            from .tools import make_registry
            signed = make_registry(a.leader_key, a.leader_seat, a.out, a.seats, a.previous, a.remove)
            print(f"wrote {a.out}: version {signed['version']}, {len(signed['seats'])} seats")
        elif a.cmd == "checkpoint":
            from .tools import checkpoint
            if not (a.db and a.relay_key and a.dir):
                raise BusError("checkpoint needs --db, --relay-key and --dir (or EXO_BUS_DB, _RELAY_KEY, _CHECKPOINT_DIR)")
            cos = dict(x.split("=", 1) for x in a.cosign)
            checkpoint(a.db, a.relay_key, a.dir, cos, stamp=not a.no_stamp, git_push=a.git_push)
    except BusError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0
