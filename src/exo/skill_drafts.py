"""Evidence-bound skill candidate export (#678). No loader, installation or execution path."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class Excerpt:
    """A reviewed-by-caller character span in one visible message's text, not arbitrary model prose."""
    seq: int
    start: int
    end: int


def draft_from_session(store: Any, caller_session: str, agent: str, *, title: str, purpose: str,
                       excerpts: list[Excerpt]) -> dict[str, Any]:
    """Build a candidate, with every step copied verbatim from host-selected session evidence.

    The host establishes scope and chooses excerpts. Stored assistant text is evidence of what was said,
    not proof it succeeded or is safe. Purpose/title are proposals, never evidence-backed conclusions.
    """
    if (not agent or store.thread(caller_session) != (agent, "thread") or store.private(caller_session)):
        raise ValueError("ordinary owned session required")
    if (not isinstance(title, str) or not title.strip() or len(title) > 120
            or not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 1000):
        raise ValueError("bounded title and purpose required")
    if not isinstance(excerpts, list) or not 1 <= len(excerpts) <= 16:
        raise ValueError("one to sixteen evidence excerpts required")
    steps = []
    seen = set()
    for ref in excerpts:
        if (not isinstance(ref, Excerpt) or any(type(v) is not int for v in (ref.seq, ref.start, ref.end))
                or ref.seq < 1 or ref.start < 0 or not ref.start < ref.end <= ref.start + 2000):
            raise ValueError("invalid evidence bounds")
        key = (ref.seq, ref.start, ref.end)
        if key in seen:
            raise ValueError("duplicate evidence")
        seen.add(key)
        messages = store.messages(caller_session, since=ref.seq - 1, upto=ref.seq)
        if len(messages) != 1:
            raise ValueError("evidence unavailable")
        message = messages[0]
        text = message.text()
        if ref.end > len(text) or not text[ref.start:ref.end].strip():
            raise ValueError("evidence outside message text")
        steps.append({"text": text[ref.start:ref.end], "evidence": {
            "session": caller_session, "seq": ref.seq, "message_id": message.id,
            "role": message.role, "start": ref.start, "end": ref.end,
            "message_text_sha256": hashlib.sha256(text.encode()).hexdigest()}})
    # Excerpts are untrusted material for review, including any commands or instructions they contain.
    return {"schema": "exo.skill-candidate.v1", "id": f"candidate-{uuid4().hex}",
            "status": "pending_human_review", "loadable": False, "agent": agent,
            "source_session": caller_session, "title": title.strip(), "purpose_proposal": purpose.strip(),
            "evidence_claim": "recorded_text_only; success, safety and generality unverified",
            "steps": steps}


def queue_draft(store: Any, caller_session: str, agent: str, *, queue: Path, title: str, purpose: str,
                excerpts: list[Excerpt]) -> Path:
    """Write a private, exclusive JSON review artifact. Never writes SKILL.md or registers a skill.

    The host selects the queue directory. It must not be a skills discovery directory. This API has no
    approval operation: a later reviewer must check evidence, redact, generalize and choose installation.
    """
    draft = draft_from_session(store, caller_session, agent, title=title, purpose=purpose, excerpts=excerpts)
    queue = Path(queue)
    if queue.is_symlink():
        raise ValueError("review queue cannot be a symlink")
    queue.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not queue.is_dir() or queue.stat().st_mode & 0o077:
        raise ValueError("review queue must be private")
    destination = queue / f"{draft['id']}.json"
    payload = (json.dumps(draft, ensure_ascii=False, indent=2) + "\n").encode()
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(payload)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination
