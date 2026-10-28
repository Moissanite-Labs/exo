import json
import stat

import pytest

from exo.core.sessions import SessionStore
from exo.core.types import Message
from exo.skill_drafts import Excerpt, draft_from_session, queue_draft


@pytest.fixture
def source():
    store = SessionStore()
    sid = store.create_session(agent="a").id
    store.append_message(sid, Message.user("First inspect the fixture. Then run the focused tests."))
    store.append_message(sid, Message.assistant("The test passed in this synthetic example."))
    yield store, sid
    store.close()


def build(source, **changes):
    args = dict(title="Fixture workflow", purpose="Candidate workflow for review", excerpts=[Excerpt(1, 0, 26)])
    args.update(changes)
    return draft_from_session(source[0], source[1], "a", **args)


def test_candidate_contains_exact_evidence_and_no_success_claim(source):
    draft = build(source)
    step = draft["steps"][0]
    assert step["text"] == source[0].messages(source[1])[0].text()[:26]
    assert step["evidence"]["message_id"] == source[0].messages(source[1])[0].id
    assert step["evidence"]["role"] == "user"
    assert draft["status"] == "pending_human_review" and draft["loadable"] is False
    assert "unverified" in draft["evidence_claim"]
    assert "SKILL.md" not in json.dumps(draft)


def test_assistant_claim_remains_recorded_text_only(source):
    draft = build(source, excerpts=[Excerpt(2, 0, 10)])
    assert draft["steps"][0]["evidence"]["role"] == "assistant"
    assert draft["evidence_claim"].startswith("recorded_text_only")


@pytest.mark.parametrize("excerpts", [[], [Excerpt(1, 0, 26)] * 2, [Excerpt(0, 0, 1)],
                                      [Excerpt(1, -1, 1)], [Excerpt(1, 0, 3000)],
                                      [Excerpt(1, 0, 100)], [Excerpt(True, 0, 1)], ["invented"]])
def test_invalid_or_unsupported_evidence_rejected(source, excerpts):
    with pytest.raises(ValueError):
        build(source, excerpts=excerpts)


def test_hidden_source_cannot_be_exported(source):
    source[0].truncate(source[1], 0)
    with pytest.raises(ValueError, match="unavailable"):
        build(source)


def test_scope_rejects_private_descendants_and_foreign_agents(source):
    store, sid = source
    with pytest.raises(ValueError):
        draft_from_session(store, sid, "other", title="t", purpose="p", excerpts=[Excerpt(1, 0, 1)])
    private = store.create_session(agent="a", kind="private").id
    store._q("UPDATE sessions SET parent=? WHERE id=?", (private, sid))
    with pytest.raises(ValueError):
        build(source)


def test_source_mutation_changes_digest_and_excerpt_rechecked(source):
    before = build(source)["steps"][0]["evidence"]["message_text_sha256"]
    msg = Message.user("Changed source message long enough to select.")
    from exo.core.types import to_dict
    source[0]._q("UPDATE messages SET data=? WHERE session=? AND seq=1", (json.dumps(to_dict(msg)), source[1]))
    assert build(source)["steps"][0]["evidence"]["message_text_sha256"] != before


def test_queue_is_private_unique_json_and_does_not_modify_store(source, tmp_path):
    store, sid = source
    before = store._db.total_changes
    args = dict(queue=tmp_path / "review-queue", title="t", purpose="p", excerpts=[Excerpt(1, 0, 26)])
    first = queue_draft(store, sid, "a", **args)
    second = queue_draft(store, sid, "a", **args)
    assert first != second
    assert first.suffix == ".json" and json.loads(first.read_text())["loadable"] is False
    assert stat.S_IMODE(first.stat().st_mode) == 0o600
    assert stat.S_IMODE(first.parent.stat().st_mode) == 0o700
    assert store._db.total_changes == before
    assert not list(tmp_path.rglob("SKILL.md"))


def test_unsafe_queue_rejected(source, tmp_path):
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    link = tmp_path / "link"
    link.symlink_to(public)
    for queue in (public, link):
        with pytest.raises(ValueError):
            queue_draft(*source, "a", queue=queue, title="t", purpose="p", excerpts=[Excerpt(1, 0, 1)])
