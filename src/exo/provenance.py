"""Who wrote a message (D-040). The core sets the author from the path a message came in by, never from its text.

Authors, by ingress path:

    principal          a person typing in one of this seat's own clients (turn.send)
    local              a turn from an untrusted socket, TCP, stdio or unstamped caller
    agent              the model's own output
    tool               a tool result
    peer:<name>        another agent session (peers, D-026)
    bus:<seat>         a seat on the Exo Bus
    job                a notice about a background job the agent started
    delegate           the brief a parent agent wrote for a subagent
    schedule           a scheduled prompt
    system             the harness's own notes (thread sync, summaries, imports)

Only ``principal`` is the principal's voice. Capture, consolidation and learning treat text as a ruling, a
correction or an answer from the principal only when its author is the principal; everything else is data.
Where the harness prints a message for the model (the core's view, a context note, a summarizer's source), text by
any other author has its pinned-block tags escaped (``defuse_pins``), so it cannot pose as exo-context's block of
the principal's words. A summary is model text whatever it summarizes, so it is escaped as it is made.

Ledger events carry the author as written by whoever recorded them. ``principal_names`` says which ledger
authors are the principal for a store: ``principal``, the store's own id, and any principal id pinned in the key
registry (exo.keyring).
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

PRINCIPAL = "principal"
LOCAL = "local"
AGENT = "agent"
TOOL = "tool"
JOB = "job"
DELEGATE = "delegate"
SCHEDULE = "schedule"
SYSTEM = "system"

# Ledger kinds that speak in the principal's voice. The agent may not write them for itself.
PRINCIPAL_VOICE_KINDS = frozenset({"ruling", "answer", "verdict"})

# The tags of exo-context's pinned block (exo.handoff): the principal's rulings and corrections, word for word.
PIN_OPEN, PIN_CLOSE = "<exo-pinned>", "</exo-pinned>"
# Either tag as a model may read it: a "<", then the letters of "exo" and "pinned" in any case, with any run of
# characters that are not an ASCII letter, a digit or an angle bracket before, between or inside them (spaces, "-",
# "_", "/", invisible characters), then the rest of the line up to an optional ">".
_GAP = r"[^a-z0-9<>]*"
_PIN_TAG = re.compile("<(" + _GAP + _GAP.join("exopinned") + r"(?![a-z0-9])[^<>\n]*)(>?)", re.I)
# A downstream step may fold a character back to ASCII later, reviving a tag an escaping step already
# passed over: an NFKC pass folds full-width forms (U+FF01-FF5E), the small brackets, mathematical and
# circled letters and more onto ASCII, and unicode tag characters (U+E0020-E007E) may be decoded to the
# printable ASCII they mirror or dropped outright. Detection therefore runs over same-length views of
# the text (see defuse_pins) and edits the original at the same offsets, so no other character changes
# (#322 item 4, #381).
_TAG_RANGE = range(0xE0020, 0xE007F)


def peer(name: str) -> str:
    return f"peer:{name}"


def bus(seat: str) -> str:
    return f"bus:{seat}"


def _ingress_author(meta: Mapping[str, Any]) -> str:
    """The author an ingress tag in ``meta`` names, or ``""`` when there is none (D-040, #370).

    Presence is what counts: an empty tag (``{}``, ``""``) still marks the message as ingress."""
    b = meta.get("bus")
    if b is not None:
        return bus(str((b.get("from") if isinstance(b, Mapping) else b) or "unknown"))
    p = meta.get("peer")
    if p is not None:
        return peer(str((p.get("from") if isinstance(p, Mapping) else p) or "unknown"))
    if meta.get("job") is not None:
        return JOB
    return ""


def author_of(msg: Any) -> str:
    """The author of a core ``Message`` (or a message dict with ``role`` and ``meta``/``author``).

    A message the core stored with ``meta["author"]`` keeps it. An older message without one is read the way
    the core read it before D-040: a peer or job tag, a harness note or an import is not the principal. A
    peer, bus or job tag in ``meta`` outranks any stamped author, the way core capture reads it (#370)."""
    if isinstance(msg, Mapping):
        role, meta, stamped = msg.get("role"), msg.get("meta") or {}, msg.get("author")
    else:
        role, meta, stamped = getattr(msg, "role", None), getattr(msg, "meta", None) or {}, None
    if not isinstance(meta, Mapping):  # #371: an unreadable meta names no ingress tag and no author
        meta = {}
    tag = _ingress_author(meta)
    if tag:
        return tag
    if stamped:
        return str(stamped)
    if meta.get("author"):
        return str(meta["author"])
    return infer(role, meta)


def infer(role: Any, meta: Mapping[str, Any] | None) -> str:
    """The author an untagged message implies by its role and its ingress tags."""
    meta = meta or {}
    if role == "assistant":
        return AGENT
    if role == "tool":
        return TOOL
    if role != "user":
        return SYSTEM
    tag = _ingress_author(meta)
    if tag:
        return tag
    if meta.get("note") or meta.get("local") or meta.get("origin") == "import":
        return SYSTEM
    if meta.get("log_kind", "thread") not in ("thread", ""):
        return SYSTEM
    return PRINCIPAL


def is_principal_author(author: Any) -> bool:
    return author == PRINCIPAL


def _fold_views(text: str) -> list[str]:
    """Same-length views of `text` under the folds a downstream step may apply (#381): the text itself,
    each character whose NFKC form is one ASCII character folded to it (unicode tag characters left as
    they are, so they still read as filler), and that fold plus tag characters decoded to the ASCII they
    mirror. Views that change nothing, or repeat an earlier one, are skipped."""
    compat: dict[int, str] = {}
    decoded: dict[int, str] = {}
    for ch in set(text):
        cp = ord(ch)
        if cp < 128:
            continue
        if cp in _TAG_RANGE:
            decoded[cp] = chr(cp - 0xE0000)
            continue
        folded = unicodedata.normalize("NFKC", ch)
        if len(folded) == 1 and ord(folded) < 128:
            compat[cp] = decoded[cp] = folded
    views = [text]
    if compat:
        views.append(text.translate(compat))
    if decoded and decoded != compat:
        views.append(text.translate(decoded))
    return views


def defuse_pins(text: str) -> str:
    """``text`` with each pinned-block tag escaped, ``<exo-pinned>`` as ``&lt;exo-pinned&gt;``: the tag's ``<``
    always, its ``>`` when it closes the tag on the same line. Nothing else changes and nothing is removed: the words
    stay readable, but they no longer open or close a block of the principal's words (#279). Escaping again changes
    nothing. For text that is not the principal's, printed where the model reads it.

    Detection runs over the views in `_fold_views` and escapes the union of their matches in the
    original, at the same offsets. One view alone is not enough: folding a tag character to its letter
    first would break the match for a tag character sitting inside the tag as filler, the spelling the
    unfolded view catches (#381)."""
    if text.isascii():
        return _PIN_TAG.sub(lambda m: "&lt;" + m[1] + ("&gt;" if m[2] else ""), text)
    starts: set[int] = set()
    ends: set[int] = set()
    for view in _fold_views(text):
        for m in _PIN_TAG.finditer(view):
            starts.add(m.start())
            if m[2]:
                ends.add(m.end() - 1)
    if not starts:
        return text
    out = list(text)
    for i in ends - starts:
        out[i] = "&gt;"
    for i in starts:
        out[i] = "&lt;"
    return "".join(out)


def flat(msg: Any) -> dict[str, Any]:
    """The plain dict a memory or learning hook receives: role, content, author, and for user messages a
    ``principal`` flag. Hooks must not read a user message as the principal's when the flag is False."""
    author = author_of(msg)
    out = {"role": msg.role, "content": msg.text(), "author": author}
    if msg.role == "user":
        out["principal"] = is_principal_author(author)
    return out


def principal_view(messages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Messages as capture should read them: a user message whose author (through `author_of`, meta tags
    included) is not the principal's becomes role ``other``, which the extractors skip. An older host's
    untagged user turn still reads as the principal's and stays a user message."""
    out = []
    for m in messages:
        if not isinstance(m, Mapping):
            continue
        if m.get("role") == "user" and (m.get("principal") is False or author_of(m) != PRINCIPAL):
            m = {**m, "role": "other"}
        out.append(dict(m))
    return out


def principal_names(store: Any = None, extra: Iterable[str] = (), *, home: Any = None) -> frozenset[str]:
    """Ledger authors that are the principal: ``principal``, the store's id (its directory name), principal ids
    pinned in the key registry, and ``extra``."""
    names = {PRINCIPAL, *[str(x) for x in extra if x]}
    root = getattr(store, "root", None)
    if root is not None:
        names.add(root.name)
    try:
        from .keyring import KeyRegistry
        names.update(KeyRegistry.load(home, create=False).principals)
    except Exception:  # no registry yet, or unreadable: the defaults still hold
        pass
    return frozenset(names)


def is_principal_event(ev: Mapping[str, Any] | None, principals: Iterable[str]) -> bool:
    """True when a ledger event was written in the principal's own voice."""
    return bool(ev) and ev.get("author") in set(principals)
