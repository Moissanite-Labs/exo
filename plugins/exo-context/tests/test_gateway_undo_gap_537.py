"""Gateway undo without a provider notification: executable gap and unsafe-fix controls (#537)."""
import json

import pytest

from exo import handoff as hf
from exo_context.engine import ExoContextEngine
from exo_context.inner import END_LINE

SYS = {'role': 'system', 'content': 'sys'}
RULING = 'From now on, write every report in British English.'


def mine(text):
    return {'role': 'user', 'author': 'principal', 'principal': True, 'content': text}


def seed(sid):
    engine = ExoContextEngine(write_handoffs=False)
    engine.on_session_start(sid)
    before = [SYS, mine('begin the importer'), {'role': 'assistant', 'content': 'ok'}, mine(RULING)]
    engine.select_context(before, session_id=sid, conversation_messages=before)
    assert engine._load_pins()['ruling'] == [RULING]
    return engine


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="Gateway undo evicts its agent without notifying persisted pin reconciliation (#537)")
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
