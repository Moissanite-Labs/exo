"""How `exo chat` turns a typed reply into an approval decision (cli_chat._wait_turn).

An option name as shown or a/y/yes for allow; anything else denies. allow_always, like Always in the TUI, needs
a second deliberate act: Enter confirms it, any other reply cancels it and asks again.
"""

import asyncio

from exo import cli_chat
from exo.protocol import schema as s
from exo.server.app import Dispatcher
from exo.server.echo import EchoEngine
from exo.server.engine import Hub

ALL = ["allow", "deny", "allow_always"]
CONFIRM = "allow always? enter confirms, anything else cancels> "


def answer(replies, options=ALL):
    """Run one approval through _wait_turn. Returns (decisions sent, prompts shown, return value)."""

    async def main():
        done, q, sent, prompts = asyncio.Event(), asyncio.Queue(), [], []
        replies_left = list(replies)

        class Client:
            async def send(self, body):
                sent.append(body)
                done.set()

        async def read_line(prompt):
            prompts.append(prompt)
            return replies_left.pop(0) if replies_left else None

        await q.put(s.ApprovalRequested(request_id="r1", summary="x", options=list(options)))
        ok = await cli_chat._wait_turn(Client(), done, q, read_line)
        assert all(b.request_id == "r1" for b in sent)
        return [b.decision for b in sent], prompts, ok

    return asyncio.run(asyncio.wait_for(main(), 5))


def test_allow_always_then_enter_sends_a_standing_grant():
    assert answer(["allow_always", ""]) == (["allow_always"], ["allow/deny/allow_always> ", CONFIRM], True)
    assert answer(["  ALLOW_ALWAYS ", "   "])[0] == ["allow_always"]


def test_allow_always_without_the_confirm_sends_nothing():
    assert answer(["allow_always"]) == ([], ["allow/deny/allow_always> ", CONFIRM], False)  # EOF at the confirm
    assert answer([]) == ([], ["allow/deny/allow_always> "], False)  # EOF at the options


def test_any_other_reply_cancels_always_and_asks_again():
    sent, prompts, ok = answer(["allow_always", "no", "deny"])
    assert (sent, ok) == (["deny"], True)
    assert prompts == ["allow/deny/allow_always> ", CONFIRM, "allow/deny/allow_always> "]
    assert answer(["allow_always", "y", "a"])[0] == ["allow"]  # y at the confirm is not Enter
    assert answer(["allow_always", "allow_always", "allow_always", ""])[0] == ["allow_always"]


def test_allow_always_not_offered_denies():
    assert answer(["allow_always"], ["allow", "deny"]) == (["deny"], ["allow/deny> "], True)


def test_option_names_and_shorthands_in_any_case():
    for reply in ("a", "y", "yes", "allow", "Allow", " ALLOW ", "YES"):
        assert answer([reply])[0] == ["allow"], reply
    for reply in ("d", "n", "no", "deny", "DENY"):
        assert answer([reply])[0] == ["deny"], reply


def test_words_that_only_start_with_a_or_y_deny():
    for reply in ("abort", "absolutely not", "yeah no", "always no", "ya", "allow it", "allow_always please", ""):
        assert answer([reply])[0] == ["deny"], reply


def test_allow_shorthands_deny_when_allow_is_not_offered():
    for reply in ("a", "y", "yes", "allow"):
        assert answer([reply], ["deny", "allow_always"])[0] == ["deny"], reply


def test_chat_loop_sends_a_confirmed_allow_always():
    class Client:
        def __init__(self, d):
            self.d, self.sent = d, []

        async def send(self, body):
            self.sent.append(body)
            return await self.d.run(s.Command.of(body))

        async def call(self, body):
            r = await self.send(body)
            assert r.ok, r.error
            return r.result

        async def events(self, since=0, session=None, reconnect=False):
            async for ev in self.d.hub.subscribe(since, session):
                yield ev

    async def main():
        client, inputs, prompts = Client(Dispatcher(EchoEngine(), Hub())), ["approve: go", "allow_always", "", "/quit"], []

        async def read_line(prompt):
            await asyncio.sleep(0.01)
            prompts.append(prompt)
            return inputs.pop(0) if inputs else None

        assert await cli_chat.chat(client, read_line, lambda _: None, title="t") == 0
        return [b.decision for b in client.sent if isinstance(b, s.ApprovalRespond)], prompts

    decisions, prompts = asyncio.run(asyncio.wait_for(main(), 10))
    assert decisions == ["allow_always"]
    assert prompts == ["> ", "allow/deny/allow_always> ", CONFIRM, "> "]
