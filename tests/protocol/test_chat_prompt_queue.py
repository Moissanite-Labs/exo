"""Preloaded line-client prompt queues use real core dispatch and fake inference."""
import asyncio

import pytest

from exo.chat_queue import MAX_PROMPTS, MAX_PROMPT_CHARS, PromptQueue
from exo.cli_chat import chat
from exo.core.engine import CoreEngine
from exo.core.models.fake import FakeProvider
from exo.protocol import schema as s
from exo.server.app import Dispatcher


def test_queue_bounds_order_and_snapshot():
    queue = PromptQueue()
    queue.add(" first ")
    queue.add("second")
    assert queue.first() == "first" and queue.render() == "1. first\n2. second"
    queue.accepted()
    assert queue.first() == "second"
    queue.clear()
    assert not queue and queue.render() == "queue is empty"
    for i in range(MAX_PROMPTS):
        queue.add(str(i))
    with pytest.raises(ValueError, match="at most"):
        queue.add("overflow")


@pytest.mark.parametrize("text", ["", " ", "/stop", " /model x", "x" * (MAX_PROMPT_CHARS + 1), None])
def test_queue_rejects_empty_commands_and_oversize(text):
    with pytest.raises(ValueError):
        PromptQueue().add(text)


class Client:
    def __init__(self, engine, *, reject="", kind=None):
        self.dispatcher = Dispatcher(engine, caller="principal:chat")
        self.sent, self.session = [], None
        self.reject, self.kind = reject, kind

    async def send(self, body):
        self.sent.append(body)
        if isinstance(body, s.TurnSend) and body.text == self.reject:
            return s.Response(id="rejected", ok=False, error=s.ErrorInfo(code="fixture", message="rejected"))
        return await self.dispatcher.run(s.Command.of(body))

    async def call(self, body):
        reply = await self.send(body)
        assert reply.ok, reply.error
        if isinstance(body, (s.SessionCreate, s.SessionResume)):
            self.session = reply.result["session"]["id"]
            if self.kind is not None:
                reply.result["session"]["kind"] = self.kind
        return reply.result

    async def events(self, since=0, session=None, reconnect=False):
        async for event in self.dispatcher.hub.subscribe(since, session):
            yield event


def run_chat(tmp_path, inputs, *, reject="", kind=None, provider=None):
    async def run():
        engine = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"]}}, cwd=str(tmp_path), env={})
        engine.loop.provider = provider or FakeProvider(["reply one", "reply two", "reply three"])
        engine.loop.max_retries = 0
        client, output, prompts = Client(engine, reject=reject, kind=kind), [], []
        lines = iter(inputs)

        async def read_line(prompt):
            prompts.append(prompt)
            await asyncio.sleep(0)
            return next(lines, None)

        try:
            assert await asyncio.wait_for(chat(client, read_line, output.append), 3) == 0
            messages = engine.store.messages(client.session)
            return client.sent, "".join(output), prompts, [m.text() for m in messages if m.role == "user"]
        finally:
            await engine.close()
    return asyncio.run(run())


def test_preloaded_fifo_sends_whole_turns_without_extra_input_readers(tmp_path):
    sent, output, prompts, users = run_chat(tmp_path, [
        "/queue add first", "/queue add second", "/queue", "/queue run", "/quit"])
    turns = [b for b in sent if isinstance(b, s.TurnSend)]
    assert [(b.text, b.mode) for b in turns] == [("first", "queue"), ("second", "queue")]
    assert users == ["first", "second"]
    assert "1. first\n2. second" in output and len(prompts) == 5
    assert not any(isinstance(b, s.CommandRun) for b in sent)


def test_rejected_prompt_stays_queued_and_does_not_automatically_retry(tmp_path):
    sent, output, _, users = run_chat(tmp_path, [
        "/queue add rejected", "/queue add later", "/queue run", "/queue", "/quit"], reject="rejected")
    assert [b.text for b in sent if isinstance(b, s.TurnSend)] == ["rejected"]
    assert not users and "1. rejected\n2. later" in output


class BrokenProvider(FakeProvider):
    async def stream(self, *args, **kwargs):
        raise RuntimeError("fixture provider failed")
        yield  # make this an async generator, like a real streaming provider


def test_failed_turn_pauses_before_the_next_prompt(tmp_path):
    sent, output, _, users = run_chat(tmp_path, [
        "/queue add first", "/queue add later", "/queue run", "/queue", "/quit"], provider=BrokenProvider())
    assert [b.text for b in sent if isinstance(b, s.TurnSend)] == ["first"]
    assert users == ["first"] and "queue paused" in output and "1. later" in output


@pytest.mark.parametrize("kind", ["subagent", "scheduled", ""])
def test_queue_does_not_turn_subagent_or_unknown_sessions_into_steering(tmp_path, kind):
    sent, output, _, _ = run_chat(tmp_path, ["/queue add first", "/queue run", "/quit"], kind=kind)
    assert not any(isinstance(b, s.TurnSend) for b in sent)
    assert "queue requires a normal conversation" in output


def test_clear_and_validation_do_not_dispatch_prompts(tmp_path):
    sent, output, _, _ = run_chat(tmp_path, [
        "/queue add /stop", "/queue add first", "/queue clear", "/queue run", "/quit"])
    assert not any(isinstance(b, s.TurnSend) for b in sent)
    assert "run slash commands directly" in output and "queue is empty" in output


def test_normal_prompt_does_not_start_a_preloaded_queue(tmp_path):
    sent, output, _, users = run_chat(tmp_path, ["/queue add later", "now", "/queue", "/quit"])
    assert [(b.text, b.mode) for b in sent if isinstance(b, s.TurnSend)] == [("now", "send")]
    assert users == ["now"] and "1. later" in output


def test_queue_is_not_restored_into_another_client_process(tmp_path):
    run_chat(tmp_path, ["/queue add private intent", "/quit"])
    sent, output, _, _ = run_chat(tmp_path, ["/queue", "/quit"])
    assert "queue is empty" in output and "private intent" not in output
    assert not any(isinstance(b, s.TurnSend) for b in sent)
