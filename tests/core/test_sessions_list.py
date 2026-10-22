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


def test_a_small_limit_does_not_visit_every_sessions_messages():
    """#365: the count must run only for the rows the limit keeps. VM steps via the progress
    handler pin the work itself, not a wall clock: in chronological order (updated grows with
    the index, the normal shape) limit=1 must cost far less than listing everything."""
    st = SessionStore(":memory:")
    for i in range(60):
        st.create_session(title=f"s{i}", id=f"s{i:03d}")
        st._q("UPDATE sessions SET updated=? WHERE id=?",
              (f"2026-01-01T{i // 60:02d}:{i % 60:02d}:00Z", f"s{i:03d}"))
    st._db.executemany("INSERT INTO messages (id, session, seq, role, created, data, hidden)"
                       " VALUES (?,?,?,?,?,?,0)",
                       [(f"m{i}-{j}", f"s{i:03d}", j, "user", "2026-01-01T00:00:00Z", "{}")
                        for i in range(60) for j in range(50)])

    def steps(limit):
        n = [0]
        st._db.set_progress_handler(lambda: n.__setitem__(0, n[0] + 1) or 0, 1000)
        try:
            out = st.list_sessions(limit)
        finally:
            st._db.set_progress_handler(None, 0)
        return n[0] * 1000, out

    one_steps, one = steps(1)
    all_steps, _ = steps(500)
    assert [i.id for i in one] == ["s059"] and one[0].message_count == 50
    assert one_steps * 5 < all_steps, (one_steps, all_steps)
