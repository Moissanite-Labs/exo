"""SYM-2P/2.0 dense packet lines (draft profile): parse, validate, render, migrate.

Vendored from aska-digital/protean-sym2p, scripts/protean-sym2p/
(sym2_parse.py, sym2-validate.py, sym2-render.py, sym2-migrate.py) at commit
2d93ed603e94ddd3e4ddcab4b1d5fc4227832a1b (MIT, Copyright (c) 2026 ASKA Digital).
See NOTICE in this package. Changes from the source: one module, no CLIs; the
renderer and migrator take an optional `roles` mapping so a deployment can show
its own party names instead of the generic role words. The wire grammar is unchanged.

Line shape: V|ID|TK|F|T|A|ST|CF|CV|BODY|RQ|BASE|EXP[|SUM]
"""

import hashlib
import re

TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
BASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*:[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
SYM = re.compile(r"^(?:\*|[A-Za-z][A-Za-z0-9_./-]*)$")
REF = re.compile(r"^\$[A-Za-z][A-Za-z0-9_./-]*$")
SUM = re.compile(r"^[0-9a-f]{16}$")

ROLES = {0: "reserved", 1: "coordinator", 2: "builder", 3: "verifier",
         4: "scout", 5: "critic", 6: "operator", 7: "swarm"}
ACTS = {0: "none", 1: "assign", 2: "acknowledge", 3: "assert", 4: "update",
        5: "receipt", 6: "challenge", 7: "dispute", 8: "authorize",
        9: "query", 10: "inform", 11: "delegate", 12: "report"}
STATES = {0: "none", 1: "proposed", 2: "accepted", 3: "rejected",
          4: "verified", 5: "open", 6: "closed", 7: "superseded",
          8: "expired"}
RQS = {0: "none", 1: "acknowledgement required", 2: "verification required"}
UNITS = {"sec": "sections"}

FIELD_NAMES = ["V", "ID", "TK", "F", "T", "A", "ST", "CF", "CV",
               "BODY", "RQ", "BASE", "EXP"]
FIELD_NAMES_RCP = FIELD_NAMES + ["SUM"]


def tokenize(text):
    tokens, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "()":
            tokens.append(c)
            i += 1
        elif c.isspace():
            i += 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in "()":
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def parse_all(text):
    """Parse concatenated s-expressions. Returns (exprs, error)."""

    def parse(pos):
        pos += 1  # consume '('
        items = []
        while pos < len(tokens) and tokens[pos] != ")":
            if tokens[pos] == "(":
                sub, pos = parse(pos)
                items.append(sub)
            else:
                items.append(tokens[pos])
                pos += 1
        if pos >= len(tokens):
            raise ValueError("unbalanced parentheses")
        return items, pos + 1

    tokens = tokenize(text)
    try:
        exprs, pos = [], 0
        while pos < len(tokens):
            if tokens[pos] != "(":
                raise ValueError("top-level atom outside expression")
            expr, pos = parse(pos)
            exprs.append(expr)
    except ValueError as ex:
        return None, str(ex)
    return exprs, None


def is_num(t):
    return isinstance(t, str) and t.isdigit()


def is_sym(t):
    return isinstance(t, str) and bool(SYM.match(t))


def is_ref(t):
    """A $name reference to a bound variable."""
    return isinstance(t, str) and bool(REF.match(t))


def is_atom(t):
    return is_num(t) or is_sym(t) or is_ref(t)


def as_op(e, want):
    if not isinstance(e, list) or not e or e[0] != str(want):
        return "want (%d ...)" % want
    return check_expr(e)


def check_expr(e):
    """Check one s-expression against the opcode grammar. None means ok."""
    if not isinstance(e, list) or not e:
        return "empty expression"
    if not (isinstance(e[0], str) and e[0].isdigit()):
        return "expression head is not an opcode"
    op, args = int(e[0]), e[1:]
    if op == 10:
        if len(args) != 2 or not is_sym(args[0]):
            return "scope wants (10 target impl)"
        return as_op(args[1], 20)
    if op == 11:
        if len(args) != 1:
            return "constraint wants (11 constraint)"
        return as_op(args[0], 21)
    if op == 12:
        if len(args) != 1:
            return "accept wants (12 accept)"
        return as_op(args[0], 22)
    if op == 20:
        if len(args) != 2 or not is_sym(args[0]):
            return "impl wants (20 unit range)"
        r = args[1]
        if isinstance(r, list):
            if not (len(r) == 2 and all(is_atom(x) for x in r)):
                return "range wants (lo hi)"
        elif not is_atom(r):
            return "range wants (lo hi) or a single atom"
        return None
    if op == 21:
        if len(args) != 1 or not is_sym(args[0]):
            return "nomod wants (21 paths)"
        return None
    if op == 22:
        if len(args) != 3 or not is_sym(args[0]) \
                or not is_num(args[1]) or not is_num(args[2]):
            return "exit wants (22 prog ok bad)"
        return None
    if op == 30:
        if len(args) != 2 or not is_sym(args[0]):
            return "bind wants (30 name expr)"
        v = args[1]
        if isinstance(v, list):
            return check_expr(v)
        if not is_atom(v):
            return "bind value must be an atom or expression"
        return None
    if op == 31:
        if len(args) != 3:
            return "if wants (31 test then else)"
        for i, a in enumerate(args):
            if isinstance(a, list):
                if not a:
                    if i != 2:
                        return "only the else branch may be ()"
                    continue
                err = check_expr(a)
                if err:
                    return err
            elif not is_atom(a):
                return "if branches must be atoms or expressions"
        return None
    if op == 32:
        if len(args) != 3 or not is_sym(args[0]):
            return "each wants (32 var items body)"
        items = args[1]
        if isinstance(items, list):
            if not items or items[0] != "33":
                return "each iterates a (33 ...) list"
            err = check_expr(items)
            if err:
                return err
        elif not is_ref(items):
            return "each iterates a (33 ...) list or a $reference"
        body = args[2]
        if isinstance(body, list):
            return check_expr(body)
        if not is_atom(body):
            return "each body must be an atom or expression"
        return None
    if op == 33:
        for a in args:
            if not is_atom(a):
                return "list items must be atoms"
        return None
    if op in (34, 35):
        name = "lt" if op == 34 else "eq"
        if len(args) != 2 or not all(is_atom(a) for a in args):
            return "%s wants (%d a b)" % (name, op)
        return None
    if op == 40:
        if len(args) != 3 or not is_num(args[0]) \
                or int(args[0]) not in ROLES:
            return "spawn wants (40 role thread task)"
        if not TOKEN.match(args[1]):
            return "spawn thread must be a token"
        task = args[2]
        if isinstance(task, list):
            return check_expr(task)
        if not is_atom(task):
            return "spawn task must be an atom or expression"
        return None
    return "unknown opcode %d" % op


def check_scope(exprs):
    """Check that every $reference resolves to a visible bind.

    bind names are visible to the top-level expressions that follow
    them. Inside if branches and each bodies, bindings are local and
    do not leak outward. None means ok. Assumes check_expr passed.
    """
    bound = set()
    for e in exprs:
        err = _scan_one(e, bound)
        if err:
            return err
        if isinstance(e, list) and e and e[0] == "30":
            bound.add(e[1])
    return None


def _scan_one(e, bound):
    if isinstance(e, str):
        if is_ref(e) and e[1:] not in bound:
            return "unbound reference %s" % e
        return None
    if not e:
        return None
    if not (isinstance(e[0], str) and e[0].isdigit()):
        # not an expression (e.g. a range pair): scan the atoms
        for x in e:
            err = _scan_one(x, bound)
            if err:
                return err
        return None
    op, args = int(e[0]), e[1:]
    if op == 30:
        return _scan_one(args[1], bound)
    if op == 31:
        for a in args:
            err = _scan_one(a, set(bound))
            if err:
                return err
        return None
    if op == 32:
        err = _scan_one(args[1], set(bound))
        if err:
            return err
        return _scan_one(args[2], set(bound) | {args[0]})
    for a in args:
        err = _scan_one(a, set(bound))
        if err:
            return err
    return None


def rcp_sum(bare_line):
    """Checksum for a v2-rcp line: first 16 hex chars of the sha256
    of the canonical 13-field line, encoded UTF-8."""
    return hashlib.sha256(bare_line.encode("utf-8")).hexdigest()[:16]


def add_rcp(bare_line):
    """Append the checksum field to a canonical bare v2 line."""
    return bare_line + "|" + rcp_sum(bare_line)


def split_fields(line):
    """Split a v2 line into fields. Bare v2 has 13 fields; v2-rcp has
    14 (the 14th is the SUM checksum). Returns (fields, error)."""
    fields = line.split("|")
    if len(fields) not in (13, 14):
        return None, "want 13 fields (bare) or 14 (rcp), have %d" % len(
            fields)
    if fields[0] != "2":
        return None, "want wire version 2"
    return fields, None


def check_line(line, lineno):
    errs = []

    def err(code, msg):
        errs.append("ERR:%s line %d: %s" % (code, lineno, msg))

    fields, ferr = split_fields(line)
    if ferr:
        err("SHAPE", ferr)
        return errs
    bare = fields[:13]
    _, pid, tk, f, t, a, st, cf, cv, body, rq, base, exp = bare
    for name, tok in (("ID", pid), ("TK", tk)):
        if not TOKEN.match(tok):
            err("TOKEN", "%s fails token pattern" % name)
    if len(tk) > 64:
        err("RANGE", "TK longer than 64 characters")
    if base and not BASE.match(base):
        err("TOKEN", "BASE is neither empty nor id:ver")
    nums = {}
    for name, raw in (("F", f), ("T", t), ("A", a), ("ST", st), ("CF", cf),
                      ("CV", cv), ("RQ", rq), ("EXP", exp)):
        if not re.fullmatch(r"\d+", raw):
            err("TYPE", "%s is not an unsigned integer" % name)
            continue
        if len(raw) > 1 and raw.startswith("0"):
            err("CANON", "%s has leading zeros" % name)
            continue
        nums[name] = int(raw)
    for name, reg, label in (("F", ROLES, "role"), ("T", ROLES, "role"),
                             ("A", ACTS, "act"), ("ST", STATES, "state"),
                             ("RQ", RQS, "request")):
        if name in nums and nums[name] not in reg:
            err("CODE", "%s=%d outside %s registry" % (name, nums[name], label))
    for name in ("CF", "CV"):
        if name in nums and not 0 <= nums[name] <= 100:
            err("RANGE", "%s=%d outside 0-100" % (name, nums[name]))
    if body.strip():
        exprs, perr = parse_all(body)
        if perr:
            err("BODY", perr)
        else:
            shaped = True
            for e in exprs:
                cerr = check_expr(e)
                if cerr:
                    err("BODY", cerr)
                    shaped = False
            if shaped:
                serr = check_scope(exprs)
                if serr:
                    err("SCOPE", serr)
    if re.search(r"\( | \)|  ", body):
        err("CANON", "BODY spacing is not canonical")
    if len(fields) == 14 and not errs:
        total = fields[13]
        if not SUM.match(total):
            err("RCP", "SUM is not 16 lowercase hex characters")
        elif total != rcp_sum("|".join(bare)):
            err("RCP", "SUM does not match the line")
    return errs


def validate_lines(lines):
    """Validate raw lines. Returns the list of diagnostics."""
    errs = []
    for i, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        if raw != raw.strip():
            errs.append("ERR:CANON line %d: leading or trailing whitespace"
                        % i)
            continue
        for e in check_line(raw.strip(), i):
            errs.append(e)
    return errs


def frag(e):
    """Render an expression or atom as an inline fragment."""
    if isinstance(e, str):
        return e[1:] if e.startswith("$") else e
    if not e:
        return ""
    op = int(e[0])
    args = e[1:]
    if op == 20:
        _unit = UNITS.get(args[0], str(args[0]))
        r = args[1]
        if isinstance(r, list):
            if r[0] == r[1]:
                return "implement section %s" % frag(r[0])
            return "implement sections %s-%s" % (frag(r[0]), frag(r[1]))
        return "implement section %s" % frag(r)
    if op == 21:
        if args[0] == "*":
            return "modify no existing paths"
        return "modify no paths matching %s" % frag(args[0])
    if op == 22:
        return "%s exits %s on valid input, %s on invalid" % (
            frag(args[0]), args[1], args[2])
    if op == 30:
        return "let %s = %s" % (args[0], frag(args[1]))
    if op == 31:
        s = "if %s then %s" % (frag(args[0]), frag(args[1]))
        if args[2]:
            s += ", otherwise %s" % frag(args[2])
        return s
    if op == 32:
        return "for each %s in %s: %s" % (
            args[0], frag(args[1]), frag(args[2]))
    if op == 33:
        return "[%s]" % ", ".join(frag(x) for x in args)
    if op == 34:
        return "%s < %s" % (frag(args[0]), frag(args[1]))
    if op == 35:
        return "%s = %s" % (frag(args[0]), frag(args[1]))
    if op == 40:
        role = ROLES.get(int(args[0]), str(args[0]))
        return "spawn %s on thread %s to %s" % (
            role, args[1], frag(args[2]))
    return None


def render_expr(expr):
    """Render a top-level body expression as one English sentence."""
    op = int(expr[0])
    args = expr[1:]
    if op == 10:
        return "Scope: %s of %s." % (frag(args[1]), args[0])
    if op == 11:
        return "Constraint: %s." % frag(args[0])
    if op == 12:
        return "Acceptance: %s." % frag(args[0])
    s = frag(expr)
    if s is None:
        return None
    return s[0].upper() + s[1:] + "."


def render_packet(line, roles=None):
    """Render one line as English. `roles` maps role codes to display names."""
    names = {**ROLES, **(roles or {})}
    fields, err = split_fields(line)
    if err:
        return None, "not a v2 packet line (%s)" % err
    rcp = len(fields) == 14
    _, pid, _tk, f, t, a, st, cf, cv, body, rq, base, exp = fields[:13]
    try:
        f, t, a, st, rq = int(f), int(t), int(a), int(st), int(rq)
        cf, cv, exp = int(cf), int(cv), int(exp)
    except ValueError:
        return None, "non-integer in numeric field"
    for code, reg, name in ((f, ROLES, "role"), (t, ROLES, "role"),
                            (a, ACTS, "act"), (st, STATES, "state"),
                            (rq, RQS, "request")):
        if code not in reg:
            return None, "unknown %s code %d" % (name, code)
    out = ["%s %s [%s -> %s] (%s) confidence %d, coverage %d"
           % (ACTS[a], pid, names[f], names[t], STATES[st], cf, cv)]
    if body.strip():
        exprs, perr = parse_all(body)
        if perr:
            return None, "body: " + perr
        for e in exprs:
            cerr = check_expr(e)
            if cerr:
                return None, "body: " + cerr
            r = render_expr(e)
            if r is None:
                return None, "body: cannot render expression"
            out.append(r)
    if rq:
        out.append("Requires: %s." % RQS[rq])
    if base:
        out.append("Base: %s." % base)
    if exp:
        out.append("Expires in %ds." % exp)
    if rcp:
        out.append("Relay corruption protection: on.")
    return "\n".join(out), None


V1_ROLES = {"cos": 1, "builder": 2, "verifier": 3, "scout": 4,
            "critic": 5, "operator": 6}
V1_ACTS = {"assign": 1, "ack": 2, "assert": 3, "update": 4, "receipt": 5,
           "challenge": 6, "dispute": 7, "authorize": 8, "query": 9,
           "inform": 10}
V1_STATES = {"proposed": 1, "accepted": 2, "rejected": 3, "verified": 4,
             "open": 5, "closed": 6, "superseded": 7, "expired": 8}
V1_RQS = {"ack": 1, "verify": 2}

SCOPE_RE = re.compile(r"^scope: implement (\S+) sections (\d+)-(\d+)", re.I)
NOMOD_RE = re.compile(r"^constraint: no existing paths? may be modified", re.I)
ACCEPT_RE = re.compile(r"^acceptance: (\S+) exits (\d+) on valid \S+, "
                       r"non-zero on invalid", re.I)


def migrate_cond(cond):
    m = SCOPE_RE.match(cond)
    if m:
        target = m.group(1).lower()
        return "(10 %s (20 sec (%s %s)))" % (target, m.group(2), m.group(3))
    if NOMOD_RE.match(cond):
        return "(11 (21 *))"
    m = ACCEPT_RE.match(cond)
    if m:
        return "(12 (22 %s %s 1))" % (m.group(1).lower(), m.group(2))
    return None


def lookup(table, value, field):
    if value not in table:
        return None, "unknown v1 %s %r" % (field, value)
    return table[value], None


def migrate(obj, lineno=1, rcp=False, roles=None):
    """v1 JSON packet to a v2 line. `roles` maps extra v1 role ids to v2 role codes."""
    role_table = {**V1_ROLES, **(roles or {})}
    def fail(msg):
        return None, "line %d: %s" % (lineno, msg)

    if not isinstance(obj, dict):
        return fail("not a JSON object")
    f, err = lookup(role_table, obj.get("f"), "role")
    if err:
        return fail(err)
    t, err = lookup(role_table, obj.get("t"), "role")
    if err:
        return fail(err)
    a, err = lookup(V1_ACTS, obj.get("a"), "act")
    if err:
        return fail(err)
    st, err = lookup(V1_STATES, obj.get("st"), "state")
    if err:
        return fail(err)
    rq = V1_RQS.get(obj.get("rq"), 0)
    body = []
    for cond in obj.get("cond", []):
        expr = migrate_cond(cond)
        if expr is None:
            return fail("cond has no v2 pattern: %r" % cond)
        body.append(expr)
    for name in ("cf", "cov", "exp"):
        v = obj.get(name, 0)
        if not isinstance(v, int) or v < 0:
            return fail("%s is not a non-negative integer" % name)
    fields = ["2", str(obj.get("id", "")), str(obj.get("tk", "")),
              str(f), str(t), str(a), str(st),
              str(obj.get("cf", 0)), str(obj.get("cov", 0)),
              "".join(body), str(rq), str(obj.get("base", "")),
              str(obj.get("exp", 0))]
    line = "|".join(fields)
    if rcp:
        line = add_rcp(line)
    return line, None


