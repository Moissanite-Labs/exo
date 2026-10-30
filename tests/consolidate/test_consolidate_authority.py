"""An accept on a ledger kept by an authority (docs/LEDGER-AUTHORITY.md). The authority can commit the
`belief_accepted` event while the append still raises. The node or skill file stays when the event stands or may
stand. It goes back only when the authority shows the event never landed. Synthetic keys and disposable stores only."""
import errno
from pathlib import Path

import pytest

from exo import consolidate as C
from exo import crypto
from exo.ledger import Ledger
from exo.ledger_authority import AppendOutcomeUnknown, AuthorityUnavailable, ProjectionPending
from exo.ledger_authority.journal import AuthorityJournal, JournalClient

AUTHORS = ["memory", "principal", "agent", "exo-consolidate", "exo-learn"]
LESSON = "Add the link."


@pytest.fixture
def led(tmp_path):
    key, checkpoint = crypto.keygen(), crypto.keygen()
    identity = {"version": 1, "stream_id": "TEST-accept", "checkpoint_pub": crypto.pubkey_b64(checkpoint),
                "writers": {"memory": {"pub": crypto.pubkey_b64(key), "author": "memory", "authors": AUTHORS}}}
    journal = AuthorityJournal.provision(tmp_path / "authority.sqlite", identity, {"memory": key}, checkpoint)
    ledger = Ledger(tmp_path / "projection.sqlite", authority=JournalClient(journal, "memory"))
    yield ledger
    ledger.close()


def _node_proposal(store, led):
    """A proposal that creates a new node."""
    led.append("ruling", {"text": "Never sign external comments with agent names."}, "principal")
    res = C.consolidate_run(store, led, author="exo-consolidate")
    p = C.load_state(led).proposals[res.proposals[0]]
    return p.id, store.nodes_dir / f"{p.node_id}.md"


def _skill_proposal(store, led):
    """A skill patch that adds the lesson LESSON to a new SKILL.md."""
    out = led.append("utterance", {"text": "old output", "role": "assistant"}, "agent", session="s1")
    ev = led.append("verdict", {"verdict": "edit", "target": out["id"], "text": "status has a link"}, "principal",
                    session="s1")
    (r,) = C.propose_typed(store, led, [{"type": "skill", "skill": "Status Update", "patch": LESSON,
                                         "claim": "Status has a link.", "evidence": [ev["id"]]}], author="exo-learn")
    return r["id"], store.root / "skills" / "status-update" / "SKILL.md"


def _accept_another(store, led):
    """Accept an unrelated lesson, so the ledger holds a `belief_accepted` for some other proposal."""
    out = led.append("utterance", {"text": "older output", "role": "assistant"}, "agent", session="s0")
    ev = led.append("verdict", {"verdict": "edit", "target": out["id"], "text": "say the date"}, "principal",
                    session="s0")
    (r,) = C.propose_typed(store, led, [{"type": "skill", "skill": "Daily Note", "patch": "Say the date.",
                                         "claim": "Daily notes say the date.", "evidence": [ev["id"]]}],
                           author="exo-learn")
    C.proposal_accept(store, led, r["id"])


# Each way the `belief_accepted` append breaks is four flags: the authority commits it, the reply is lost, lookup
# fails, and sync fails after the authority call. A commit with the reply kept and sync failing is ProjectionPending.
BREAKS = {
    "lost reply": (True, True, False, False),
    "lost reply, lookup down": (True, True, True, False),
    "lost reply, authority down": (True, True, True, True),
    "projection pending": (True, False, False, True),
    "lost request": (False, True, False, False),
    "lost request, lookup down": (False, True, True, False),
    "lost request, authority down": (False, True, True, True),
}


def _break_the_accept(mp, led, how):
    """Make the `belief_accepted` append fail the way BREAKS[how] says, until `mp` is undone."""
    commits, reply_lost, lookup_down, sync_down = BREAKS[how]
    client, real_append, real_sync = led.authority, led.authority.append, led.sync_authority
    called = []

    def sync():
        if called and sync_down:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_sync()

    def append(operation_id, expected_head, intent):
        if intent["kind"] != "belief_accepted":
            return real_append(operation_id, expected_head, intent)
        called.append(operation_id)
        record = real_append(operation_id, expected_head, intent) if commits else None
        if reply_lost:
            raise AuthorityUnavailable("connection reset")
        return record

    def lookup(operation_id):
        raise AuthorityUnavailable("authority unreachable")

    mp.setattr(client, "append", append)
    mp.setattr(led, "sync_authority", sync)
    if lookup_down:
        mp.setattr(client, "lookup", lookup)


def _accepts(led, pid):
    return [e for e in led.iter(kind="belief_accepted") if e["body"]["proposal_id"] == pid]


def _accept_through(mp, store, led, pid, how):
    _break_the_accept(mp, led, how)
    with pytest.raises(ProjectionPending if how == "projection pending" else AppendOutcomeUnknown):
        C.proposal_accept(store, led, pid)


@pytest.mark.parametrize("path_kind", ["node", "skill"])
@pytest.mark.parametrize("how", ["lost reply", "lost reply, lookup down", "lost reply, authority down",
                                 "projection pending"])
def test_an_accept_the_authority_committed_keeps_its_file(template_store, led, monkeypatch, path_kind, how):
    pid, path = (_node_proposal if path_kind == "node" else _skill_proposal)(template_store, led)
    with monkeypatch.context() as mp:
        _accept_through(mp, template_store, led, pid, how)
    assert path.is_file()   # the authority holds the accept, so the lesson stays in its file
    written = path.read_text(encoding="utf-8")
    # A lookup or sync that answers also repairs the local copy. Otherwise the next sync does.
    assert len(_accepts(led, pid)) == (0 if how.endswith(("authority down", "pending")) else 1)
    led.sync_authority()
    assert len(_accepts(led, pid)) == 1
    with pytest.raises(C.ConsolidateError, match="already accepted"):
        C.proposal_accept(template_store, led, pid)
    assert path.read_text(encoding="utf-8") == written
    if path_kind == "skill":
        assert written.count(LESSON) == 1


@pytest.mark.parametrize("path_kind", ["node", "skill"])
@pytest.mark.parametrize("how", ["lost request", "lost request, lookup down"])
def test_an_accept_the_authority_never_received_puts_the_file_back(template_store, led, monkeypatch, path_kind,
                                                                    how):
    pid, path = (_node_proposal if path_kind == "node" else _skill_proposal)(template_store, led)
    _accept_another(template_store, led)   # an accept of another proposal is no proof for this one
    with monkeypatch.context() as mp:
        _accept_through(mp, template_store, led, pid, how)
    assert not path.exists() and not _accepts(led, pid)
    out = C.proposal_accept(template_store, led, pid)
    assert out["action"] == "create" and len(_accepts(led, pid)) == 1
    if path_kind == "skill":
        assert path.read_text(encoding="utf-8").count(LESSON) == 1


@pytest.mark.parametrize("path_kind", ["node", "skill"])
def test_an_accept_nobody_can_confirm_keeps_its_file(template_store, led, monkeypatch, path_kind):
    """The authority cannot answer, so the outcome stays unknown: the file stays, since a lost lesson is worse."""
    pid, path = (_node_proposal if path_kind == "node" else _skill_proposal)(template_store, led)
    with monkeypatch.context() as mp:
        _accept_through(mp, template_store, led, pid, "lost request, authority down")
    assert path.is_file() and not _accepts(led, pid)


# An interrupt (Ctrl-C) can land while the authority call is in flight. Then the outcome is unknown, as for a lost
# reply, and the lookup decides. An interrupt before the ledger write, or on a plain ledger, puts the file back.

def _down(*args):
    raise AuthorityUnavailable("authority unreachable")


@pytest.mark.parametrize("path_kind", ["node", "skill"])
@pytest.mark.parametrize("commits", [True, False], ids=["after the commit", "before the commit"])
def test_an_interrupted_accept_asks_the_authority(template_store, led, monkeypatch, path_kind, commits):
    pid, path = (_node_proposal if path_kind == "node" else _skill_proposal)(template_store, led)
    real_append = led.authority.append

    def append(operation_id, expected_head, intent):
        if intent["kind"] == "belief_accepted":
            if commits:
                real_append(operation_id, expected_head, intent)
            raise KeyboardInterrupt
        return real_append(operation_id, expected_head, intent)
    with monkeypatch.context() as mp:
        mp.setattr(led.authority, "append", append)
        with pytest.raises(KeyboardInterrupt):
            C.proposal_accept(template_store, led, pid)
    assert path.is_file() == commits and len(_accepts(led, pid)) == int(commits)
    if commits:
        with pytest.raises(C.ConsolidateError, match="already accepted"):
            C.proposal_accept(template_store, led, pid)
    else:
        C.proposal_accept(template_store, led, pid)
    assert len(_accepts(led, pid)) == 1
    if path_kind == "skill":
        assert path.read_text(encoding="utf-8").count(LESSON) == 1


@pytest.mark.parametrize("path_kind", ["node", "skill"])
def test_an_interrupt_before_the_ledger_write_puts_the_file_back(template_store, led, monkeypatch, path_kind):
    """The interrupt cuts the file write short while the authority is down. The accept never reached the ledger, so
    the file goes back without asking the authority."""
    pid, path = (_node_proposal if path_kind == "node" else _skill_proposal)(template_store, led)
    real_write = Path.write_text

    def write_text(self, data, *args, **kwargs):
        if self == path:
            real_write(self, data[: len(data) // 2], *args, **kwargs)
            raise KeyboardInterrupt
        return real_write(self, data, *args, **kwargs)
    with monkeypatch.context() as mp:
        mp.setattr(Path, "write_text", write_text)
        mp.setattr(led.authority, "lookup", _down)
        mp.setattr(led, "sync_authority", _down)
        with pytest.raises(KeyboardInterrupt):
            C.proposal_accept(template_store, led, pid)
    assert not path.exists() and not _accepts(led, pid)


@pytest.mark.parametrize("path_kind", ["node", "skill"])
def test_an_interrupted_accept_without_an_authority_puts_the_file_back(template_store, signer, monkeypatch,
                                                                        path_kind):
    """A plain ledger has nobody to ask, so an interrupt during its write puts the file back like any failure."""
    with Ledger(template_store.ledger_path, signer) as local:
        pid, path = (_node_proposal if path_kind == "node" else _skill_proposal)(template_store, local)
        real_append = local.append

        def append(kind, *args, **kwargs):
            if kind == "belief_accepted":
                raise KeyboardInterrupt
            return real_append(kind, *args, **kwargs)
        with monkeypatch.context() as mp:
            mp.setattr(local, "append", append)
            with pytest.raises(KeyboardInterrupt):
                C.proposal_accept(template_store, local, pid)
        assert not path.exists() and not _accepts(local, pid)
