import json

import pytest

from exo.core.context import SummaryNode
from exo.core.sessions import SessionStore
from exo.core.types import Message
from exo.summary_zoom import PAGE_CHARS, SessionSummaryView


@pytest.fixture
def tree():
    store = SessionStore()
    sid = store.create_session(agent="a").id
    for i in range(20):
        store.append_message(sid, Message.user(f"original {i}"))
    for node in (SummaryNode("left", sid, 0, 0, 1, 10, "first", "done"),
                 SummaryNode("right", sid, 0, 1, 11, 20, "second", "done"),
                 SummaryNode("root", sid, 1, 0, 1, 20, "overview", "done")):
        store.save_summary(node)
    yield store, sid, SessionSummaryView(store, sid, "a")
    store.close()


def test_drill_down_to_exact_original_without_writes(tree):
    store, sid, view = tree
    before = store._db.total_changes
    assert [c["id"] for c in view.describe("root")["children"]] == ["left", "right"]
    assert view.describe("left")["children"] == []
    assert json.loads(view.source("left", 3)["payload"]["text"])["content"][0]["text"] == "original 2"
    assert view.source("left", 3)["message_id"] == store.messages(sid)[2].id
    assert store._db.total_changes == before


def test_cursor_covers_originals_once_and_hides_undone(tree):
    store, sid, view = tree
    first = view.sources("root")
    assert len(first["messages"]) == 16
    assert [m["seq"] for m in view.sources("root", first["next_after"])["messages"]] == [17, 18, 19, 20]
    store._q("UPDATE messages SET hidden=1 WHERE session=? AND seq=3", (sid,))
    assert 3 not in [m["seq"] for m in view.sources("left")["messages"]]
    with pytest.raises(ValueError, match="unavailable"):
        view.source("left", 3)


def test_pages_reassemble_exact_json_and_stable_digest(tree):
    store, sid, view = tree
    store._q("UPDATE messages SET data=? WHERE session=? AND seq=1", ('"' + 'x' * PAGE_CHARS + '"', sid))
    one = view.source("left", 1)["payload"]
    two = view.source("left", 1, 2)["payload"]
    assert one["text"] + two["text"] == '"' + 'x' * PAGE_CHARS + '"'
    assert one["sha256"] == two["sha256"]
    with pytest.raises(ValueError):
        view.source("left", 1, 3)


def test_no_cross_scope_or_private_ancestry(tree):
    store, sid, view = tree
    other = store.create_session(agent="b").id
    store.save_summary(SummaryNode("foreign", other, 0, 0, 1, 1, "secret", "done"))
    with pytest.raises(ValueError, match="unavailable"):
        view.describe("foreign")
    for caller, agent in ((sid, "b"), ("agent:a", "a"), (other, "a")):
        with pytest.raises(ValueError):
            SessionSummaryView(store, caller, agent)
    private = store.create_session(agent="a", kind="private").id
    store._q("UPDATE sessions SET parent=? WHERE id=?", (private, sid))
    with pytest.raises(ValueError):
        view.describe("root")
    with pytest.raises(ValueError):
        SessionSummaryView(store, private, "a")


@pytest.mark.parametrize("operation", [lambda v: v.source("left", 11), lambda v: v.source("left", True),
                                      lambda v: v.sources("left", -1), lambda v: v.describe("left", 0)])
def test_invalid_bounds(tree, operation):
    with pytest.raises(ValueError):
        operation(tree[2])


def test_missing_or_inconsistent_children_fail_closed(tree):
    store, sid, view = tree
    store._q("UPDATE summaries SET start_seq=12 WHERE id='right'")
    with pytest.raises(ValueError, match="children"):
        view.describe("root")
