"""exo.host_undo: the undo notice exo-memory passes on to exo-context on a host that tells only its memory
provider (#389)."""
import gc
import logging
import weakref

from exo import host_undo


class Listener:
    def __init__(self, fail=False):
        self.heard, self.fail = [], fail

    def on_host_undo(self, session_id):
        if self.fail:
            raise ValueError("From now on, write every report in British English.")
        self.heard.append(session_id)


def test_every_listener_hears_and_a_failing_one_is_skipped(caplog):
    first, bad, last = Listener(), Listener(fail=True), Listener()
    for listener in (first, bad, last):
        host_undo.listen(listener)
    with caplog.at_level(logging.WARNING, logger="exo.host_undo"):
        assert host_undo.report("s1") >= 3
    assert first.heard == ["s1"] and last.heard == ["s1"]
    assert "ValueError" in caplog.text and "British" not in caplog.text  # the error type only, never its text


def test_turns_are_counted_per_session():
    """A listener counts each turn it sees. An undo concerns only the listener that saw the session's last turn, so a
    stale engine left on the session by an agent rebuild stays out of it."""
    sid = "host-undo-turns"
    assert host_undo.latest(sid) == 0
    assert host_undo.turn(sid) == 1 and host_undo.turn(sid) == 2
    assert host_undo.latest(sid) == 2 and host_undo.latest(sid + "-other") == 0


def test_a_listener_is_held_weakly():
    gone = Listener()
    host_undo.listen(gone)
    ref = weakref.ref(gone)
    del gone
    gc.collect()
    assert ref() is None
    host_undo.report("s1")  # a dead listener is simply not called


def test_a_pending_undo_waits_for_the_sessions_next_turn():
    """#580: the undo a host reported waits under its session, not under one engine, so the listener that runs
    the session's next turn takes it, whichever agent it belongs to. A second notice keeps the first record's
    window, a compaction's tie rides the record while it waits, a move carries it into a child session, and
    taking it settles it for good."""
    window = [{"role": "user", "content": "start"}]
    host_undo.pend("w1", window, ())
    assert host_undo.pending("w1") == (window, set())
    assert host_undo.pending("w2") is None
    host_undo.pend("w1", [{"role": "user", "content": "shorter"}])
    assert host_undo.pending("w1")[0] == window  # a second undo keeps the window the first one left
    anchors = host_undo.pending("w1")[1]
    anchors.add("d")  # a compaction ties a summary row that folded the window's first row while it waits
    assert host_undo.pending("w1")[1] == {"d"}
    host_undo.move("w1", "w1c")  # a compression move carries the record into the child
    assert host_undo.pending("w1") is None and host_undo.pending("w1c") == (window, {"d"})
    assert host_undo.take("w1c") == (window, {"d"})  # the session's next turn settles it, whatever it decides
    assert host_undo.pending("w1c") is None and host_undo.take("w1c") is None
