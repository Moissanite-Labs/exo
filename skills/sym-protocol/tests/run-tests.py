#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused tests for scripts/sym-validate.py (stdlib only, no network).

Runs real subprocess invocations of the validator and asserts the exit code and
the typed diagnostic code. Exit 0 iff every case passes.

    python3 tests/run-tests.py
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, "scripts", "sym-validate.py")
FIX = os.path.join(HERE, "fixtures")
STATE = os.path.join(FIX, "state", "receiver-state.json")
EXAMPLE = os.path.join(ROOT, "examples", "t6k45-review-fix-verify-merge.jsonl")

V = lambda *p: os.path.join(FIX, "valid", *p)
I = lambda *p: os.path.join(FIX, "invalid", *p)

# (name, argv, expected_exit, expected_code_or_None)
CASES = [
    # ---- valid: must exit 0 ----
    ("help exits 0", ["--help"], 0, None),
    ("template/brief-packet.json", [os.path.join(ROOT, "templates", "brief-packet.json")], 0, None),
    ("template/receipt-packet.json", [os.path.join(ROOT, "templates", "receipt-packet.json")], 0, None),
    ("worked example (auto)", [EXAMPLE], 0, None),
    ("worked example (strict canonical)", ["--strict-canonical", EXAMPLE], 0, None),
    ("valid packet-assert", [V("packet-assert.json")], 0, None),
    ("valid packet-error-ver", [V("packet-error-ver.json")], 0, None),
    ("valid object-dispute-open", [V("object-dispute-open.json")], 0, None),
    ("base matches receiver state", ["--state", STATE, V("packet-update-current.json")], 0, None),

    # ---- invalid: must exit non-zero with a typed diagnostic ----
    ("truncated JSON", [I("syntax-truncated.jsonl")], 1, "ERR:SYN"),
    ("duplicate keys", [I("duplicate-keys.json")], 1, "ERR:SYN"),
    ("id grammar violated", [I("bad-id-grammar.json")], 1, "ERR:SYN"),
    ("float where integer required", [I("float-score.json")], 1, "ERR:SYN"),
    ("unknown act enum", [I("unknown-act.json")], 1, "ERR:SYN"),
    ("extra property (additionalProperties=false)", [I("extra-property.json")], 1, "ERR:SYN"),
    ("key order violated", [I("reordered-keys.json")], 1, "ERR:SYN"),
    ("multi-recipient packet", [I("multi-recipient.json")], 1, "ERR:POL"),
    ("wrong major version", [I("wrong-major-version.json")], 1, "ERR:SYN"),
    ("exec without authorization", [I("missing-auth-exec.json")], 1, "ERR:AUTH"),
    ("challenge without dissent", [I("challenge-without-dissent.json")], 1, "ERR:SEM"),
    ("verify without evidence", [I("verify-without-evidence.json")], 1, "ERR:SEM"),
    ("update without base", [I("update-without-base.json")], 1, "ERR:SYN"),
    ("dispute missing positions (P3-P5)", [I("protected-dispute-omission.json")], 1, "ERR:SYN"),
    ("losing position dropped at resolution", [I("drop-losing-position.json")], 1, "ERR:SEM"),
    ("expired authorization", ["--now", "2026-09-17T03:00:00Z",
                              I("expired-authorization.json")], 1, "ERR:EXP"),
    ("stale base vs receiver state", ["--state", STATE, I("stale-base.jsonl")], 1, "ERR:VER"),
    ("duplicate (f,id)", [I("duplicate-packet.jsonl")], 1, "ERR:DUP"),
    ("non-canonical serialization", ["--strict-canonical",
                                     I("noncanonical-whitespace.jsonl")], 1, "ERR:SYN"),
    # ---- duplicate downgraded to a warning when explicitly allowed ----
    ("duplicate allowed -> warning only", ["--allow-duplicates",
                                           I("duplicate-packet.jsonl")], 0, "ERR:DUP"),
]


def run(argv):
    proc = subprocess.run([sys.executable, SCRIPT] + argv,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def main():
    failures = []
    for name, argv, want_exit, want_code in CASES:
        rc, out = run(argv)
        problems = []
        if rc != want_exit:
            problems.append("exit %d (wanted %d)" % (rc, want_exit))
        if want_code and want_code not in out:
            problems.append("diagnostic %s missing" % want_code)
        if not want_code and want_exit == 0 and "OK" not in out and "--help" not in argv:
            problems.append("no OK line")
        status = "PASS" if not problems else "FAIL"
        print("%-4s %-52s exit=%d" % (status, name, rc))
        if problems:
            failures.append(name)
            for p in problems:
                print("       -> %s" % p)
            for line in out.strip().splitlines()[:6]:
                print("       |  %s" % line)
    print("")
    print("%d/%d cases passed" % (len(CASES) - len(failures), len(CASES)))
    if failures:
        print("FAILED: %s" % ", ".join(failures))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
