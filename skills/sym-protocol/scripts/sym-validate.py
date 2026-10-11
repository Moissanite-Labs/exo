#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SYM-2P/1.0 packet + blackboard-object validator.

Implements the Architect-locked SYM-2P/1.0 surface (architect-lock.md sections 3-5) only.
Stdlib only. Fails closed: every rejection carries a typed diagnostic.

Typed diagnostic codes
----------------------
Protocol codes (source-verbatim, architect-lock section 3 / cache L471-480):
  ERR:SYN   invalid JSON, schema, key order, grammar, enum, score type
  ERR:SEM   contradictory semantics / protected-semantic omission
  ERR:REF   unresolved object reference
  ERR:VER   base / version mismatch (stale base)
  ERR:EXP   expired message or authorization
  ERR:AUTH  identity or authority failure (missing/invalid authorization)
  ERR:AMB   unsafe ambiguity
  ERR:POL   policy restriction (e.g. multi-recipient packet: illegal in 1.0)

Validator-local code (NOT a wire code; see SKILL.md):
  ERR:DUP   duplicate (f,id) seen in this run. The live protocol returns the
            prior terminal response for a retry; a static validator has no
            prior response to return, so it fails closed instead of guessing.

Exit codes
----------
  0  every document valid
  1  at least one document invalid (typed diagnostics printed)
  2  usage / IO / configuration error (missing file, bad --state, ...)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import os
import re
import sys

# --------------------------------------------------------------------------
# Locked vocabulary (architect-lock.md section 3, source-verbatim, no additions or
# removals). An out-of-enum value is a rejection (L204, L735).
# --------------------------------------------------------------------------

CANON_KEY_ORDER = [
    "v", "id", "tk", "prj", "f", "t", "a", "s", "st", "cf", "err", "cov",
    "ev", "ce", "cond", "ops", "rq", "base", "exp", "auth", "ext",
]
REQUIRED_PACKET_KEYS = ["v", "id", "tk", "prj", "f", "t", "a"]

ACTS = [
    "assign", "assert", "question", "answer", "propose", "accept", "reject",
    "challenge", "verify", "update", "escalate", "done",
]
REQUESTS = ["ack", "upd", "ver", "rev", "exec", "src", "exp", "full", "decide"]
STATUSES = [
    "observed", "inferred", "assumed", "proposed", "disputed", "rejected",
    "verified",
]
ERROR_CODES = [
    "ERR:SYN", "ERR:SEM", "ERR:REF", "ERR:VER", "ERR:EXP", "ERR:AUTH",
    "ERR:AMB", "ERR:POL",
]
# Object type prefixes (L254-256) -> object-file `type` words (cache L276-286).
OBJECT_TYPES = [
    "task", "query", "claim", "hypothesis", "evidence", "plan", "action",
    "result", "decision", "constraint", "unknown", "risk", "artifact", "dispute",
]
TTL_CLASSES = ["ephemeral", "task", "project", "regulated"]
DISPUTE_CLASSES = [
    "fact", "source", "scope", "evaluation", "causal", "value", "plan", "risk",
]
OPS = ["add", "remove", "set", "move"]

ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
REF_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}(:v[1-9][0-9]*)?$")
REF_VERSIONED_RE = re.compile(r"^(?P<obj>[a-z][a-z0-9_-]{0,31}):v(?P<ver>[1-9][0-9]*)$")
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
APPROVAL_REF_RE = re.compile(r"^approval:[A-Za-z0-9_.:-]+$")
ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

# Acts whose subject ref is expected to already exist (resolvable), as opposed
# to being created by the act (L194, L736).
REFERENTIAL_ACTS = ["verify", "update", "accept", "reject", "escalate", "done"]


def E(code, field, msg):
    return {"code": code, "field": field, "msg": msg}


# --------------------------------------------------------------------------
# Strict JSON parsing (duplicate keys, NaN/Infinity, UTF-8, BOM)
# --------------------------------------------------------------------------

class DuplicateKey(ValueError):
    def __init__(self, key):
        ValueError.__init__(self, "duplicate object key: %r" % (key,))
        self.key = key


def _pairs_hook(pairs):
    seen = set()
    for k, _v in pairs:
        if k in seen:
            raise DuplicateKey(k)
        seen.add(k)
    return dict(pairs)


def _no_constants(name):
    raise ValueError("non-numeric constant %r is not valid JSON" % (name,))


def decode_utf8(raw, findings):
    """Strict UTF-8, no BOM (L-3: UTF-8 JSON object)."""
    if raw.startswith(b"\xef\xbb\xbf"):
        findings.append(E("ERR:SYN", "<document>", "UTF-8 BOM not permitted"))
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        findings.append(E("ERR:SYN", "<document>", "not valid UTF-8: %s" % exc))
        return None


def parse_json(text, findings):
    try:
        return json.loads(
            text, object_pairs_hook=_pairs_hook, parse_constant=_no_constants
        )
    except DuplicateKey as exc:
        findings.append(E("ERR:SYN", "<document>", str(exc)))
    except ValueError as exc:
        findings.append(E("ERR:SYN", "<document>", "invalid JSON: %s" % exc))
    return None


# --------------------------------------------------------------------------
# Primitive checks
# --------------------------------------------------------------------------

def is_int(v):
    """Strict integer: bools are not integers (L204 floats-for-integers rule)."""
    return isinstance(v, int) and not isinstance(v, bool)


def check_id(v):
    return isinstance(v, str) and bool(ID_RE.match(v))


def check_ref(v):
    return isinstance(v, str) and bool(REF_RE.match(v))


def check_ref_array(v, field, findings, require_unique=True):
    if not isinstance(v, list):
        findings.append(E("ERR:SYN", field, "must be an array of refs"))
        return
    seen = set()
    for i, item in enumerate(v):
        if not check_ref(item):
            findings.append(
                E("ERR:SYN", "%s[%d]" % (field, i), "not a valid REF: %r" % (item,))
            )
            continue
        if require_unique and item in seen:
            findings.append(
                E("ERR:SYN", "%s[%d]" % (field, i), "duplicate ref %r (uniqueItems)" % (item,))
            )
        seen.add(item)


def check_score(v, field, findings):
    if not is_int(v):
        findings.append(E("ERR:SYN", field, "must be an integer 0-99, got %s"
                          % type(v).__name__))
        return
    if not (0 <= v <= 99):
        findings.append(E("ERR:SYN", field, "out of range 0-99: %d" % v))


def check_key_order(keys, findings):
    """Canonical order is exact: no reordering, no extra keys (L-4, M-1)."""
    idx = -1
    for k in keys:
        if k not in CANON_KEY_ORDER:
            findings.append(E("ERR:SYN", k, "unknown property (additionalProperties=false)"))
            continue
        pos = CANON_KEY_ORDER.index(k)
        if pos <= idx:
            findings.append(E("ERR:SYN", k, "key order violates canonical order"))
        idx = pos


def check_iso_z(v, field, findings):
    if not isinstance(v, str) or not ISO_Z_RE.match(v):
        findings.append(E("ERR:SYN", field, "must be ISO-8601 UTC (…Z): %r" % (v,)))
        return None
    try:
        return _dt.datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc)
    except ValueError:
        try:
            cleaned = v.replace("Z", "+00:00")
            return _dt.datetime.fromisoformat(cleaned)
        except ValueError:
            findings.append(E("ERR:SYN", field, "unparseable timestamp: %r" % (v,)))
            return None


def check_ops(v, findings):
    if not isinstance(v, list):
        findings.append(E("ERR:SYN", "ops", "must be an array of typed operations"))
        return
    if not v:
        findings.append(E("ERR:SYN", "ops", "must not be empty for a mutation"))
    for i, op in enumerate(v):
        f = "ops[%d]" % i
        if not isinstance(op, dict):
            findings.append(E("ERR:SYN", f, "operation must be an object"))
            continue
        unknown = [k for k in op if k not in ("op", "path", "from", "to", "val")]
        if unknown:
            findings.append(E("ERR:SYN", f, "unknown operation key(s): %s"
                              % ", ".join(sorted(unknown))))
        kind = op.get("op")
        if kind not in OPS:
            findings.append(E("ERR:SYN", f + ".op", "unknown op %r (allowed: %s)"
                              % (kind, ", ".join(OPS))))
            continue
        path = op.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            findings.append(E("ERR:SYN", f + ".path",
                              "restricted JSON Pointer required (leading '/'): %r" % (path,)))
        if "*" in str(path):
            findings.append(E("ERR:SEM", f + ".path", "wildcard mutation is not permitted"))
        if kind == "set" and not ("from" in op and "to" in op):
            findings.append(E("ERR:SYN", f, "op 'set' requires 'from' and 'to'"))
        if kind == "add" and not ("path" in op):
            findings.append(E("ERR:SYN", f, "op 'add' requires 'path'"))
        if kind == "remove" and not ("from" in op or "val" in op):
            findings.append(E("ERR:SYN", f, "op 'remove' requires 'from' or 'val'"))
        if kind == "move" and not ("from" in op and "to" in op):
            findings.append(E("ERR:SYN", f, "op 'move' requires 'from' and 'to'"))


def check_auth(v, findings, now=None):
    """Locked authorization envelope (architect-lock section 7)."""
    if not isinstance(v, dict):
        findings.append(E("ERR:AUTH", "auth", "must be an object"))
        return
    allowed = ("kind", "scope", "approved_at", "expires_at", "ref")
    unknown = [k for k in v if k not in allowed]
    if unknown:
        findings.append(E("ERR:AUTH", "auth", "unknown authorization key(s): %s"
                          % ", ".join(sorted(unknown))))
    for req in allowed:
        if req not in v:
            findings.append(E("ERR:AUTH", "auth." + req,
                              "required in the authorization envelope"))
    if v.get("kind") != "human_approval":
        findings.append(E("ERR:AUTH", "auth.kind", "must be 'human_approval'"))
    scope = v.get("scope")
    if not check_ref(scope):
        findings.append(E("ERR:AUTH", "auth.scope",
                          "must be a versioned object REF: %r" % (scope,)))
    ref = v.get("ref")
    if not isinstance(ref, str) or not APPROVAL_REF_RE.match(ref):
        findings.append(E("ERR:AUTH", "auth.ref",
                          "must be 'approval:<id>': %r" % (ref,)))
    approved = check_iso_z(v.get("approved_at"), "auth.approved_at", findings)
    expires = check_iso_z(v.get("expires_at"), "auth.expires_at", findings)
    if approved and expires and expires <= approved:
        findings.append(E("ERR:AUTH", "auth.expires_at",
                          "expiry must be after approval time"))
    if expires and now is not None:
        if now.tzinfo is None:
            now = now.replace(tzinfo=_dt.timezone.utc)
        if expires <= now:
            findings.append(E("ERR:EXP", "auth.expires_at",
                              "authorization expired at %s (now %s)"
                              % (v.get("expires_at"), now.isoformat())))


def check_ext(v, findings, rq=None):
    """ext is namespaced and optional (L-2, M-7); unknown ext keys are ignored."""
    if not isinstance(v, dict):
        findings.append(E("ERR:SYN", "ext", "must be an object"))
        return
    if "err" in v:
        code = v["err"]
        if code not in ERROR_CODES:
            findings.append(E("ERR:SYN", "ext.err", "unknown error code %r" % (code,)))
        if code == "ERR:VER":
            if rq != "full":
                findings.append(E("ERR:VER", "rq",
                                  "a version-mismatch packet must request rq:\"full\""))
            if "have" not in v:
                findings.append(E("ERR:VER", "ext.have",
                                  "ERR:VER must report the receiver's current version"))
            elif not check_ref(v["have"]):
                findings.append(E("ERR:SYN", "ext.have", "not a valid REF: %r"
                                  % (v["have"],)))
    if "sym2p" in v:
        sym = v["sym2p"]
        if not isinstance(sym, dict):
            findings.append(E("ERR:SYN", "ext.sym2p", "must be an object"))
        else:
            rev = sym.get("rev", "1.0")
            if not isinstance(rev, str):
                findings.append(E("ERR:SYN", "ext.sym2p.rev", "must be a string"))


# --------------------------------------------------------------------------
# Packet validation
# --------------------------------------------------------------------------

def validate_packet(obj, findings):
    if not isinstance(obj, dict):
        findings.append(E("ERR:SYN", "<document>", "packet must be a JSON object"))
        return
    keys = list(obj.keys())
    check_key_order(keys, findings)
    for req in REQUIRED_PACKET_KEYS:
        if req not in obj:
            findings.append(E("ERR:SYN", req, "required field missing"))
    if findings:
        # Structure is not trustworthy yet; still validate what is present below.
        pass

    # v is fixed at integer 2 (L-1). Unknown major versions are rejected.
    if "v" in obj:
        if not is_int(obj["v"]) or obj["v"] != 2:
            got = obj["v"]
            hint = "unknown major version" if is_int(got) and got != 2 else "v must be integer 2"
            findings.append(E("ERR:SYN", "v", "%s (got %r)" % (hint, got)))

    for fld in ("id", "tk", "prj", "f", "t"):
        if fld in obj:
            val = obj[fld]
            if isinstance(val, list):
                findings.append(E("ERR:POL", fld,
                                  "multi-recipient packets are not legal in SYM-2P/1.0 "
                                  "(one packet per recipient)"))
            elif not check_id(val):
                findings.append(E("ERR:SYN", fld, "violates ID grammar: %r" % (val,)))

    if "a" in obj and obj["a"] not in ACTS:
        findings.append(E("ERR:SYN", "a", "unknown speech act %r" % (obj["a"],)))

    if "s" in obj and not check_ref(obj["s"]):
        findings.append(E("ERR:SYN", "s", "not a valid REF: %r" % (obj["s"],)))
    if "st" in obj and obj["st"] not in STATUSES:
        findings.append(E("ERR:SYN", "st", "unknown status %r" % (obj["st"],)))

    for fld in ("cf", "err", "cov"):
        if fld in obj:
            check_score(obj[fld], fld, findings)

    for fld in ("ev", "ce"):
        if fld in obj:
            check_ref_array(obj[fld], fld, findings)

    if "cond" in obj:
        if not isinstance(obj["cond"], list) or not all(
                isinstance(c, str) for c in obj["cond"]):
            findings.append(E("ERR:SYN", "cond", "must be an array of strings"))

    if "ops" in obj:
        check_ops(obj["ops"], findings)

    if "rq" in obj and obj["rq"] not in REQUESTS:
        findings.append(E("ERR:SYN", "rq", "unknown request %r" % (obj["rq"],)))

    if "base" in obj and not check_ref(obj["base"]):
        findings.append(E("ERR:SYN", "base", "not a valid REF: %r" % (obj["base"],)))

    if "exp" in obj:
        if not is_int(obj["exp"]) or obj["exp"] < 0:
            findings.append(E("ERR:SYN", "exp", "must be an integer step index >= 0"))

    if "auth" in obj:
        check_auth(obj["auth"], findings, now=_CURRENT_NOW)
    if "ext" in obj:
        check_ext(obj["ext"], findings, rq=obj.get("rq"))

    # -------- act-conditional structural requirements (Architect-locked) --------
    act = obj.get("a")
    rq = obj.get("rq")

    if act == "update":
        if "base" not in obj:
            findings.append(E("ERR:SYN", "base", "required for every mutation (act=update)"))
        if "ops" not in obj:
            findings.append(E("ERR:SYN", "ops", "required for act=update"))
    if rq == "exec" and "auth" not in obj:
        findings.append(E("ERR:AUTH", "auth",
                          "irreversible/consequential execution requires authorization"))
    if act == "challenge":
        if obj.get("st") != "disputed":
            findings.append(E("ERR:SEM", "st",
                              "a challenge must carry st:\"disputed\""))
        if not obj.get("ce"):
            findings.append(E("ERR:SEM", "ce",
                              "a challenge must preserve counter-evidence (P0-P2)"))
    if act == "verify" and not obj.get("ev"):
        findings.append(E("ERR:SEM", "ev",
                          "verification requires supporting evidence (P0-P2)"))
    if act == "done" and "st" not in obj:
        findings.append(E("ERR:SYN", "st",
                          "act=done must state epistemic status"))


# --------------------------------------------------------------------------
# Blackboard-object validation (architect-lock section 4)
# --------------------------------------------------------------------------

OBJECT_REQUIRED = ["id", "type", "v", "tk", "prj", "by", "at", "hash", "ttl", "body"]
OBJECT_ALLOWED = OBJECT_REQUIRED + [
    "sup", "deps", "acl", "cf", "cov", "err", "subj", "positions", "dtype",
    "need", "status", "resolution", "ext",
]


def validate_object(obj, findings):
    if not isinstance(obj, dict):
        findings.append(E("ERR:SYN", "<document>", "object must be a JSON object"))
        return
    for k in obj:
        if k not in OBJECT_ALLOWED:
            findings.append(E("ERR:SYN", k, "unknown object property"))
    for req in OBJECT_REQUIRED:
        if req not in obj:
            findings.append(E("ERR:SYN", req, "required object metadata missing"))
    if not check_id(obj.get("id")):
        findings.append(E("ERR:SYN", "id", "violates ID grammar: %r" % (obj.get("id"),)))
    if not check_id(obj.get("tk")):
        findings.append(E("ERR:SYN", "tk", "violates ID grammar: %r" % (obj.get("tk"),)))
    if not check_id(obj.get("prj")):
        findings.append(E("ERR:SYN", "prj", "violates ID grammar: %r" % (obj.get("prj"),)))
    if not check_id(obj.get("by")):
        findings.append(E("ERR:SYN", "by", "violates ID grammar: %r" % (obj.get("by"),)))
    if obj.get("type") not in OBJECT_TYPES:
        findings.append(E("ERR:SYN", "type", "unknown object type %r" % (obj.get("type"),)))
    v = obj.get("v")
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        findings.append(E("ERR:SYN", "v", "object version must be an integer >= 1"))
    if not isinstance(obj.get("hash"), str) or not HASH_RE.match(obj.get("hash", "")):
        findings.append(E("ERR:SYN", "hash", "must be 'sha256:<64 hex>' (canonical content hash)"))
    if obj.get("ttl") not in TTL_CLASSES:
        findings.append(E("ERR:SYN", "ttl", "unknown retention class %r (allowed: %s)"
                          % (obj.get("ttl"), ", ".join(TTL_CLASSES))))
    if not isinstance(obj.get("body"), dict):
        findings.append(E("ERR:SYN", "body", "must be an object (content stored once)"))
    for fld in ("sup", "deps"):
        if fld in obj and not isinstance(obj[fld], list):
            findings.append(E("ERR:SYN", fld, "must be an array of refs"))
    if "sup" in obj and isinstance(obj["sup"], list):
        for i, r in enumerate(obj["sup"]):
            if not check_ref(r):
                findings.append(E("ERR:SYN", "sup[%d]" % i, "not a valid REF: %r" % (r,)))
    if "acl" in obj and not isinstance(obj["acl"], list):
        findings.append(E("ERR:SYN", "acl", "must be an array of agent ids"))

    if obj.get("type") == "dispute":
        _validate_dispute(obj, findings)


def _validate_dispute(obj, findings):
    """P3-P5: dissent survives; class + needed resolution are required."""
    positions = obj.get("positions")
    if not isinstance(positions, list) or not positions:
        findings.append(E("ERR:SYN", "positions",
                          "a dispute must preserve at least one position object"))
    else:
        for i, pos in enumerate(positions):
            f = "positions[%d]" % i
            if not isinstance(pos, dict):
                findings.append(E("ERR:SYN", f, "position must be an object"))
                continue
            if not check_id(pos.get("by")):
                findings.append(E("ERR:SYN", f + ".by", "violates ID grammar: %r"
                                  % (pos.get("by"),)))
            if pos.get("stance") not in ACTS:
                findings.append(E("ERR:SYN", f + ".stance", "unknown stance %r"
                                  % (pos.get("stance"),)))
            check_score(pos.get("cf"), f + ".cf", findings)
            if not isinstance(pos.get("ev"), list) or not all(
                    check_ref(e) for e in pos.get("ev", [])):
                findings.append(E("ERR:SYN", f + ".ev", "must be an array of REFs"))
    if obj.get("dtype") not in DISPUTE_CLASSES:
        findings.append(E("ERR:SYN", "dtype", "unknown dispute class %r (allowed: %s)"
                          % (obj.get("dtype"), ", ".join(DISPUTE_CLASSES))))
    if not check_ref(obj.get("need")):
        findings.append(E("ERR:SYN", "need", "needed resolution must be a REF: %r"
                          % (obj.get("need"),)))
    status = obj.get("status")
    if status not in ("open", "resolved"):
        findings.append(E("ERR:SYN", "status", "must be 'open' or 'resolved'"))
    if status == "resolved":
        res = obj.get("resolution")
        if not isinstance(res, dict):
            findings.append(E("ERR:SEM", "resolution",
                              "a resolved dispute requires a resolution record"))
            return
        for req in ("selected", "changed_evidence", "adjudicator",
                    "remaining_uncertainty", "action_impact", "positions_preserved"):
            if req not in res:
                findings.append(E("ERR:SEM", "resolution." + req,
                                  "resolution contract field missing"))
        preserved = res.get("positions_preserved")
        if isinstance(preserved, list) and isinstance(positions, list):
            if len(preserved) != len(positions):
                findings.append(E("ERR:SEM", "resolution.positions_preserved",
                                  "losing positions were dropped: %d preserved of %d"
                                  % (len(preserved), len(positions))))


# --------------------------------------------------------------------------
# Duplicate + stale-base state checks
# --------------------------------------------------------------------------

class RunState(object):
    def __init__(self, allow_duplicates=False):
        self.seen = {}
        self.allow_duplicates = allow_duplicates

    def check_duplicate(self, obj, where, findings, warnings):
        f, i = obj.get("f"), obj.get("id")
        if not (isinstance(f, str) and isinstance(i, str)):
            return
        key = (f, i)
        if key in self.seen:
            msg = "duplicate (f,id)=%s/%s; first seen at %s" % (f, i, self.seen[key])
            if self.allow_duplicates:
                warnings.append(E("ERR:DUP", "id", msg + " (allowed by --allow-duplicates)"))
            else:
                findings.append(E("ERR:DUP", "id", msg))
        else:
            self.seen[key] = where


def load_state(path, findings):
    """id -> current integer version, from JSON map or JSONL object rows."""
    try:
        with io.open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        findings.append(E("ERR:REF", "state", "cannot read state file: %s" % exc))
        return None
    text = decode_utf8(raw, findings)
    if text is None:
        return None
    state = {}
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            doc = json.loads(stripped)
        except ValueError as exc:
            findings.append(E("ERR:REF", "state", "invalid state JSON: %s" % exc))
            return None
        if "id" in doc and "v" in doc:
            state[doc["id"]] = doc["v"]
        else:
            for k, val in doc.items():
                if isinstance(val, dict) and "v" in val:
                    state[k] = val["v"]
                elif is_int(val):
                    state[k] = val
        return state
    for line in stripped.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if isinstance(doc, dict) and "id" in doc and is_int(doc.get("v")):
            state[doc["id"]] = doc["v"]
    return state


def check_against_state(obj, state, findings, require_refs=False):
    """Stale base -> ERR:VER (fail closed, no silent merge). Refs -> ERR:REF."""
    if state is None:
        return
    if isinstance(obj.get("base"), str):
        m = REF_VERSIONED_RE.match(obj["base"])
        if m and m.group("obj") in state:
            current = state[m.group("obj")]
            if int(m.group("ver")) != current:
                findings.append(E("ERR:VER", "base",
                                  "stale base %s; receiver holds %s:v%d — resend with "
                                  "rq:\"full\" or recompute the delta (no silent merge)"
                                  % (obj["base"], m.group("obj"), current)))
    act = obj.get("a")
    if require_refs:
        refs = []
        for fld in ("ev", "ce"):
            for r in obj.get(fld, []) or []:
                if isinstance(r, str):
                    refs.append((fld, r))
        if isinstance(obj.get("s"), str) and act in REFERENTIAL_ACTS:
            refs.append(("s", obj["s"]))
        for fld, r in refs:
            m = REF_VERSIONED_RE.match(r)
            obj_id = m.group("obj") if m else r
            if isinstance(obj_id, str) and ID_RE.match(obj_id):
                if obj_id not in state:
                    findings.append(E("ERR:REF", fld, "unresolved reference %r" % (r,)))
                elif m and state[obj_id] != int(m.group("ver")):
                    findings.append(E("ERR:VER", fld, "reference %s is stale; receiver "
                                      "holds %s:v%d" % (r, obj_id, state[obj_id])))


# --------------------------------------------------------------------------
# Document iteration
# --------------------------------------------------------------------------

def iter_documents(path, findings):
    if path == "-":
        raw = sys.stdin.buffer.read()
        documents = _split(raw, path, findings)
    else:
        try:
            with io.open(path, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            findings.append(E("ERR:SYN", path, "cannot read file: %s" % exc))
            return
        documents = _split(raw, path, findings)
    for locator, text, noncanonical in documents:
        yield locator, text, noncanonical


def _try_single(text):
    """Return the parsed object when `text` is exactly one JSON object."""
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def _line_docs(text, path):
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        out.append(("%s:%d" % (path, n), line.strip(), _noncanonical(line.strip())))
    return out


def _split(raw, path, findings):
    """`.jsonl` is one object per line; anything else is whole-file first, then lines."""
    text = decode_utf8(raw, findings)
    if text is None:
        return []
    stripped = text.strip()
    if not path.endswith(".jsonl"):
        if _try_single(stripped) is not None:
            return [("%s:1" % path, stripped, _noncanonical(stripped))]
    return _line_docs(text, path)


def _noncanonical(line):
    try:
        obj = json.loads(line)
    except ValueError:
        return False
    canon = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return canon != line


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

_CURRENT_NOW = None
STRICT_CANONICAL = False


def validate_document(locator, text, findings, warnings, run_state, state,
                      kind, require_refs, noncanonical):
    if STRICT_CANONICAL and noncanonical:
        findings.append(E("ERR:SYN", "<document>",
                          "not canonical compact serialization (L-3/L-4, M-2); "
                          "run --canonicalize"))
    obj = parse_json(text, findings)
    if obj is None:
        return
    if not isinstance(obj, dict):
        findings.append(E("ERR:SYN", "<document>", "top level must be a JSON object"))
        return
    resolved = kind
    if kind == "auto":
        if "type" in obj and "a" not in obj:
            resolved = "object"
        elif "a" in obj:
            resolved = "packet"
        else:
            findings.append(E("ERR:SYN", "<document>",
                              "cannot classify: neither a packet (needs 'a') nor a "
                              "blackboard object (needs 'type')"))
            return
    if resolved == "packet":
        validate_packet(obj, findings)
        run_state.check_duplicate(obj, locator, findings, warnings)
        check_against_state(obj, state, findings, require_refs=require_refs)
    else:
        validate_object(obj, findings)


def main(argv=None):
    global _CURRENT_NOW, STRICT_CANONICAL
    parser = argparse.ArgumentParser(
        prog="sym-validate.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Validate SYM-2P/1.0 packets and blackboard objects (stdlib only).",
        epilog=(
            "exit codes: 0=valid, 1=invalid (typed diagnostics), 2=usage/IO error.\n"
            "typed codes: ERR:SYN ERR:SEM ERR:REF ERR:VER ERR:EXP ERR:AUTH ERR:AMB "
            "ERR:POL ERR:DUP(validator-local)\n"
            "examples:\n"
            "  sym-validate.py packet.json\n"
            "  sym-validate.py --kind packet examples/t6k45-review-fix-verify-merge.jsonl\n"
            "  sym-validate.py --state state.json --require-refs packets.jsonl\n"
            "  sym-validate.py --now 2026-09-17T00:00:00Z --strict-canonical packets.jsonl\n"
            "  sym-validate.py --canonicalize packets.jsonl\n"
        ),
    )
    parser.add_argument("files", nargs="*", metavar="FILE",
                        help="JSON or JSONL file(s); '-' reads stdin")
    parser.add_argument("--kind", choices=["auto", "packet", "object"], default="auto",
                        help="document kind (default: auto-detect)")
    parser.add_argument("--state", metavar="FILE",
                        help="receiver state (JSON map or JSONL objects) for base/reference "
                             "checks; stale base => ERR:VER (fail closed)")
    parser.add_argument("--require-refs", action="store_true",
                        help="with --state, require ev/ce/s refs to resolve")
    parser.add_argument("--now", metavar="ISO8601Z",
                        help="current time for authorization expiry checks (ERR:EXP)")
    parser.add_argument("--allow-duplicates", action="store_true",
                        help="report duplicate (f,id) as a warning instead of ERR:DUP")
    parser.add_argument("--strict-canonical", action="store_true",
                        help="require compact canonical serialization (no insignificant "
                             "whitespace)")
    parser.add_argument("--canonicalize", action="store_true",
                        help="print the canonical compact form of each document and exit")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="emit a JSON report instead of text lines")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress per-document OK lines")
    args = parser.parse_args(argv)

    if args.now:
        probe = []
        _CURRENT_NOW = check_iso_z(args.now, "--now", probe)
        if probe:
            sys.stderr.write("--now: %s\n" % probe[0]["msg"])
            return 2
    STRICT_CANONICAL = args.strict_canonical

    if not args.files:
        parser.print_help(sys.stderr)
        return 2

    state = None
    if args.state:
        probe = []
        state = load_state(args.state, probe)
        if probe and state is None:
            for f in probe:
                sys.stderr.write("%s: %s: %s\n" % (args.state, f["code"], f["msg"]))
            return 2

    if args.canonicalize:
        rc = 0
        for path in args.files:
            for locator, text, _nc in iter_documents(path, []):
                probe = []
                obj = parse_json(text, probe)
                if obj is None:
                    sys.stderr.write("%s: %s\n" % (locator, probe[0]["msg"]))
                    rc = 1
                    continue
                sys.stdout.write(json.dumps(obj, ensure_ascii=False,
                                            separators=(",", ":")) + "\n")
        return rc

    run_state = RunState(allow_duplicates=args.allow_duplicates)
    report = []
    any_invalid = False
    for path in args.files:
        read_probe = []
        docs = list(iter_documents(path, read_probe))
        if read_probe:
            any_invalid = True
            for f in read_probe:
                report.append({"file": path, "locator": path, "code": f["code"],
                               "field": f["field"], "msg": f["msg"]})
            continue
        for locator, text, noncanonical in docs:
            findings, warnings = [], []
            validate_document(locator, text, findings, warnings, run_state,
                              state, args.kind, args.require_refs, noncanonical)
            invalid = bool(findings)
            any_invalid = any_invalid or invalid
            report.append({"file": path, "locator": locator, "valid": not invalid,
                           "findings": findings, "warnings": warnings})

    if args.as_json:
        sys.stdout.write(json.dumps(
            {"valid": not any_invalid, "documents": report}, indent=2,
            ensure_ascii=False) + "\n")
    else:
        for entry in report:
            invalid = not entry.get("valid", True)
            if not invalid:
                if not args.quiet:
                    sys.stdout.write("OK  %s\n" % entry["locator"])
            for f in entry.get("findings", []):
                sys.stdout.write("%s: %s: %s: %s\n"
                                 % (entry["locator"], f["code"], f["field"], f["msg"]))
            for w in entry.get("warnings", []):
                sys.stdout.write("%s: %s(warning): %s: %s\n"
                                 % (entry["locator"], w["code"], w["field"], w["msg"]))
    return 1 if any_invalid else 0


if __name__ == "__main__":
    sys.exit(main())
