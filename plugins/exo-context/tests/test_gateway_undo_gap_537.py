"""Gateway undo without a provider notification (#537): the first host turn of a new engine drops a pin the host's
transcript no longer holds, by the lead noted with the pin, and the controls keep pins after a transcript cut
short, a compaction and an earlier undo."""
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
    engine.select_context(before, session_id=sid, conversation_messages=before)
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

    def turn(self, text):
        self.history.append(mine(text))
        out = self.engine.select_context([SYS, *self.history], conversation_messages=list(self.history),
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

    def file(self):
        return json.loads(self.engine._pin_file().read_text())


def test_a_gateway_undo_drops_the_ruling_in_the_new_agent():
    gw = Gateway('gw-drop-537')
    for text in ('start the importer', RULING, 'now the parser'):
        gw.turn(text)
    data = gw.file()
    assert data['ruling'] == [RULING] and all(re.fullmatch('[0-9a-f]{16}', v) for v in data['reach'].values())
    gw.undo(2)
    out = gw.turn('next step')
    assert gw.file()['ruling'] == [] and gw.file()['dropped']
    assert out is None or RULING not in hf.message_text(out[0])
    assert RULING not in gw.engine._pin_file().read_text()


def test_a_gateway_undo_of_a_later_turn_keeps_the_ruling():
    gw = Gateway('gw-keep-537')
    for text in ('start the importer', RULING, 'now the parser'):
        gw.turn(text)
    gw.undo(1)
    gw.turn('next step')
    assert gw.file()['ruling'] == [RULING]


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
