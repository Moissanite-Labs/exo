"""Bounded summary adoption: real builders, synthetic storage, no provider calls."""
import asyncio

from exo.core.context import AgentContext, SummaryNode
from exo.core.models.anthropic import AnthropicProvider
from exo.core.sessions import SessionStore
from exo.core.types import Message, ToolSpec


def setup(kind='', budget=6000, bulky=False):
    store = SessionStore()
    sid = store.create_session(agent='synthetic', kind=kind).id
    for i in range(32):
        text = 'q' * 500 if bulky and 8 <= i < 16 else (f'question {i}' if i % 2 == 0 else f'answer {i}')
        store.append_message(sid, Message.user(text) if i % 2 == 0 else Message.assistant(text))
    ctx = AgentContext(store, None, view_budget=budget)
    node = SummaryNode('leaf', 'agent:synthetic', 0, 0, 1, 8, text='first summary', status='done')
    store.save_summary(node)
    return store, sid, ctx, node


def test_ordinary_round_keeps_actual_wire_prefix_and_new_turn_adopts_summary():
    async def go():
        store, sid, ctx, node = setup()
        p = AnthropicProvider()
        tools = [ToolSpec(name='synthetic', description='No call made')]
        async def body(turn):
            return p.build_body(await ctx.build(sid, 'SYS', settle=False, turn_id=turn), tools)
        first = await body('turn1')
        node.text = 'new background summary'; store.save_summary(node)
        assert await body('turn1') == first
        store.append_message(sid, Message.user('new live information'))
        same = await body('turn1')
        assert same['system'] == first['system']
        assert 'new live information' in str(same['messages'])
        assert (await body('turn2'))['system'] != first['system']
    asyncio.run(go())


def test_max_two_leaves_and_token_growth_adopt_latest_view():
    async def go():
        for count, size in [(16, 1), (1, 30000)]:
            store, sid, ctx, node = setup()
            await ctx.build(sid, 'SYS', settle=False, turn_id='turn')
            node.text = 'new summary'; store.save_summary(node)
            for _ in range(count):
                store.append_message(sid, Message.user('x' * size))
            result = await ctx.build(sid, 'SYS', settle=False, turn_id='turn')
            assert 'new summary' in result[0].text()
            assert 'x' * size in ''.join(m.text() for m in result)
    asyncio.run(go())


def test_retained_frontier_pressure_adopts_coherent_latest_summary_and_tail():
    async def go():
        store, sid, ctx, node = setup(budget=100, bulky=True)
        await ctx.build(sid, 'SYS', settle=False, turn_id='turn')
        store.save_summary(SummaryNode('leaf2', 'agent:synthetic', 0, 1, 9, 16,
                                       text='second short summary', status='done'))
        out = await ctx.build(sid, 'SYS', settle=False, turn_id='turn')
        assert 'second short summary' in out[0].text()
        assert all('q' * 500 not in m.text() for m in out)
        assert any('question 30' in m.text() for m in out)
    asyncio.run(go())


def test_private_contexts_and_inspections_are_not_frozen():
    async def go():
        for kind, turn in [('private', 'turn'), ('', '')]:
            store, sid, ctx, node = setup(kind)
            first = await ctx.build(sid, 'SYS', settle=False, turn_id=turn)
            node.text = 'replacement'; store.save_summary(node)
            second = await ctx.build(sid, 'SYS', settle=False, turn_id=turn)
            assert first[0].text() != second[0].text()
            assert sid not in ctx._turn_views
    asyncio.run(go())


def test_explicit_invalidation_and_undo_adopt_latest_view():
    async def go():
        for invalidate in ('steer', 'undo'):
            store, sid, ctx, node = setup()
            await ctx.build(sid, 'SYS', settle=False, turn_id='turn')
            node.text = 'replacement'; store.save_summary(node)
            if invalidate == 'steer':
                ctx.invalidate_turn_view(sid)
            else:
                store.truncate(sid, 30)
            out = await ctx.build(sid, 'SYS', settle=False, turn_id='turn')
            assert 'replacement' in out[0].text()
    asyncio.run(go())


def test_recall_missing_opening_anchor_does_not_move_to_later_user():
    async def go():
        store, sid, ctx, _ = setup()
        out = await ctx.build(sid, 'SYS', settle=False, recall='recalled', turn_id='turn', recall_anchor_id='gone')
        assert out[1].meta.get('author') == 'system'
        assert out[1].text().startswith('# Recalled memory')
    asyncio.run(go())


def test_background_capture_compression_and_other_thread_notes_continue():
    class Summarizer:
        async def summarize(self, content, **kw):
            return 'compressed synthetic background'

    async def go():
        store, sid, ctx, _ = setup()
        captured = []
        ctx.compressor = Summarizer()
        ctx.min_compact_tokens = 0
        ctx.on_capture = lambda session, messages: captured.append(len(messages)) or True
        first = await ctx.build(sid, 'SYS', settle=False, turn_id='turn')
        assert await ctx.settle(sid, timeout=2, lane=None)
        assert captured and any(n.status == 'done' for n in store.summaries('agent:synthetic'))
        other = store.create_session(agent='synthetic').id
        store.append_message(other, Message.user('new other-thread evidence'))
        out = await ctx.build(sid, 'SYS', settle=False, turn_id='turn')
        assert out[0].text() == first[0].text()
        assert 'new other-thread evidence' in ''.join(m.text() for m in out)
        await ctx.compact(sid, 'SYS', timeout=2)
        assert sid not in ctx._turn_views
    asyncio.run(go())


def test_configured_large_leaves_do_not_extend_sixteen_message_adoption_bound():
    async def go():
        store, sid, ctx, node = setup()
        ctx.leaf_size = 64
        await ctx.build(sid, 'SYS', settle=False, turn_id='turn')
        node.text = 'new summary'; store.save_summary(node)
        for _ in range(16):
            store.append_message(sid, Message.user('small fresh message'))
        out = await ctx.build(sid, 'SYS', settle=False, turn_id='turn')
        assert 'new summary' in out[0].text()
        fork = store.fork(sid)
        node.text = 'latest fork-visible summary'; store.save_summary(node)
        fork_out = await ctx.build(fork.id, 'SYS', settle=False, turn_id='turn')
        assert 'latest fork-visible summary' in fork_out[0].text()
    asyncio.run(go())


def test_production_adapter_forwards_recall_anchor_and_steer_invalidation():
    from exo.plugin_context import ContextAdapter

    async def go():
        store, sid, ctx, node = setup()
        adapter = ContextAdapter(ctx)
        first = await adapter.build(sid, 'SYS', settle=False, turn_id='turn',
                                    recall='synthetic recall', recall_anchor_id='missing-opening')
        assert first[1].meta.get('author') == 'system'
        node.text = 'latest summary'; store.save_summary(node)
        assert (await adapter.build(sid, 'SYS', settle=False, turn_id='turn'))[0].text() == first[0].text()
        adapter.invalidate_turn_view(sid)
        assert 'latest summary' in (await adapter.build(sid, 'SYS', settle=False, turn_id='turn'))[0].text()
        adapter.rewound(sid)
        assert sid not in ctx._turn_views
    asyncio.run(go())


def test_production_adapter_preserves_legacy_tree_build_signature():
    from exo.plugin_context import ContextAdapter
    from exo.core.context import TreeContext

    async def go():
        store, sid, _, _ = setup()
        adapter = ContextAdapter(TreeContext(store, None))
        out = await adapter.build(sid, 'SYS', recall='synthetic recall', recall_anchor_id='ignored')
        assert 'synthetic recall' in out[0].text()
        assert adapter.invalidate_turn_view(sid) is None
    asyncio.run(go())
