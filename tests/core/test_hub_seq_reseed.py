"""Regression for #237: an external append_event write must not wedge a running Hub.

Hub numbers events in memory (seeded once from last_seq) and SqliteLog stores
that explicit seq, while SessionStore.append_event lets SQLite AUTOINCREMENT
assign it. If append_event runs while a Hub core is live, the Hub's next seq
collides with the row the database already holds; the Hub must reseed and keep
publishing with strictly increasing seqs instead of retrying the dead seq.
"""

from exo.core.engine import SqliteLog
from exo.core.sessions import SessionStore
from exo.protocol import schema as s
from exo.server.engine import Hub


def test_hub_reseeds_after_external_append_event(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite")
    hub = Hub(SqliteLog(store))

    first = hub.publish(s.Notice(text="one"))
    external = store.append_event(s.Notice(text="external"))  # SQLite assigns this seq
    assert external.seq > first.seq

    second = hub.publish(s.Notice(text="two"))
    assert second.seq > external.seq
    assert second.seq > first.seq
    assert second.seq == store.last_event_seq()

    third = hub.publish(s.Notice(text="three"))
    assert third.seq > second.seq
    assert third.seq == store.last_event_seq()

    stored = store.events_since(0)
    assert [ev.seq for ev in stored] == [first.seq, external.seq, second.seq, third.seq]
    assert stored[2].id == second.id  # the event stored is the event fanned out
