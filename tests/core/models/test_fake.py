from exo.core.models import FakeProvider
from exo.core.models.fake import rate_limited, tool_turn
from exo.core.types import ContentPart, Message, Provider

from .conftest import collect


def test_fake_streams_scripted_turns_and_records_calls():
    msg = Message(role="assistant", content=[ContentPart(type="reasoning", text="hm"), ContentPart.of_text("hello")])
    fake = FakeProvider([msg, tool_turn("ls"), rate_limited(5)], chunk=2)
    assert isinstance(fake, Provider)
    ev = collect(fake.stream([Message.user("a")], [], model="x"))
    assert [e.text for e in ev if e.kind == "text"] == ["he", "ll", "o"]
    assert ev[-1].kind == "done" and ev[-1].message is msg
    ev = collect(fake.stream([], []))
    assert ev[-1].stop_reason == "tool_use" and ev[0].tool_call.name == "ls"
    ev = collect(fake.stream([], []))
    assert ev[0].status == 429 and ev[0].retry_after == 5
    assert collect(fake.stream([], []))[0].error.endswith("exhausted")
    assert fake.calls[0]["opts"] == {"model": "x"}


def test_fake_repeat():
    fake = FakeProvider(["again"], repeat=True)
    for _ in range(3):
        assert collect(fake.stream([], []))[-1].message.text() == "again"
