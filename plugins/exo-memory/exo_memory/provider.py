"""exo-memory: the Exo memory provider for the Exo core.

One provider slot holds three layers (ARCHITECTURE.md section 3):

- the principal pack (PRINCIPAL.md) in the static system prompt, trimmed to a token budget;
- belief retrieval (exo.retrieve) plus episodic recall (the vendored optchat log and summary
  tree) per turn, under one budget;
- the signed ledger (exo.ledger): every turn, memory write, delegation, captured ruling and
  correction becomes an event.

All state is per Exo home (seat) and lives in the principal store.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional

from exo.core.slots import MemoryProvider, RecallStatus

try:
    from exo import crypto
    from exo.capture import (CORRECTION, DECISION, RULING, clip, extract_signals, is_trivial, normalize,
                             render_block, to_event)
    from exo.ledger import Ledger
    from exo.provenance import principal_view
    from exo.retrieve import assemble
    from exo.store import PrincipalStore
    _EXO_ERROR = ""
except ImportError as exc:  # the plugin still loads and reports why it is unavailable
    _EXO_ERROR = f"the exo package is not importable ({exc})"

from . import config as C

logger = logging.getLogger(__name__)

PROVIDER_NAME = "exo-memory"
NOTE_KINDS = ("note", "question", "action", "artifact")
# D-040: kinds in the principal's voice. The agent's exo_note cannot write them: a note claiming one is kept as an
# agent note (kind note, type agent_note), which consolidation and learning never read as the principal.
PRINCIPAL_KINDS = ("ruling", "answer")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TOOLS_LINE = ("Memory tools: exo_recall(query, budget) for beliefs and past sessions; exo_get(id) reads a whole "
              "belief node when recall shows only its summary; exo_note(text, kind) to log; "
              "exo_should_have_been(target, wanted, reason) when the principal corrects you; "
              "exo_zoom(id, n) and exo_date(id) open episodic lines.")
CORE_TOOLS_LINE = ("Memory tools: exo_recall(query, budget) for beliefs and older details of your log; exo_get(id) "
                   "reads a whole belief node when recall shows only its summary; exo_note(text, kind) to log; exo_should_have_been(target, wanted, reason) when the principal corrects you; "
                   "memory_zoom(start, n) opens your log.")

RECALL_SCHEMA = {
    "name": "exo_recall",
    "description": ("Recall what is known about the principal and past sessions: belief nodes from the "
                    "principal store plus matching episodic memory, under a token budget."),
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "What to recall."},
        "budget": {"type": "integer", "description": "Token budget (default from config)."}},
        "required": ["query"]},
}
GET_SCHEMA = {
    "name": "exo_get",
    "description": ("Read one belief node in full by its id. Use it when exo_recall lists a node as "
                    "'summary only; exo get <id>'. Same store and access as exo_recall."),
    "parameters": {"type": "object", "properties": {
        "id": {"type": "string", "description": "The node id, as exo_recall printed it (a path like domain/name)."},
        "body_only": {"type": "boolean", "description": "Only the body, without the front matter (default false)."}},
        "required": ["id"]},
}
GET_LIMIT = 40000  # characters; a node is a note, not a corpus
NOTE_SCHEMA = {
    "name": "exo_note",
    "description": ("Append your own note to the principal's signed ledger: facts, open questions and notable "
                    "actions. It is signed as you, not as the principal. The principal's rulings and answers are "
                    "captured from their own messages; you cannot write one for them."),
    "parameters": {"type": "object", "properties": {
        "text": {"type": "string", "description": "The note, in plain words."},
        "kind": {"type": "string", "enum": list(NOTE_KINDS), "description": "Event kind (default note)."}},
        "required": ["text"]},
}
SHB_SCHEMA = {
    "name": "exo_should_have_been",
    "description": ("Record a correction: what an output should have been. Call it when the principal says "
                    "you got something wrong, with what they wanted and why."),
    "parameters": {"type": "object", "properties": {
        "target": {"type": "string", "description": ("Ledger event id of the judged output, 'last' for your "
                                                     "last reply, or a short description of it.")},
        "wanted": {"type": "string", "description": "What it should have been."},
        "reason": {"type": "string", "description": "Why, in the principal's terms."}},
        "required": ["target", "wanted"]},
}


def _h(text: str) -> str:
    return hashlib.sha256(normalize(text or "").encode("utf-8")).hexdigest()[:24]


def _home_default() -> Optional[str]:
    return os.environ.get("EXO_HOME") or None


def _home_key(home: Optional[str] = None) -> Optional[str]:
    h = home or os.environ.get("EXO_HOME")
    return os.path.realpath(os.path.expanduser(h)) if h else None


def _compaction_skip():
    from exo.core.context import is_compaction_summary_message
    return is_compaction_summary_message


def trim_pack(text: str, budget_tokens: int) -> str:
    """PRINCIPAL.md without curator comments, cut at paragraph boundaries to `budget_tokens`
    (estimated as chars / 4, as in exo.store)."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    limit = budget_tokens * 4
    if len(text) <= limit:
        return text
    note = "\n\n(pack trimmed to budget; the rest is in PRINCIPAL.md)"
    out = ""
    for para in text.split("\n\n"):
        cand = f"{out}\n\n{para}" if out else para
        if len(cand) + len(note) > limit:
            break
        out = cand
    if not out:  # one giant paragraph: cut at a line
        out = text[: max(0, limit - len(note))].rsplit("\n", 1)[0]
    return out + note


def cap_lines(text: str, limit: int) -> str:
    """Cut `text` at a line boundary so it is at most `limit` chars."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    return cut.rsplit("\n", 1)[0] if "\n" in cut else cut


class _Runtime:
    """Store, ledger and episodic layer for one principal store, shared by every profile of
    this process that points at it (the episodic log allows one writer per process)."""

    def __init__(self, root: Path, cfg: dict, agent: str, summarizer=None, episodic: bool = True):
        self.root = root
        self.store = PrincipalStore(root)
        kp = C.key_path(cfg, agent)
        if not kp.exists():
            if not cfg["auto_keygen"]:
                raise RuntimeError(f"no signing key at {kp}; run: exo keygen {agent}")
            crypto.save_key(crypto.keygen(), kp)
        self.signer = crypto.Signer(agent, crypto.load_key(kp))
        self.ledger = Ledger(self.store.ledger_path, self.signer)
        self.lock = threading.RLock()
        self.episodic = None
        self.episodic_error = ""
        try:
            if not episodic:  # the host keeps the agent's one log (Exo core, D-028): no second log
                raise LookupError
            from .episodic import EpisodicLayer
            self.episodic = EpisodicLayer(root / "episodic", view_chars=cfg["view_chars"],
                                          compact=cfg["compact_enabled"], summarizer=summarizer,
                                          agent_name=agent)
        except LookupError:
            self.episodic_error = "the host keeps the agent log"
        except Exception as exc:  # e.g. LogLocked: another process holds the log
            self.episodic_error = str(exc)
            logger.warning("exo-memory: episodic layer disabled: %s", exc)
        self.capture_keys: set[str] = set()
        for ev in self.ledger.iter(kind=("ruling", "verdict", "question")):
            key = ev["body"].get("capture_key")
            if key:
                self.capture_keys.add(key)
        self.refs = 0

    def close(self) -> None:
        if self.episodic is not None:
            self.episodic.close()
            self.episodic = None
        self.ledger.close()


_RUNTIMES: dict[str, _Runtime] = {}
_RUNTIMES_LOCK = threading.Lock()


class _State:
    """Per-profile state."""

    def __init__(self, cfg: dict, rt: _Runtime, agent: str):
        self.cfg = cfg
        self.rt = rt
        self.agent = agent
        self.principal = C.principal_id(cfg)
        self.principal_ids = C.principal_user_ids(cfg)
        self.session = "default"
        # session id -> {"primary": bool, "last_idx": int | None, "last_user": (hash, id) | None,
        #                "last_agent": id | None}
        self.sessions: dict[str, dict] = {}
        self.utterances: OrderedDict[str, str] = OrderedDict()  # text hash -> event id
        self.pack = ""
        self.last_recall = 0
        self.core_log: Any = None  # the host's agent log (D-028); when set, it is the episodic log

    @property
    def epi(self) -> Any:
        return self.core_log if self.core_log is not None else self.rt.episodic

    def remember(self, text: str, eid: str) -> None:
        self.utterances[_h(text)] = eid
        self.utterances.move_to_end(_h(text))
        while len(self.utterances) > 5000:
            self.utterances.popitem(last=False)


class ExoMemoryProvider(MemoryProvider):
    """Exo-core MemoryProvider backed by an Exo principal store."""

    pre_compress_checkpoint_api_version = 2  # captures land in the ledger before compression

    def __init__(self, config: Optional[dict] = None, *, summarizer=None) -> None:
        self._overrides = dict(config or {})
        self._summarizer = summarizer
        self._states: dict[str, _State] = {}
        self._lock = threading.RLock()

    # -- helpers -----------------------------------------------------------------

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    def _cfg(self, home: Optional[str] = None) -> dict:
        return C.load(home or _home_default(), self._overrides)

    def _state(self, home: Optional[str] = None) -> Optional[_State]:
        key = _home_key(home)
        with self._lock:
            if key and key in self._states:
                return self._states[key]
            if len(self._states) == 1:
                return next(iter(self._states.values()))
        return None

    def _sess(self, st: _State, sid: str = "") -> dict:
        sid = sid or st.session
        sess = st.sessions.get(sid)
        if sess is None:
            sess = st.sessions[sid] = {"primary": True, "last_idx": None, "last_user": None, "last_agent": None}
        return sess

    def _user_author(self, st: _State, author_id: Any = None, is_bot: bool = False, author: Any = None) -> str:
        """The ledger author for a user-role message. ``author`` is the core's provenance (D-040): anything but
        ``principal`` (a peer, a bus seat, a job notice, the scheduler) is written under that name."""
        if author and author != "principal":
            return str(author)
        if is_bot:
            return f"bot:{author_id or 'unknown'}"
        if st.principal_ids and author_id is not None and str(author_id) not in st.principal_ids:
            return f"user:{author_id}"
        return st.principal

    def _append(self, st: _State, kind: str, body: dict, author: str, sid: str = "", refs=()) -> dict:
        with st.rt.lock:
            return st.rt.ledger.append(kind, body, author, session=sid or st.session, refs=refs)

    # -- availability and setup --------------------------------------------------

    def is_available(self) -> bool:
        return not self.unavailable_reason()

    def unavailable_reason(self) -> str:
        if _EXO_ERROR:
            return _EXO_ERROR
        try:
            root = C.store_root(self._cfg())
        except ValueError as exc:
            return str(exc)
        if root is None:
            return "no principal configured: set EXO_PRINCIPAL (or EXO_STORE), or save a config with exo-memory"
        if not (root / "PRINCIPAL.md").is_file() and not (root / "nodes").is_dir():
            return f"no principal store at {root}: run exo init <id>"
        return ""

    def get_config_schema(self) -> list[dict[str, Any]]:
        return [dict(f) for f in C.SCHEMA]

    def save_config(self, values: dict[str, Any], exo_home: str) -> None:
        C.save(values, exo_home)

    def identity_signature(self) -> dict[str, Any]:
        try:
            cfg = self._cfg()
            return {"exo-memory.principal": C.principal_id(cfg), "exo-memory.store": str(C.store_root(cfg) or ""),
                    "exo-memory.agent": cfg.get("agent_id") or ""}
        except Exception:
            return {"exo-memory.principal": ""}

    def backup_paths(self) -> list[str]:
        try:
            root = C.store_root(self._cfg())
        except Exception:
            return []
        if root is None:
            return []
        return [str(p) for p in (root / "ledger.sqlite", root / "episodic") if p.exists()]

    # -- lifecycle ---------------------------------------------------------------

    def initialize(self, session_id: str, **kwargs) -> None:
        if _EXO_ERROR:
            raise RuntimeError(_EXO_ERROR)
        home = kwargs.get("exo_home") or kwargs.get("hermes_home") or _home_default()
        key = _home_key(home) or "default"
        with self._lock:
            st = self._states.get(key)
            if st is None:
                cfg = self._cfg(home)
                root = C.store_root(cfg)
                if root is None:
                    raise RuntimeError(self.unavailable_reason() or "exo-memory: no principal store configured")
                agent = C.agent_id(cfg, kwargs.get("agent_identity"))
                rkey = str(root.expanduser().resolve())
                core_log = kwargs.get("agent_log")
                with _RUNTIMES_LOCK:
                    rt = _RUNTIMES.get(rkey)
                    if rt is None:
                        rt = _RUNTIMES[rkey] = _Runtime(Path(rkey), cfg, agent, self._summarizer,
                                                        episodic=core_log is None)
                    rt.refs += 1
                st = self._states[key] = _State(cfg, rt, agent)
                st.core_log = core_log
                st.pack = self._build_pack(st)
                self._pin_principal(st)
        sid = session_id or "default"
        st.session = sid
        primary = kwargs.get("agent_context", "primary") == "primary"
        st.sessions[sid] = {"primary": primary, "last_idx": None, "last_user": None, "last_agent": None}
        if primary:
            body = {"type": "session_start", "platform": kwargs.get("platform"), "provider": PROVIDER_NAME}
            if kwargs.get("parent_session_id"):
                body["parent_session"] = kwargs["parent_session_id"]
            self._safe(lambda: self._append(st, "note", body, st.agent, sid))

    @staticmethod
    def _pin_principal(st: _State) -> None:
        """D-040: ledger events this provider writes as the configured principal are the principal's voice; the
        key registry lists that id so consolidation and learning recognise it."""
        try:
            from exo.keyring import KeyRegistry
            KeyRegistry.load().add_principal(st.principal)
        except Exception as exc:  # a registry problem never stops memory; `exo memory verify` reports it
            logger.warning("exo-memory: cannot record the principal id in the key registry: %s", exc)

    def shutdown(self) -> None:
        with self._lock:
            states = list(self._states.values())
            self._states.clear()
        for st in states:
            with _RUNTIMES_LOCK:
                st.rt.refs -= 1
                if st.rt.refs <= 0:
                    _RUNTIMES.pop(str(st.rt.root), None)
                    st.rt.close()

    @staticmethod
    def _safe(fn):
        try:
            return fn()
        except Exception as exc:
            logger.warning("exo-memory: ledger write failed: %s", exc)
            return None

    # -- system prompt -----------------------------------------------------------

    def _build_pack(self, st: _State) -> str:
        path = st.rt.root / "PRINCIPAL.md"
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            raw = ""
        pack = trim_pack(raw, st.cfg["pack_budget"]) if raw.strip() else ""
        head = "# Principal pack (exo-memory)\n\n" + pack if pack else "# Principal pack (exo-memory)\n\n(empty store)"
        return head + "\n\n" + (CORE_TOOLS_LINE if st.core_log is not None else TOOLS_LINE)

    def system_prompt_block(self) -> str:
        st = self._state()
        return st.pack if st else ""  # built once per process: byte-stable for prompt caching

    # -- recall ------------------------------------------------------------------

    def _assemble(self, st: _State, query: str, budget: int) -> dict:
        limit = max(budget, 50) * 4
        epi = st.epi
        has_epi = epi is not None and len(epi) > 0
        belief_tokens = int(budget * (1 - st.cfg["episodic_share"])) if has_epi else budget
        sections, node_ids, hit_ids = [], [], []
        if st.rt.store.exists():
            asm = assemble(st.rt.store, query, max(belief_tokens - 20, 50))
            if asm.node_ids:
                node_ids = asm.node_ids
                sections.append("## Principal beliefs (exo store)\n" + asm.text)
        used = sum(len(s) + 2 for s in sections)
        if has_epi and limit - used > 200:
            room = limit - used - 80
            head = ("## Your log (memory_zoom(start) opens an entry)" if st.core_log is not None else
                    "## Episodic memory (past sessions; exo_zoom(id, n) opens a line)")
            lines = []
            hits = epi.search(query, n=st.cfg["episodic_hits"], skip_recent=st.cfg["recent_skip"])
            for hit in hits:
                line = f"#{hit['id']} {hit['kind']} {hit['date'][:10]}: {hit['text']}"
                if sum(len(x) + 1 for x in lines) + len(line) + 1 > room // 2:
                    break
                lines.append(line)
                hit_ids.append(hit["id"])
            # the host's tree is already in the system message, so no second view of the same log
            view = "" if st.core_log is not None else epi.render(room - sum(len(x) + 1 for x in lines) - 20)
            if view:
                lines.append("Summary view, oldest first (id+n|text):\n" + view)
            if lines:
                sections.append(head + "\n" + "\n".join(lines))
        text = cap_lines("\n\n".join(sections), limit)
        return {"text": text, "node_ids": node_ids, "episodic_ids": hit_ids, "tokens": len(text) // 4}

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        st = self._state()
        if st is None or is_trivial(query):
            if st:
                st.last_recall = 0
            return ""
        try:
            out = self._assemble(st, query, st.cfg["prefetch_budget"])
        except Exception as exc:
            logger.warning("exo-memory: prefetch failed: %s", exc)
            st.last_recall = 0
            return ""
        st.last_recall = len(out["node_ids"]) + len(out["episodic_ids"])
        return out["text"]

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        st = self._state()
        if st and st.rt.episodic is not None:
            st.rt.episodic.notify()

    def recall_status(self):
        st = self._state()
        if st is None or not st.last_recall or RecallStatus is None:
            return None
        return RecallStatus(provider_label="exo", count=st.last_recall)

    # -- capture: turns ----------------------------------------------------------

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        st = self._state()
        if st is None or not (message or "").strip():
            return
        sid = kwargs.get("session_id") or ""  # the Exo core passes it; sync_turn looks the turn up under it
        sess = self._sess(st, sid)
        if not sess["primary"]:
            return
        author = self._user_author(st, kwargs.get("author_id"), bool(kwargs.get("author_is_bot")), kwargs.get("author"))
        body = {"text": message, "role": "user", "turn": turn_number}
        if kwargs.get("author_name"):
            body["author_name"] = kwargs["author_name"]
        ev = self._safe(lambda: self._append(st, "utterance", body, author, sid))
        if ev:
            sess["last_user"] = (_h(message), ev["id"])
            st.remember(message, ev["id"])

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "",
                  messages: Optional[list[dict[str, Any]]] = None,
                  turn_author: Optional[dict[str, Any]] = None) -> None:
        st = self._state()
        if st is None:
            return
        sid = session_id or st.session
        sess = self._sess(st, sid)
        if not sess["primary"]:
            return  # cron, subagent and flush contexts do not write
        ta = turn_author or {}
        user_id = None
        if (user_content or "").strip():
            last = sess.get("last_user")
            if last and last[0] == _h(user_content):
                user_id = last[1]
            else:
                author = self._user_author(st, ta.get("id"), bool(ta.get("is_bot")), ta.get("author"))
                body = {"text": user_content, "role": "user"}
                if ta.get("name"):
                    body["author_name"] = ta["name"]
                ev = self._safe(lambda: self._append(st, "utterance", body, author, sid))
                if ev:
                    user_id = ev["id"]
                    st.remember(user_content, user_id)
            sess["last_user"] = None
        if (assistant_content or "").strip():
            refs = [user_id] if user_id else []
            ev = self._safe(lambda: self._append(st, "utterance", {"text": assistant_content, "role": "assistant"},
                                                 st.agent, sid, refs))
            if ev:
                sess["last_agent"] = ev["id"]
                st.remember(assistant_content, ev["id"])
        self._log_episodic(st, sess, sid, user_content, assistant_content, messages)

    def checkpoint(self, *, session_id: str = "", messages: Optional[list[dict[str, Any]]] = None,
                   reason: str = "turn") -> None:
        """Core save point. After each turn (``reason="turn"``) and just before a block is summarized
        (``"pre_compress"``) the recent transcript is scanned for rulings and corrections. Keys dedupe
        the overlap between consecutive windows, and with ``on_pre_compress``."""
        st = self._state()
        if st is None or not messages:
            return
        if not self._sess(st, session_id or st.session)["primary"]:
            return
        if reason == "turn" and not st.cfg.get("capture_turns", True):
            return
        window = principal_view(messages)  # D-040: text the principal did not write is not captured
        self._safe(lambda: self._capture(st, window, reason))

    def capture_principal(self, messages: list[dict[str, Any]], *, session_id: str = "",
                          source: str = "leaf") -> Optional[int]:
        """D-032: the core offers a span holding the principal's own messages (with the agent output they answer, and
        each user message flagged ``principal``) before it is summarized (a leaf) or on
        ``/compact``. Rulings, corrections and open decisions among them go to the signed ledger (deduped by
        key, so overlap with ``checkpoint`` and ``on_pre_compress`` costs nothing). Returns how many signals it
        found, or None when there is no store to take them. Raises when a ledger write fails, so the core
        counts the messages as not captured rather than pretending."""
        st = self._state()
        if st is None:
            return None
        if not messages:
            return 0
        if not self._sess(st, session_id or st.session)["primary"]:
            return None
        # a user-role message the host marks "principal": False or with another author (a peer message, a bus seat,
        # a job notice) is not the principal's
        window = principal_view(messages)
        return len(self._capture(st, window, f"capture:{source}", True))

    def _log_episodic(self, st: _State, sess: dict, sid: str, user: str, assistant: str,
                      messages: Optional[list[dict]]) -> None:
        epi = st.rt.episodic
        if epi is None or st.core_log is not None:  # the host's agent log already holds the turn: one log
            return
        from .episodic import entries_from_messages
        try:
            if messages:
                start = sess.get("last_idx")
                # exo: optchat seeded last_idx with the log length, which skipped a new session's
                # first turns. Start at this turn's user message instead, and again after the host
                # compresses (the list gets shorter).
                if start is None or start > len(messages):
                    start = self._turn_start(messages, user)
                entries = list(entries_from_messages(messages[start:]))
                sess["last_idx"] = len(messages)
            else:
                entries = [(k, t) for k, t in (("user", user), ("talk", assistant)) if (t or "").strip()]
            epi.append_many(entries, session=sid)
        except Exception as exc:
            logger.warning("exo-memory: episodic append failed: %s", exc)

    @staticmethod
    def _turn_start(messages: list[dict], user: str) -> int:
        from exo.capture import content_text
        want = normalize(user or "")
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                if not want or normalize(content_text(messages[i].get("content"))) == want:
                    return i
        return max(0, len(messages) - 2)

    def on_session_switch(self, new_session_id: str, *, parent_session_id: str = "", reset: bool = False,
                          rewound: bool = False, **kwargs) -> None:
        st = self._state()
        if st is None:
            return
        old = st.sessions.get(st.session, {})
        st.session = new_session_id or st.session
        sess = self._sess(st)
        sess["primary"] = old.get("primary", True)
        sess["last_idx"] = None
        if sess["primary"]:
            body = {"type": "session_switch", "parent_session": parent_session_id or None,
                    "reset": bool(reset), "rewound": bool(rewound)}
            self._safe(lambda: self._append(st, "note", body, st.agent))

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        st = self._state()
        if st is None or not self._sess(st)["primary"]:
            return
        body = {"type": "consolidate_pending", "messages": len(messages or [])}
        self._safe(lambda: self._append(st, "note", body, st.agent))
        if st.rt.episodic is not None:
            st.rt.episodic.notify()

    # -- capture: memory writes and delegation -----------------------------------

    def on_memory_write(self, action: str, target: str, content: str,
                        metadata: Optional[dict[str, Any]] = None) -> None:
        st = self._state()
        if st is None:
            return
        md = metadata or {}
        body = {"type": "memory_write", "action": action, "target": target, "content": content}
        for k in ("write_origin", "tool_name", "session_id"):
            if md.get(k):
                body[k] = md[k]
        if md.get("previous_content"):
            body["previous_content"] = clip(str(md["previous_content"]), 2000)
        self._safe(lambda: self._append(st, "note", body, st.agent, md.get("session_id") or ""))

    def on_delegation(self, task: str, result: str, *, child_session_id: str = "", **kwargs) -> None:
        st = self._state()
        if st is None:
            return
        body = {"type": "delegation", "task": clip(task or "", 4000), "result": clip(result or "", 4000)}
        if child_session_id:
            body["child_session"] = child_session_id
        self._safe(lambda: self._append(st, "action", body, st.agent))
        if st.rt.episodic is not None and st.core_log is None and (result or "").strip():
            try:
                st.rt.episodic.append_many([("work", f"task: {task}\nresult: {result}")], session=st.session)
            except Exception as exc:
                logger.warning("exo-memory: episodic append failed: %s", exc)

    # -- capture: before compression ---------------------------------------------

    def on_pre_compress(self, messages: list[dict[str, Any]], **kwargs) -> str:
        st = self._state()
        if st is None or not messages:
            return ""
        signals = self._capture(st, principal_view(messages), "pre_compress", bool(kwargs.get("require_checkpoint")))
        return render_block(signals, st.cfg["capture_chars"])

    def _capture(self, st: _State, messages: list[dict[str, Any]], source: str, strict: bool = False) -> list:
        """Extract rulings, corrections and open questions from `messages` and append the new
        ones to the ledger. Returns every signal found (new or not)."""
        signals = extract_signals(messages, skip=_compaction_skip())
        from exo.capture import content_text
        for sig in signals:
            if sig.key in st.rt.capture_keys:
                continue
            try:
                kind, body = to_event(sig, source=source)
                src = content_text(messages[sig.index].get("content"))
                refs = [st.utterances[_h(src)]] if _h(src) in st.utterances else []
                if sig.kind == CORRECTION:
                    body["target"] = self._target_event(st, messages, sig.target_index)
                author = st.agent if sig.role == "assistant" else self._user_author(st)
                self._append(st, kind, body, author, refs=refs)
                st.rt.capture_keys.add(sig.key)
            except Exception as exc:
                if strict:
                    raise
                logger.warning("exo-memory: capture write failed: %s", exc)
        return signals

    def _target_event(self, st: _State, messages: list[dict], idx: Optional[int]) -> str:
        """Ledger id of the agent message a correction judges; logs it first when missing."""
        from exo.capture import content_text
        text = content_text(messages[idx].get("content")) if idx is not None else ""
        if text and _h(text) in st.utterances:
            return st.utterances[_h(text)]
        body = {"text": text or "(agent output not in the transcript)", "role": "assistant",
                "source": "pre_compress_backfill"}
        ev = self._append(st, "utterance", body, st.agent)
        st.remember(text, ev["id"])
        return ev["id"]

    # -- tools -------------------------------------------------------------------

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        tools = [RECALL_SCHEMA, GET_SCHEMA, NOTE_SCHEMA, SHB_SCHEMA]
        st = self._state()
        if st is not None and st.core_log is not None:
            return tools  # the host's memory_zoom opens the one log
        if st is None or st.rt.episodic is not None:
            from .episodic import DATE_SCHEMA, ZOOM_SCHEMA
            tools += [ZOOM_SCHEMA, DATE_SCHEMA]
        return tools

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs) -> str:
        st = self._state()
        if st is None:
            return json.dumps({"error": "exo-memory is not initialized for this profile"})
        args = args or {}
        try:
            if tool_name == "exo_recall":
                query = str(args.get("query") or "").strip()
                if not query:
                    return json.dumps({"error": "exo_recall needs a query"})
                budget = int(args.get("budget") or st.cfg["prefetch_budget"])
                out = self._assemble(st, query, max(100, min(budget, 16000)))
                return json.dumps({"result": out["text"] or "(nothing found)", "node_ids": out["node_ids"],
                                   "episodic_ids": out["episodic_ids"], "tokens": out["tokens"]})
            if tool_name == "exo_get":
                return self._get_node(st, args)
            if tool_name == "exo_note":
                text = str(args.get("text") or "").strip()
                kind = str(args.get("kind") or "note")
                if not text:
                    return json.dumps({"error": "exo_note needs text"})
                if kind in PRINCIPAL_KINDS:  # D-040: the agent cannot speak in the principal's voice
                    ev = self._append(st, "note", {"text": text, "source": "exo_note", "type": "agent_note",
                                                   "claimed_kind": kind}, st.agent)
                    return json.dumps({"ok": True, "id": ev["id"], "kind": "agent_note",
                                       "note": f"kept as your own note, not a {kind}: only the principal's own words "
                                               "become rulings and answers, and they are captured from their messages"})
                if kind not in NOTE_KINDS:
                    return json.dumps({"error": f"kind must be one of {list(NOTE_KINDS)}"})
                ev = self._append(st, kind, {"text": text, "source": "exo_note"}, st.agent)
                return json.dumps({"ok": True, "id": ev["id"], "kind": kind})
            if tool_name == "exo_should_have_been":
                return self._should_have_been(st, args)
            if tool_name in ("exo_zoom", "exo_date"):
                epi = st.epi
                if epi is None:
                    return json.dumps({"error": f"episodic memory unavailable: {st.rt.episodic_error}"})
                if tool_name == "exo_zoom":
                    return json.dumps({"result": epi.zoom(int(args["id"]), int(args["n"]), int(args.get("page", 1)))})
                return json.dumps({"result": epi.date(int(args["id"]))})
        except (KeyError, TypeError, ValueError) as exc:
            return json.dumps({"error": f"bad arguments for {tool_name}: {exc}"})
        except Exception as exc:
            return json.dumps({"error": f"{tool_name} failed: {exc}"})
        return json.dumps({"error": f"unknown tool {tool_name}"})

    def _get_node(self, st: _State, args: dict) -> str:
        nid = str(args.get("id") or "").strip()
        if not nid:
            return json.dumps({"error": "exo_get needs an id"})
        store = st.rt.store
        if not store.exists():
            return json.dumps({"error": "no principal store for this seat"})
        if nid not in store.nodes:
            store.reload()  # a node written since the store was last read
        if nid not in store.nodes:
            near = [i for i, _ in store.find(nid.split("/")[-1].replace("-", " "), n=5)]
            return json.dumps({"error": f"no node {nid}", "did_you_mean": near})
        body_only = bool(args.get("body_only"))
        text = store.render(nid, body_only)
        cut = len(text) > GET_LIMIT
        return json.dumps({"id": nid, "result": text[:GET_LIMIT] + ("\n[cut]" if cut else ""),
                           "body_only": body_only, "truncated": cut})

    def _should_have_been(self, st: _State, args: dict) -> str:
        target = str(args.get("target") or "").strip()
        wanted = str(args.get("wanted") or "").strip()
        if not wanted:
            return json.dumps({"error": "exo_should_have_been needs wanted"})
        sess = self._sess(st)
        if HEX64.match(target) and st.rt.ledger.get(target):
            tid = target
        elif target in ("", "last") and sess.get("last_agent"):
            tid = sess["last_agent"]
        else:
            ev = self._append(st, "artifact", {"description": target or "(unspecified output)",
                                               "source": "exo_should_have_been"}, st.agent)
            tid = ev["id"]
        # D-040: this is the agent's report of a correction, so it is signed as the agent. Consolidation reads a
        # correction as the principal's only from the principal's own message, which capture records.
        body = {"verdict": "should_have_been", "target": tid, "wanted": wanted,
                "reason": str(args.get("reason") or ""), "recorded_by": st.agent, "reported_by": st.agent,
                "for": st.principal}
        ev = self._append(st, "verdict", body, st.agent)
        return json.dumps({"ok": True, "id": ev["id"], "target": tid})
