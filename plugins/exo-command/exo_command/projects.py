"""Projects board: sessions clustered into probable projects, with evidence tiers.

Ported from the Continuum plugin (scanner, evidence, cluster, classify), cut down to the
read-only core. There is no registry and no review write path here: the board is derived on
every scan and never stored. The rules are kept as Continuum defined them:

Signals (applied in order):
  CS-1  shared ``git_repo_root``               -> band high
  CS-2  shared workspace root (derived from cwd) -> band medium
  CS-3  shared strong token (repo, path, url, issue id). Annotates only: it never creates or
        bridges a cluster, it corroborates one that CS-1, CS-2 or CS-4 already formed.
  CS-4  parent and child session fold          -> medium with CS-2/3 support, else low

Noise classes (never seed a cluster): NS-1 cron, NS-2 subagent child (folded into its parent),
NS-3 recurring title, NS-4 test or handshake, NS-5 group relay, NS-6 untitled fragment,
NS-7 model eval.

Evidence tiers: tier 0 = session metadata only; tier 1 = a rule matched in a bounded message
probe (first message plus the last six). Tier 2 (model summary) is not built; Continuum shipped
it disabled too.

Lifecycles LS-1..LS-9 map to board lanes the way Continuum's ``_DERIVED_TO_PLACEMENT`` did.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from exo.ingest.hermes_dashboard import ProfileDb, columns, ro, select_cols

DAY = 86400.0

# ----------------------------------------------------------------------------- rule tables

STOPWORDS = set("""build site website audit fix fixes update test tests testing project session
error errors issue issues feature work task tasks plan check review the and for with that this
from into files file code new add make help need please continue resume handoff status summary
report dashboard plugin agent hermes""".split())

WORKSPACE_PATTERNS = [re.compile(r"^(?P<repo>.*?/(?:wrk|working|work|projects|src|code|repos)/[^/]+)", re.I)]

TEST_TITLE = [re.compile(p) for p in (
    r"(?i)^\s*(ping|pong|test|testing|smoke ?test|hello|hi)\b",
    r"(?i)\b(confirm receipt|handshake|delegate_task probe|probe)\b",
    r"(?i)\b(health ?check|heartbeat)\b")]
EVAL_TITLE = [re.compile(r"(?i)\b(model eval|benchmark|eval(uation)? run|provider test|model test)\b")]
RECURRING_TITLE = re.compile(r"·\s*\w{3}\s+\d{1,2}\s+\d{1,2}:\d{2}\s*$")

NEXT_ACTION = [re.compile(p) for p in (
    r"(?i)\bnext (?:step|action|up)s?\b[: ]", r"(?i)\b(?:to[- ]?do|todo)\b[: ]",
    r"(?i)\bcontinue (?:by|with)\b", r"(?i)\bremain(?:s|ing)? to\b")]
BLOCKER = [re.compile(p) for p in (
    r"(?i)\bblocked\b", r"(?i)\bcannot proceed\b", r"(?i)\bwaiting (?:on|for)\b",
    r"(?i)\bneed(?:s)? (?:access|approval|input)\b", r"(?i)\bpermission denied\b")]
AWAITING_USER = [re.compile(p) for p in (
    r"(?i)\bawaiting (?:your|user|approval|confirmation)\b",
    r"(?i)\bplease (?:confirm|approve|let me know)\b", r"(?i)\blet me know\b",
    r"(?i)\bwaiting on (?:you|the user)\b", r"\?\s*$")]
GOAL = [re.compile(p) for p in (r"(?i)\b(?:goal|objective)\b[: ]", r"(?i)\bbuilding\b",
                                r"(?i)\bimplement(?:ing)?\b")]
DECISION = [re.compile(p) for p in (r"(?i)\bdecision\b[: ]", r"(?i)\bdecided to\b",
                                    r"(?i)\bwe(?:'| a)?ll use\b")]
HEALTH_ACK = [re.compile(r"(?i)^\s*(ok|okay|ack|acknowledged|received|got it|thanks|thank you|done)\s*[.!]?\s*$")]

_URL_RE = re.compile(r"https?://[^\s<>'\"\)\]]+")
_PR_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d{3,6})\b")
_HASH_ISSUE_RE = re.compile(r"(?<![\w/])#(\d{4,6})\b")
_PATH_RE = re.compile(r"(?:/[\w.@-]+){2,}")
_REPO_SEGMENT_RE = re.compile(r"/(?:wrk|working|work|projects|repos)/([\w.-]+)", re.I)

LIFECYCLE_NAMES = {
    "LS-1": "proposed", "LS-2": "active", "LS-3": "blocked", "LS-4": "awaiting-principal",
    "LS-5": "stalled", "LS-6": "parked", "LS-7": "complete", "LS-8": "abandoned-candidate",
    "LS-9": "unknown",
}
LANE_BY_LIFECYCLE = {
    "LS-1": "inbox", "LS-2": "ongoing", "LS-3": "blocked", "LS-4": "waiting_on_you",
    "LS-5": "paused", "LS-6": "paused", "LS-7": "done", "LS-8": "scrapped", "LS-9": "inbox",
}
LANES = ("waiting_on_you", "blocked", "ongoing", "inbox", "paused", "done", "scrapped")
LANE_LABELS = {
    "waiting_on_you": "Waiting on you", "blocked": "Blocked", "ongoing": "Ongoing",
    "inbox": "Inbox", "paused": "Stalled", "done": "Done", "scrapped": "Abandoned?",
}
BAND_SCORES = {"high": 0.85, "medium": 0.60, "low": 0.35, "unknown": 0.15}

SESSION_COLS = ("id", "source", "title", "display_name", "cwd", "git_repo_root", "started_at",
                "last_activity_at", "message_count", "tool_call_count", "parent_session_id",
                "model", "chat_type", "title_source", "archived", "hidden")


# ----------------------------------------------------------------------------- data shapes

@dataclass
class Fact:
    profile: str
    session_id: str
    source: str
    title: str
    cwd: Optional[str]
    workspace_root: Optional[str]
    git_repo_root: Optional[str]
    started_at: Optional[float]
    last_activity_at: Optional[float]
    message_count: int
    tool_call_count: int
    parent_session_id: Optional[str]
    model: Optional[str]
    chat_type: Optional[str]
    display_name: Optional[str]
    title_source: Optional[str]

    @property
    def ref(self) -> str:
        return "{}/{}".format(self.profile, self.session_id)


@dataclass
class Probe:
    ref: str
    role: str
    content: str
    timestamp: Optional[float]
    position: str  # head | tail


@dataclass
class Evidence:
    tier: int
    kind: str
    excerpt: str
    ref: str


@dataclass
class Cluster:
    cluster_id: str
    members: List[str]
    keys: List[str]
    breakdown: Dict[str, int]
    reasons: List[str]
    band: str
    name: Optional[str]


# ----------------------------------------------------------------------------- helpers

class Rules:
    def __init__(self, generic_paths: Sequence[str] = ()):
        home = os.path.expanduser("~").rstrip("/")
        base = {"", ".", "./", "/", "/tmp", "/private/tmp", "/Users", "/home", "~", "/root",
                home, os.path.dirname(home)}
        self.generic = {p.rstrip("/") or "/" for p in base} | {
            (os.path.expanduser(p).rstrip("/") or "/") for p in generic_paths}

    def is_generic(self, path: Optional[str]) -> bool:
        if path is None:
            return True
        norm = os.path.expanduser(path.strip()).rstrip("/") or "/"
        return norm in self.generic


_WALKUP_CACHE: Dict[str, Optional[str]] = {}


def workspace_root(cwd: Optional[str], rules: Rules, walkup: bool = True) -> Optional[str]:
    """cwd -> workspace root. Generic roots never produce a key (Continuum AV-4)."""
    if not cwd:
        return None
    norm = os.path.expanduser(cwd).rstrip("/") or "/"
    if rules.is_generic(norm):
        return None
    for pat in WORKSPACE_PATTERNS:
        m = pat.search(norm)
        if m and m.group("repo") and not rules.is_generic(m.group("repo")):
            return m.group("repo").rstrip("/")
    if not walkup:
        return None
    if norm in _WALKUP_CACHE:
        return _WALKUP_CACHE[norm]
    found = None
    cur = norm
    for _ in range(8):
        try:
            if os.path.isdir(os.path.join(cur, ".git")):
                found = None if rules.is_generic(cur) else cur
                break
        except OSError:
            break
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    _WALKUP_CACHE[norm] = found
    return found


def _f(v) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _clip(text: str, n: int = 240) -> str:
    return " ".join((text or "").split())[:n]


def _any(pats: Iterable[re.Pattern], text: str) -> bool:
    return any(p.search(text) for p in pats)


# ----------------------------------------------------------------------------- scan (M1)

def scan(profiles: Sequence[ProfileDb], rules: Rules, *, window_days: int = 90,
         now: Optional[float] = None, walkup: bool = True,
         probe_head: int = 1, probe_tail: int = 6) -> Tuple[List[Fact], List[Probe], List[dict]]:
    now = now if now is not None else time.time()
    since = now - window_days * DAY if window_days > 0 else 0
    facts: List[Fact] = []
    probes: List[Probe] = []
    status: List[dict] = []
    for p in profiles:
        st = {"profile": p.name, "status": "ok", "sessions": 0, "snapshot": False}
        try:
            with ro(p.path) as c:
                st["snapshot"] = c.snapshot
                have = columns(c, "sessions")
                if not have:
                    st["status"] = "no sessions table"
                    status.append(st)
                    continue
                cols = select_cols(SESSION_COLS, have)
                act = "COALESCE(last_activity_at, started_at)" if "last_activity_at" in have else "started_at"
                where = "WHERE {} >= ?".format(act)
                rows = list(c.execute('SELECT {} FROM sessions {}'.format(
                    ", ".join('"%s"' % k for k in cols), where), (since,)))
                mcols = columns(c, "messages")
                can_probe = "content" in mcols and "role" in mcols
                st["sessions"] = len(rows)
                for r in rows:
                    rec = dict(r)
                    cwd = rec.get("cwd")
                    fact = Fact(
                        profile=p.name, session_id=str(rec.get("id")),
                        source=(rec.get("source") or "unknown"), title=(rec.get("title") or ""),
                        cwd=cwd, workspace_root=workspace_root(cwd, rules, walkup),
                        git_repo_root=rec.get("git_repo_root") or None,
                        started_at=_f(rec.get("started_at")),
                        last_activity_at=_f(rec.get("last_activity_at")) or _f(rec.get("started_at")),
                        message_count=int(rec.get("message_count") or 0),
                        tool_call_count=int(rec.get("tool_call_count") or 0),
                        parent_session_id=rec.get("parent_session_id") or None,
                        model=rec.get("model"), chat_type=rec.get("chat_type"),
                        display_name=rec.get("display_name"), title_source=rec.get("title_source"),
                    )
                    if int(rec.get("hidden") or 0):
                        continue
                    facts.append(fact)
                    # Noise sessions never seed a cluster, so their messages are not read.
                    if can_probe and not classify_noise([fact])[fact.ref]:
                        probes.extend(_probe(c, fact, probe_head, probe_tail))
        except (OSError, sqlite3.Error) as exc:
            st["status"] = "error: {}".format(exc)
        status.append(st)
    return facts, probes, status


def _probe(c, fact: Fact, head: int, tail: int) -> List[Probe]:
    """Bounded window: first ``head`` and last ``tail`` messages. Never the whole transcript."""
    sql = ("SELECT id, role, substr(content, 1, 600) AS content, timestamp FROM messages "
           "WHERE session_id=? ORDER BY id {} LIMIT ?")
    out: List[Probe] = []
    seen = set()
    try:
        head_rows = list(c.execute(sql.format("ASC"), (fact.session_id, head))) if head else []
        tail_rows = list(reversed(list(c.execute(sql.format("DESC"), (fact.session_id, tail)))))
    except sqlite3.Error:
        return out
    for pos, rows in (("head", head_rows), ("tail", tail_rows)):
        for r in rows:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            out.append(Probe(fact.ref, r["role"] or "", str(r["content"] or ""), _f(r["timestamp"]), pos))
    return out


# ----------------------------------------------------------------------------- evidence (M3)

def extract(probes: Sequence[Probe]) -> List[Evidence]:
    out: List[Evidence] = []
    for p in probes:
        text = p.content or ""
        if not text.strip() or _any(HEALTH_ACK, text):
            continue
        for kind, pats in (("blocker", BLOCKER), ("next_action", NEXT_ACTION),
                           ("decision", DECISION), ("goal", GOAL)):
            if _any(pats, text):
                out.append(Evidence(1, kind, _clip(text), p.ref))
                break
        for url in _URL_RE.findall(text)[:1]:
            out.append(Evidence(1, "link", _clip(url), p.ref))
    return out


def _tokens(text: str, rules: Rules) -> Counter:
    toks: Counter = Counter()
    if not text:
        return toks
    for url in _URL_RE.findall(text):
        toks["url:" + url.rstrip("/.,;")] += 1
    for pr in _PR_RE.findall(text):
        toks["issue:" + pr] += 1
    for n in _HASH_ISSUE_RE.findall(text):
        toks["issue:#" + n] += 1
    for seg in _REPO_SEGMENT_RE.findall(text):
        toks["repo:" + seg.lower()] += 1
    for path in _PATH_RE.findall(text):
        if len(path) >= 8 and not rules.is_generic(path) and _REPO_SEGMENT_RE.search(path):
            toks["path:" + path.rstrip("/.")] += 1
    return toks


def canonical_keys(facts: Sequence[Fact], probes: Sequence[Probe], rules: Rules,
                   min_sessions: int = 2, max_freq: float = 0.02, max_sessions: int = 40) -> Dict[str, List[str]]:
    """token -> refs, for strong tokens shared by enough sessions and rare enough to mean something."""
    by_ref: Dict[str, Counter] = defaultdict(Counter)
    for f in facts:
        by_ref[f.ref].update(_tokens(f.title, rules))
        if f.cwd and not rules.is_generic(f.cwd):
            for seg in _REPO_SEGMENT_RE.findall(f.cwd):
                by_ref[f.ref]["repo:" + seg.lower()] += 1
    for p in probes:
        by_ref[p.ref].update(_tokens(p.content, rules))
    holders: Dict[str, set] = defaultdict(set)
    for ref, counter in by_ref.items():
        for tok in counter:
            holders[tok].add(ref)
    total = max(1, len(facts))
    # small corpora would fail any frequency gate; the absolute ceiling still applies
    freq_ok = (lambda n: n / total <= max_freq) if total >= 200 else (lambda n: True)
    return {tok: sorted(refs) for tok, refs in holders.items()
            if min_sessions <= len(refs) <= max_sessions and freq_ok(len(refs))}


# ----------------------------------------------------------------------------- noise + clusters (M4)

def classify_noise(facts: Sequence[Fact]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for f in facts:
        title = f.title.strip()
        src = f.source.lower()
        cls = ""
        if src == "cron":
            cls = "NS-1"
        elif f.parent_session_id and src == "subagent":
            cls = "NS-2"
        elif not title and not (f.title_source or "").strip():
            cls = "NS-6"
        elif title and RECURRING_TITLE.search(title):
            cls = "NS-3"
        elif (f.chat_type or "").lower() == "group" or (f.display_name or "").startswith("Group:") \
                or title.startswith("Group:"):
            cls = "NS-5"
        elif title and _any(TEST_TITLE, title):
            cls = "NS-4"
        elif title and _any(EVAL_TITLE, title):
            cls = "NS-7"
        out[f.ref] = cls
    return out


class _UF:
    def __init__(self):
        self.parent: Dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def build_clusters(facts: Sequence[Fact], keys: Dict[str, List[str]], rules: Rules,
                   noise: Dict[str, str]) -> List[Cluster]:
    by_ref = {f.ref: f for f in facts}
    seeds = [r for r in by_ref if not noise.get(r)]
    seed_set = set(seeds)
    uf = _UF()
    reasons: Dict[str, set] = defaultdict(set)
    owner: Dict[str, str] = {}

    for attr, sig in (("git_repo_root", "CS-1"), ("workspace_root", "CS-2")):
        buckets: Dict[str, List[str]] = defaultdict(list)
        for r in seeds:
            v = getattr(by_ref[r], attr)
            if v and not rules.is_generic(v):
                buckets[v].append(r)
        for v, refs in buckets.items():
            if len(refs) < 2:
                continue
            for r in refs[1:]:
                uf.union(refs[0], r)
            owner["{}:{}".format(sig, v)] = refs[0]
            for r in refs:
                reasons[r].add("{} shared {}".format(sig, v))

    # CS-3 annotates components that already exist; it never unions.
    for tok, refs in keys.items():
        present = [r for r in refs if r in seed_set]
        if len(present) < 2:
            continue
        groups: Dict[str, List[str]] = defaultdict(list)
        for r in present:
            groups[uf.find(r)].append(r)
        for g in groups.values():
            if len(g) >= 2:
                owner["CS-3:" + tok] = g[0]
                for r in g:
                    reasons[r].add("CS-3 shared token " + tok)

    for r, f in by_ref.items():
        if not f.parent_session_id:
            continue
        pref = "{}/{}".format(f.profile, f.parent_session_id)
        if pref not in by_ref or pref not in seed_set:
            continue
        if r in seed_set or noise.get(r) == "NS-2":
            uf.union(pref, r)
            owner["CS-4:" + r] = pref
            reasons[r].add("CS-4 child of " + f.parent_session_id)

    groups: Dict[str, List[str]] = defaultdict(list)
    for r in list(uf.parent):
        groups[uf.find(r)].append(r)
    out: List[Cluster] = []
    for root, members in groups.items():
        if len(members) < 2:
            continue
        mkeys = sorted(k for k, o in owner.items() if uf.find(o) == root)
        breakdown: Dict[str, int] = Counter(k.split(":", 1)[0] for k in mkeys)
        rs = sorted({x for m in members for x in reasons.get(m, ())})
        out.append(Cluster(
            cluster_id=str(uuid.uuid5(uuid.NAMESPACE_URL, "exo-command:" + ("|".join(mkeys) or root))),
            members=sorted(members), keys=mkeys, breakdown=dict(breakdown), reasons=rs,
            band=_band(breakdown, rs), name=_name(members, by_ref)))
    return out


def _band(cs: Dict[str, int], reasons: Sequence[str]) -> str:
    if cs.get("CS-1"):
        return "high"
    if cs.get("CS-2") or cs.get("CS-3"):
        return "medium"
    if cs.get("CS-4"):
        return "medium" if any("CS-2" in r or "CS-3" in r for r in reasons) else "low"
    return "unknown"


def _name(members: Sequence[str], by_ref: Dict[str, Fact]) -> Optional[str]:
    for attr in ("git_repo_root", "workspace_root"):
        for m in members:
            v = getattr(by_ref[m], attr)
            if v:
                return os.path.basename(v.rstrip("/")) or v
    titled = sorted((by_ref[m] for m in members if by_ref[m].title),
                    key=lambda f: -(f.last_activity_at or 0))
    return titled[0].title[:80] if titled else None


# ----------------------------------------------------------------------------- classify (M5)

def classify(c: Cluster, by_ref: Dict[str, Fact], probes_by_ref: Dict[str, List[Probe]],
             evidence_by_ref: Dict[str, List[Evidence]], *, now: float,
             stall_days: float, abandon_days: float) -> dict:
    last = None
    msgs = tools = 0
    for m in c.members:
        f = by_ref[m]
        msgs += f.message_count
        tools += f.tool_call_count
        ts = _substantive(f, probes_by_ref.get(m, []))
        if ts is not None and (last is None or ts > last):
            last = ts
    age = None if last is None else max(0.0, (now - last) / DAY)

    # Only the newest session speaks for the project's current state.
    # Delegated children report to their parent, so prefer top-level sessions.
    top = [m for m in c.members if not by_ref[m].parent_session_id] or list(c.members)
    newest = max(top, key=lambda m: by_ref[m].last_activity_at or 0)
    blockers: List[str] = []
    awaiting = False
    next_action = None
    for p in probes_by_ref.get(newest, []):
        if p.position != "tail" or p.role not in ("user", "assistant"):
            continue
        text = p.content or ""
        if _any(BLOCKER, text):
            blockers.append(_clip(text, 140))
        if _any(NEXT_ACTION, text) and next_action is None:
            next_action = _clip(text, 180)
    tail = [p for p in probes_by_ref.get(newest, []) if p.role in ("user", "assistant") and p.content.strip()]
    if tail and tail[-1].role == "assistant" and _any(AWAITING_USER, tail[-1].content):
        awaiting = True

    if age is not None and age > abandon_days:
        lc = "LS-8"
    elif blockers and (age is None or age <= stall_days):
        lc = "LS-3"
    elif awaiting and (age is None or age <= stall_days):
        lc = "LS-4"
    elif c.band == "unknown":
        lc = "LS-9"
    elif age is not None and age > stall_days:
        lc = "LS-5"
    elif msgs <= 4 and tools == 0:
        lc = "LS-1"
    else:
        lc = "LS-2"

    ev = [e for m in c.members for e in evidence_by_ref.get(m, [])]
    tier = 1 if ev else 0
    conf = round(min(0.95, BAND_SCORES.get(c.band, 0.15) + 0.02 * max(0, len(c.keys) - 1)), 3)
    profiles = Counter(by_ref[m].profile for m in c.members)
    sessions = sorted(c.members, key=lambda m: -(by_ref[m].last_activity_at or 0))
    return {
        "id": c.cluster_id,
        "name": c.name or "(untitled cluster)",
        "lane": LANE_BY_LIFECYCLE[lc],
        "lifecycle": lc,
        "lifecycle_name": LIFECYCLE_NAMES[lc],
        "band": c.band,
        "confidence": conf,
        "evidence_tier": tier,
        "needs_review": c.band in ("low", "unknown") or tier == 0,
        "signals": sorted(c.breakdown),
        "keys": c.keys[:12],
        "reasons": c.reasons[:12],
        "owner_profile": profiles.most_common(1)[0][0],
        "profiles": sorted(profiles),
        "session_count": len(c.members),
        "message_count": msgs,
        "last_activity": last,
        "stall_age_days": None if age is None else round(age, 2),
        "next_action": next_action,
        "blockers": blockers[:3],
        "evidence": [{"tier": e.tier, "kind": e.kind, "excerpt": e.excerpt, "ref": e.ref}
                     for e in sorted(ev, key=lambda e: e.kind)[:8]],
        "sessions": [{"ref": m, "title": by_ref[m].title or by_ref[m].session_id,
                      "source": by_ref[m].source, "last_activity": by_ref[m].last_activity_at}
                     for m in sessions[:12]],
    }


def _substantive(f: Fact, probes: Sequence[Probe]) -> Optional[float]:
    best = None
    for p in probes:
        if p.role not in ("user", "assistant") or not p.content.strip() or _any(HEALTH_ACK, p.content):
            continue
        if p.timestamp is not None and (best is None or p.timestamp > best):
            best = p.timestamp
    return best if best is not None else f.last_activity_at


# ----------------------------------------------------------------------------- façade

def board(profiles: Sequence[ProfileDb], *, generic_paths: Sequence[str] = (), window_days: int = 90,
          stall_days: float = 7, abandon_days: float = 60, now: Optional[float] = None,
          walkup: bool = True) -> dict:
    """Board from Hermes databases (importer path). The live command centre uses ``board_from_facts``."""
    now = now if now is not None else time.time()
    rules = Rules(generic_paths)
    facts, probes, status = scan(profiles, rules, window_days=window_days, now=now, walkup=walkup)
    return board_from_facts(facts, probes, status, rules, window_days=window_days, stall_days=stall_days,
                            abandon_days=abandon_days, now=now)


def facts_from_sessions(sessions: Iterable[dict], rules: Rules, *, walkup: bool = True) -> List[Fact]:
    """Facts from Exo protocol session records (see ``state.State.session_records``).

    A record has id, title, seat, model, cwd, parent, started_at, last_activity_at, message_count,
    tool_call_count and ``subagent`` (True for a child session of a delegated task).
    """
    out: List[Fact] = []
    for r in sessions:
        cwd = r.get("cwd")
        out.append(Fact(
            profile=r.get("seat") or "main", session_id=str(r["id"]),
            source="subagent" if r.get("subagent") else "exo", title=r.get("title") or "",
            cwd=cwd, workspace_root=workspace_root(cwd, rules, walkup), git_repo_root=None,
            started_at=_f(r.get("started_at")),
            last_activity_at=_f(r.get("last_activity_at")) or _f(r.get("started_at")),
            message_count=int(r.get("message_count") or 0), tool_call_count=int(r.get("tool_call_count") or 0),
            parent_session_id=r.get("parent") or None, model=r.get("model"), chat_type=None,
            display_name=None, title_source="exo" if r.get("title") else None))
    return out


def probes_from_sessions(sessions: Iterable[dict], rules: Rules) -> List[Probe]:
    """Bounded probes: each record carries ``probe`` rows of (role, text, ts, position)."""
    out: List[Probe] = []
    for r in sessions:
        ref = "{}/{}".format(r.get("seat") or "main", r["id"])
        for role, text, ts, pos in r.get("probe") or []:
            out.append(Probe(ref, role, str(text or "")[:600], _f(ts), pos))
    return out


def board_from_facts(facts: List[Fact], probes: List[Probe], status: List[dict], rules: Rules, *,
                     window_days: int = 90, stall_days: float = 7, abandon_days: float = 60,
                     now: Optional[float] = None) -> dict:
    now = now if now is not None else time.time()
    noise = classify_noise(facts)
    keys = canonical_keys(facts, probes, rules)
    clusters = build_clusters(facts, keys, rules, noise)
    by_ref = {f.ref: f for f in facts}
    probes_by_ref: Dict[str, List[Probe]] = defaultdict(list)
    for p in probes:
        probes_by_ref[p.ref].append(p)
    evidence_by_ref: Dict[str, List[Evidence]] = defaultdict(list)
    for e in extract(probes):
        evidence_by_ref[e.ref].append(e)

    cards = [classify(c, by_ref, probes_by_ref, evidence_by_ref, now=now,
                      stall_days=stall_days, abandon_days=abandon_days) for c in clusters]
    order = {lane: i for i, lane in enumerate(LANES)}
    band_rank = {"high": 0, "medium": 1, "low": 2, "unknown": 3}
    cards.sort(key=lambda c: (order[c["lane"]], band_rank[c["band"]], -(c["last_activity"] or 0)))
    lanes = [{"id": lane, "label": LANE_LABELS[lane],
              "cards": [c for c in cards if c["lane"] == lane]} for lane in LANES]
    clustered = {m for c in clusters for m in c.members}
    noise_counts = Counter(v for v in noise.values() if v)
    return {
        "lanes": lanes,
        "counts": {"projects": len(cards), "sessions": len(facts), "clustered": len(clustered),
                   "unclustered": len([f for f in facts if f.ref not in clustered and not noise.get(f.ref)]),
                   "noise": dict(sorted(noise_counts.items()))},
        "sources": status,
        "window_days": window_days,
    }
