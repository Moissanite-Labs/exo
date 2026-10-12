from exo.core.tools import ToolContext, default_registry
from exo.core.tools.builtin import ui
from exo.core.types import ToolCall
from exo.protocol import schema as s


def setup():
    seen = []
    reg = default_registry()
    ui.register(reg, lambda body, sid: seen.append((body, sid)))
    n = iter(range(100))

    def run(**args):
        return reg.call_sync(ToolCall(id=f"c{next(n)}", name="ui", arguments=args), ToolContext(session="S1"))
    return reg, seen, run


def test_open_update_close():
    reg, seen, run = setup()
    assert reg.get("ui").spec.risk == "low"
    r = run(action="open", kind="diff", title="Change", text="Two lines changed.", data={"a": 1}, reason="show the edit")
    assert not r.is_error
    body, sid = seen[0]
    assert isinstance(body, s.UiPanel) and sid == "S1" and body.action == "open" and body.kind == "diff"
    assert body.panel_id and body.panel_id in r.text() and body.data == {"a": 1} and body.reason == "show the edit"
    assert not run(action="update", panel_id=body.panel_id, text="Three lines changed.").is_error
    assert seen[1][0].panel_id == body.panel_id and seen[1][0].kind == "markdown"
    assert not run(action="close", panel_id=body.panel_id, text="Done.").is_error
    assert [b.action for b, _ in seen] == ["open", "update", "close"]


def test_validation():
    _, seen, run = setup()
    assert run(action="open", kind="pie", text="x").is_error
    assert run(action="open", kind="table").is_error  # text required
    assert run(action="open", text="  ").is_error
    assert run(action="update", text="x").is_error  # needs panel_id
    assert run(action="open", text="x", data="no").is_error
    assert run(action="nope", text="x").is_error
    assert seen == []
