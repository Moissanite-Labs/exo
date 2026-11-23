"""Gateway undo without a provider notification (#537): the first host turn of a new engine drops a pin the host's
transcript no longer holds, when the transcript is the one noted last with its tail cut at or before the pin's lead.
The controls keep pins after a transcript cut short, a compaction and an earlier undo."""
import gc
import json
import re

import pytest

from exo import handoff as hf
from exo import host_undo
from exo_context.engine import ExoContextEngine
from exo_context.inner import END_LINE

SYS = {'role': 'system', 'content': 'sys'}
RULING = 'From now on, write every report in British English.'
NOTE = 'From now on, deploy on Fridays without a review.'


def mine(text):
    return {'role': 'user', 'author': 'principal', 'principal': True, 'content': text}


def ok():
    return {'role': 'assistant', 'content': 'ok'}


def seed(sid):
    engine = ExoContextEngine(write_handoffs=False)
    engine.on_session_start(sid)
    before = [SYS, mine('begin the importer'), {'role': 'assistant', 'content': 'ok'}, mine(RULING)]
    engine.select_context(before, session_id=sid, conversation_messages=before, incoming_message=before[-1])
    assert engine._load_pins()['ruling'] == [RULING]
    return engine


def test_gateway_undo_without_notice_does_not_restore_the_removed_ruling():
    sid = 'gateway-undo-537'
    seed(sid)
    # The gateway rewinds the transcript and evicts its agent without host_undo.report or on_session_switch.
    after = [SYS, mine('begin the importer'), {'role': 'assistant', 'content': 'ok'}, mine('continue after undo')]
    fresh = ExoContextEngine(write_handoffs=False)
    out = fresh.select_context(after, session_id=sid, conversation_messages=after, incoming_message='continue after undo')
    assert RULING not in '\n'.join(hf.message_text(m) for m in (out or after))
    assert fresh._load_pins()['ruling'] == []


def test_a_fresh_truncated_transcript_is_not_proof_of_undo():
    sid = 'gateway-truncated-537'
    seed(sid)
    fresh = ExoContextEngine(write_handoffs=False)
    tail = [SYS, mine('continue')]
    fresh.select_context(tail, session_id=sid, conversation_messages=tail)
    assert fresh._load_pins()['ruling'] == [RULING]


class Summary:
    def compress(self, messages, **kwargs):
        text = '\n\n'.join(hf.message_text(m) for m in messages[1:-1])
        return [messages[0], {'role': 'user', '_compressed_summary': True,
                            'content': '[CONTEXT COMPACTION] notes\n\n' + text + '\n\n' + END_LINE}, messages[-1]]


def test_a_fresh_engine_retains_a_pin_with_persisted_compaction_evidence():
    sid = 'gateway-folded-537'
    engine = ExoContextEngine(inner=Summary(), write_handoffs=False)
    engine.on_session_start(sid)
    before = [SYS, mine(RULING), {'role': 'assistant', 'content': 'ok'}, mine('keep tail')]
    engine.select_context(before)
    folded = engine.compress(before, force=True)
    data = json.loads(engine._pin_file().read_text())
    assert data.get('folded')
    fresh = ExoContextEngine(write_handoffs=False)
    fresh.select_context(folded, session_id=sid, conversation_messages=folded)
    assert fresh._load_pins()['ruling'] == [RULING]


def test_a_fresh_engine_preserves_an_existing_undo_tombstone():
    sid = 'gateway-tombstone-537'
    engine = seed(sid)
    after = [SYS, mine('begin the importer'), {'role': 'assistant', 'content': 'ok'}]
    engine.on_session_switch(sid, rewound=True, messages=after)
    assert engine._load_pins()['ruling'] == []
    assert json.loads(engine._pin_file().read_text()).get('dropped')
    fresh = ExoContextEngine(write_handoffs=False)
    fresh.select_context(after + [mine('continue')], session_id=sid, conversation_messages=after)
    assert fresh._load_pins()['ruling'] == []
    assert json.loads(fresh._pin_file().read_text()).get('dropped')


class Paraphrase:
    """A host summary that names what it folded in its own words, in a flagged user row, as Hermes's does."""

    def compress(self, messages, **kwargs):
        summary = {'role': 'user', '_compressed_summary': True,
                   'content': f'[CONTEXT COMPACTION] {len(messages) - 2} earlier rows.\n\n' + END_LINE}
        return [messages[0], summary, messages[-1]]


class Gateway:
    """Drives engines as Hermes's messaging gateway does. Each turn sends the request with the whole transcript as
    conversation_messages and the turn's message as incoming_message. /undo N cuts the last N user turns, evicts the
    cached agent and tells no one, so the next turn runs in a new engine that reads the pins from disk."""

    def __init__(self, sid, inner=None):
        self.sid, self.inner, self.history = sid, inner, []
        self.engine = self.agent()

    def agent(self):
        engine = ExoContextEngine(inner=self.inner, write_handoffs=False)
        engine.on_session_start(self.sid)
        return engine

    def turn(self, text, listed=True):
        """`listed=False` leaves the turn's message out of conversation_messages, as a host may."""
        self.history.append(mine(text))
        sent = self.history if listed else self.history[:-1]
        out = self.engine.select_context([SYS, *self.history], conversation_messages=list(sent),
                                         incoming_message=self.history[-1], budget_tokens=0)
        self.history.append(ok())
        self.engine.on_turn_complete(list(self.history), usage=None)
        return out

    def undo(self, n=1):
        users = [i for i, m in enumerate(self.history) if m['role'] == 'user' and not m.get('_compressed_summary')]
        self.history = self.history[:users[-n]]
        self.engine = None  # evicted: the old engine is gone before the next message
        gc.collect()
        self.engine = self.agent()

    def compress(self):
        self.history = self.engine.compress(list(self.history), force=True)

    def window(self, n):
        """The host sends only its last n user turns from now on, under the same session, to a new engine."""
        users = [i for i, m in enumerate(self.history) if m['role'] == 'user']
        self.history = self.history[users[-n]:]
        self.engine = None
        gc.collect()
        self.engine = self.agent()

    def file(self):
        return json.loads(self.engine._pin_file().read_text())


def test_a_gateway_undo_drops_the_ruling_in_the_new_agent():
    gw = Gateway('gw-drop-537')
    for text in ('start the importer', RULING, 'now the parser'):
        gw.turn(text)
    data = gw.file()
    assert data['ruling'] == [RULING] and all(re.fullmatch('[0-9a-f]{16}', v) for v in data['reach'].values())
    assert data['leads'] and all(re.fullmatch('[0-9a-f]{16}', v) for v in data['leads'])
    assert data['leads'][-1] in data['reach'].values()  # the file keeps the leads up to the last pin's, no further
    gw.undo(2)
    out = gw.turn('next step')
    assert gw.file()['ruling'] == [] and gw.file()['dropped']
    assert out is None or RULING not in hf.message_text(out[0])
    assert RULING not in gw.engine._pin_file().read_text()


@pytest.mark.parametrize('n', [2, 3])
def test_a_gateway_undo_that_also_takes_earlier_turns_drops_the_ruling(n):
    """/undo 3 takes the turn before the ruling's as well, so the transcript holds only part of the ruling's lead.
    It ends where the transcript the engine noted had a lead, before the ruling's, so the ruling goes as for /undo 2."""
    gw = Gateway('gw-deep-537')
    for text in ('a first task', 'start the importer', RULING, 'now the parser'):
        gw.turn(text)
    gw.undo(n)
    gw.turn('next step')
    assert gw.file()['ruling'] == [] and gw.file()['dropped']


@pytest.mark.parametrize('turns, kept', [
    (('continue', RULING, 'continue', 'work on the parser'), 2),
    (('continue', RULING, 'continue', 'parser', 'tests'), 3),
    (('yes', 'continue', RULING, 'yes', 'continue', 'parser'), 3),
])
def test_a_transcript_cut_short_that_opens_like_the_lead_keeps_the_ruling(turns, kept):
    """The host sends a new engine only its last turns, and they open with the same words as the ruling's lead. The
    transcript then goes on past where that lead ended, so it is no undo, and the ruling stays."""
    gw = Gateway('gw-window-537')
    for text in turns:
        gw.turn(text)
    gw.window(kept)
    gw.turn('next step')
    assert gw.file()['ruling'] == [RULING] and not gw.file().get('dropped')


def test_a_gateway_undo_of_a_later_turn_keeps_the_ruling():
    gw = Gateway('gw-keep-537')
    for text in ('start the importer', RULING, 'now the parser'):
        gw.turn(text)
    gw.undo(1)
    gw.turn('next step')
    assert gw.file()['ruling'] == [RULING]


def test_an_undo_the_same_engine_runs_past_is_not_settled_by_a_later_engine():
    """Not covered (D-041, 2026-10-30 note). The host cuts its transcript, reports no undo and keeps the engine.
    While that engine runs the session, only a notice drops pins. Its turn after the undo leaves a transcript that
    goes on past the noted lead, the same as a window that only opens like it, so a later engine keeps the ruling."""
    gw = Gateway('gw-same-537')
    for text in ('start the importer', RULING, 'now the parser'):
        gw.turn(text)
    users = [i for i, m in enumerate(gw.history) if m['role'] == 'user']
    gw.history = gw.history[:users[-2]]  # the same cut as gw.undo(2), but the engine stays
    gw.turn('more work')
    assert gw.file()['ruling'] == [RULING]
    gw.engine = None
    gc.collect()
    gw.engine = gw.agent()
    gw.turn('next step')
    assert gw.file()['ruling'] == [RULING] and not gw.file().get('dropped')


@pytest.mark.parametrize('turns, listed, again, kept', [
    (('continue', RULING, 'x'), False, 'continue', [RULING]),  # not covered: the row left is taken for the message
    (('continue', RULING, 'x'), True, 'continue', []),  # the message in the transcript, as the gateway sends it
    (('continue', RULING, 'x'), False, 'next step', []),  # a message that repeats no row
    (('start', 'continue', RULING, 'x'), False, 'continue', []),  # a user row before the repeated one
])
def test_a_turn_message_left_out_that_repeats_the_last_row_left(turns, listed, again, kept):
    """Not covered in the first case (D-041, 2026-10-30 note). A host may leave the turn's message out of
    conversation_messages. The engine takes the last user row for the turn's message when the two read the same.
    With no user or summary row before that row, nothing comes before the turn's own message, so the ruling stays."""
    gw = Gateway('gw-left-out-537')
    for text in turns:
        gw.turn(text)
    gw.undo(2)
    gw.turn(again, listed=listed)
    assert gw.file()['ruling'] == kept


@pytest.mark.parametrize('turns, listed, again, kept', [
    (('continue', RULING, 'continue', 'go on'), False, 'go on', []),  # not covered: only `continue` is checked
    (('continue', RULING, 'continue', 'go on'), True, 'go on', [RULING]),  # every message in the transcript
    (('continue', RULING, 'continue', 'go on'), False, 'next step', [RULING]),  # a message that repeats no row
    (('start', RULING, 'continue', 'go on'), False, 'go on', [RULING]),  # the row checked is not the session's first
])
def test_a_turn_message_left_out_that_repeats_the_last_row_of_a_window(turns, listed, again, kept):
    """Not covered in the first case (D-041, 2026-10-30 note). A window of the last two turns leaves `continue` and
    `go on`, which do not read like the session's first rows. A host that leaves every turn's message out has the
    engine take the window's `go on` row for a next turn's message of `go on`, so it checks only the `continue`
    before it. That reads like the session's first row, so the window looks like an undo, and the ruling after that
    row goes. `listed` holds for every turn, as such a host leaves out each message, the ruling's too."""
    gw = Gateway('gw-left-out-window-537')
    for text in turns:
        gw.turn(text, listed=listed)
    gw.window(2)
    gw.turn(again, listed=listed)
    assert gw.file()['ruling'] == kept


@pytest.mark.parametrize('turns, n, kept', [
    (('start', RULING), 1, [RULING]),  # not covered: no transcript held the ruling's message, so it has no lead
    (('start', RULING, 'x'), 2, []),  # the turn after the ruling's held it and noted its lead
])
def test_a_host_that_leaves_every_turn_message_out_notes_a_lead_one_turn_late(turns, n, kept):
    """Not covered in the first case (D-041, 2026-10-30 note). Only a transcript that holds a pin's message notes
    the pin's lead, so a pin first said in the last turn before the undo has none and stays."""
    gw = Gateway('gw-all-out-537')
    for text in turns:
        gw.turn(text, listed=False)
    gw.undo(n)
    gw.turn('go', listed=False)
    assert gw.file()['ruling'] == kept


@pytest.mark.parametrize('again', [RULING, f'As I said: {RULING}'])
def test_a_ruling_said_again_in_the_new_turn_stays(again):
    gw = Gateway('gw-again-537')
    for text in ('start the importer', RULING):
        gw.turn(text)
    gw.undo(1)
    gw.turn(again)
    assert RULING in gw.file()['ruling'] and not gw.file().get('dropped')


def test_a_notice_no_engine_could_place_is_settled_by_the_next_turn():
    """An undo heard before any engine saw a turn of the session (after a restart, say) finds no window, so it
    records nothing. The next turn, the new engine's first, drops the pin all the same."""
    gw = Gateway('gw-notice-537')
    for text in ('start the importer', RULING):
        gw.turn(text)
    gw.undo(1)
    host_undo.report('gw-notice-537')
    assert host_undo.pending('gw-notice-537') is None
    gw.turn('next step')
    assert gw.file()['ruling'] == []


def test_a_gateway_undo_after_a_compaction_drops_the_tail_ruling_and_keeps_the_folded_one():
    gw = Gateway('gw-compact-537', inner=Paraphrase())
    for text in ('start the importer', NOTE, 'now the parser'):
        gw.turn(text)
    gw.compress()
    for text in (RULING, 'and the writer'):
        gw.turn(text)
    gw.undo(2)
    gw.turn('next step')
    assert gw.file()['ruling'] == [NOTE]


class Headless:
    """A host summary that keeps no head row: the summary row opens the transcript."""

    def compress(self, messages, **kwargs):
        summary = {'role': 'user', '_compressed_summary': True,
                   'content': f'[CONTEXT COMPACTION] {len(messages) - 1} earlier rows.\n\n' + END_LINE}
        return [summary, messages[-1]]


@pytest.mark.parametrize('inner, again', [(Paraphrase(), f'As I said: {RULING}'), (Headless(), RULING)])
def test_a_folded_ruling_said_again_stays_after_an_undo_of_the_repeat(inner, again):
    """A compaction folds the ruling, and the principal says it again later. The pin's lead moves to that later
    message, so the summary row now comes before the lead. A gateway undo of the later turn leaves the summary row,
    which still carries the ruling, so the pin stays. Only a pin said in the undone turn alone goes."""
    gw = Gateway('gw-again-folded-537', inner=inner)
    for text in ('start the importer', RULING, 'now the parser'):
        gw.turn(text)
    gw.compress()
    for text in ('and the writer', again):
        gw.turn(text)
    gw.undo(1)
    gw.turn('next step')
    assert gw.file()['ruling'] == [RULING]


def test_a_summary_row_after_the_lead_keeps_the_ruling():
    """A compaction may have folded the ruling's message into a summary row after its lead, even one that carries
    none of this engine's blocks (a stock summary), so the pin stays."""
    sid = 'gw-stock-537'
    seed(sid)
    stock = {'role': 'user', '_compressed_summary': True, 'content': '[CONTEXT COMPACTION] notes\n\n' + END_LINE}
    after = [SYS, mine('begin the importer'), stock, mine('continue')]
    fresh = ExoContextEngine(write_handoffs=False)
    fresh.select_context(after, session_id=sid, conversation_messages=after, incoming_message=after[-1])
    assert fresh._load_pins(sid)['ruling'] == [RULING]


@pytest.mark.parametrize('tail', [
    [SYS, mine('continue')],
    [SYS, {'role': 'assistant', 'content': 'ok'}, mine('continue')],
    [SYS, mine('now the parser'), {'role': 'assistant', 'content': 'ok'}, mine('continue')],
    [SYS, mine('begin the importer')],  # the turn's own message is no row before it
])
def test_a_transcript_cut_short_keeps_the_ruling_on_a_hosts_turn(tail):
    sid = 'gw-short-537'
    seed(sid)
    fresh = ExoContextEngine(write_handoffs=False)
    fresh.select_context(tail, session_id=sid, conversation_messages=tail, incoming_message=tail[-1])
    assert fresh._load_pins(sid)['ruling'] == [RULING]


def test_a_ruling_in_the_first_message_has_no_lead_and_stays():
    """Nothing comes before the session's first message, so no transcript can show it reaches back there: an undo
    of every turn looks like a transcript cut short to the turn's own message. Absence alone drops nothing."""
    gw = Gateway('gw-first-537')
    for text in (RULING, 'now the parser'):
        gw.turn(text)
    gw.undo(2)
    gw.turn('start again')
    assert gw.file()['ruling'] == [RULING]


def test_a_request_with_no_turn_message_reconciles_nothing():
    """The core sends no incoming_message, and it reports every undo itself (D-041), so its requests drop nothing
    here."""
    sid = 'gw-core-537'
    seed(sid)
    after = [SYS, mine('begin the importer'), {'role': 'assistant', 'content': 'ok'}, mine('continue')]
    fresh = ExoContextEngine(write_handoffs=False)
    fresh.select_context(after, session_id=sid, conversation_messages=after)
    assert fresh._load_pins(sid)['ruling'] == [RULING]


def test_a_request_with_no_turn_message_notes_no_lead():
    """Only a request that can reconcile notes leads, so the core's requests do not pay for them."""
    engine = ExoContextEngine(write_handoffs=False)
    engine.on_session_start('gw-core-lead-537')
    before = [SYS, mine('begin the importer'), {'role': 'assistant', 'content': 'ok'}, mine(RULING)]
    engine.select_context(before, session_id='gw-core-lead-537', conversation_messages=before)
    data = json.loads(engine._pin_file().read_text())
    assert data['ruling'] == [RULING] and 'reach' not in data and 'leads' not in data
