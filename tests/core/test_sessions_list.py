"""#255 W1-2: list_sessions reads its message counts in the same query, not one COUNT per session."""
from __future__ import annotations

from exo.core.sessions import SessionStore


def _store(n_sessions: int) -> SessionStore:
    st = SessionStore(":memory:")
    for i in range(n_sessions):
        st.create_session(title=f"s{i}", seat="a" if i % 2 else "b",
                          agent="ag" if i % 3 else None, id=f"s{i:03d}")
        st._q("UPDATE sessions SET updated=? WHERE id=?", (f"2026-01-{i + 1:02d}T00:00:00Z", f"s{i:03d}"))
        for j in range(i % 4):  # 0..3 messages; the first is hidden, so it never counts
            st._q("INSERT INTO messages (id, session, seq, role, created, data, hidden)"
                  " VALUES (?,?,?,?,?,?,?)",
                  (f"m{i}-{j}", f"s{i:03d}", j, "user", "2026-01-01T00:00:00Z", "{}", 1 if j == 0 else 0))
    return st


def _counted(st: SessionStore, **kw):
    seen: list[str] = []
    st._db.set_trace_callback(seen.append)
    try:
        return st.list_sessions(**kw), len(seen)
    finally:
        st._db.set_trace_callback(None)


def test_list_sessions_query_count_does_not_grow_with_sessions():
    small, small_q = _counted(_store(5), limit=500)
    big, big_q = _counted(_store(50), limit=500)
    assert len(small) == 5 and len(big) == 50
    assert small_q == big_q == 1


def test_list_sessions_counts_filters_and_order_match_get_session():
    st = _store(9)
    listed = st.list_sessions(limit=500)
    assert [i.id for i in listed] == [f"s{i:03d}" for i in reversed(range(9))]  # updated desc
    for info in listed:
        assert info == st.get_session(info.id)
    assert [i.id for i in st.list_sessions(limit=500, seat="a")] == ["s007", "s005", "s003", "s001"]
    assert [i.id for i in st.list_sessions(limit=500, agent="ag")] == [
        "s008", "s007", "s005", "s004", "s002", "s001"]
    assert [i.id for i in st.list_sessions(limit=2)] == ["s008", "s007"]
    assert st.list_sessions(limit=500, seat="nobody") == []
