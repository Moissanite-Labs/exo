"""Exercise the actual optional host patch before cached listener eviction (#537)."""
import asyncio
import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace

from exo import handoff as hf
from exo import host_undo
from exo_context.engine import ExoContextEngine

from test_gateway_undo_gap_537 import RULING, SYS, mine, seed

ROOT = Path(__file__).resolve().parents[3]


def gateway(tmp_path, engine, result, *, patched=True):
    path = tmp_path / 'gateway/slash_commands_session.py'
    path.parent.mkdir()
    path.write_text((Path(__file__).parent / 'fixtures/gateway_undo_537.txt').read_text())
    if patched:
        subprocess.run(['git', 'apply', str(ROOT / 'patches/0006-gateway-undo-notice.patch')], cwd=tmp_path, check=True)
    namespace = {'MessageEvent': object, 'logger': logging.getLogger(__name__), 't': lambda key, **kw: key}
    exec(compile(path.read_text(), str(path), 'exec'), namespace)
    obj = namespace['GatewayFixture']()
    obj.engine, obj.evicted = engine, False
    sid = engine._x_session_id

    async def get(_):
        return SimpleNamespace(session_id=sid, last_prompt_tokens=10)

    async def rewind(_, n):
        return result

    def evict(_):
        obj.evicted, obj.engine = True, None

    obj.async_session_store = SimpleNamespace(get_or_create_session=get, rewind_session=rewind)
    obj._record_model_friction = lambda *a, **kw: None
    obj._session_key_for_source = lambda source: 'profile-scoped-cache-key'
    obj._evict_cached_agent = evict
    return obj


def event():
    return SimpleNamespace(source='fixture-source', get_command_args=lambda: '')


def test_patch_notifies_before_eviction_and_fresh_engine_drops_removed_pin(tmp_path):
    sid = 'patched-gateway-537'
    obj = gateway(tmp_path, seed(sid), {'turns_undone': 1, 'target_text': RULING, 'rewound_count': 1})
    asyncio.run(obj._handle_undo_command(event()))
    assert obj.evicted and host_undo.pending(sid) is not None
    after = [SYS, mine('begin the importer'), {'role': 'assistant', 'content': 'ok'}, mine('continue after undo')]
    fresh = ExoContextEngine(write_handoffs=False)
    out = fresh.select_context(after, session_id=sid, conversation_messages=after, incoming_message='continue after undo')
    assert RULING not in '\n'.join(hf.message_text(m) for m in (out or after))
    assert fresh._load_pins()['ruling'] == []


def test_no_rewind_does_not_notify_or_evict(tmp_path, monkeypatch):
    obj = gateway(tmp_path, seed('gateway-no-rewind-537'), None)
    monkeypatch.setattr(host_undo, 'report', lambda sid: (_ for _ in ()).throw(AssertionError('not an undo')))
    asyncio.run(obj._handle_undo_command(event()))
    assert not obj.evicted


def test_optional_notification_failure_does_not_prevent_host_undo(tmp_path, monkeypatch):
    obj = gateway(tmp_path, seed('gateway-hook-failure-537'), {'turns_undone': 1, 'target_text': RULING, 'rewound_count': 1})
    monkeypatch.setattr(host_undo, 'report', lambda sid: (_ for _ in ()).throw(RuntimeError('synthetic failure')))
    asyncio.run(obj._handle_undo_command(event()))
    assert obj.evicted


def test_unpatched_fixture_reproduces_the_missing_notice(tmp_path):
    sid = 'unpatched-gateway-537'
    obj = gateway(tmp_path, seed(sid), {'turns_undone': 1, 'target_text': RULING, 'rewound_count': 1}, patched=False)
    asyncio.run(obj._handle_undo_command(event()))
    assert obj.evicted and host_undo.pending(sid) is None
    after = [SYS, mine('begin the importer'), {'role': 'assistant', 'content': 'ok'}, mine('continue after undo')]
    fresh = ExoContextEngine(write_handoffs=False)
    out = fresh.select_context(after, session_id=sid, conversation_messages=after, incoming_message='continue after undo')
    assert RULING in '\n'.join(hf.message_text(m) for m in (out or after))
