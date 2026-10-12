"""Small widgets: transcript blocks, composer, approval card, palette, edit screen, panel view."""

from __future__ import annotations

from typing import Any

from rich.markdown import Markdown
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from exo_tui.panels import choice_options, render_panel


class Block(Static):
    """One transcript entry. ``text`` is the truth; ``flush`` paints it."""

    def __init__(self, text: str = "", kind: str = "assistant") -> None:
        super().__init__("", classes=f"block {kind}")
        self.text = text
        self.kind = kind

    def flush(self) -> None:
        if self.kind == "assistant":
            self.update(Markdown(self.text))
        elif self.kind == "user":
            self.update(Text("> " + self.text, style="bold"))
        elif self.kind == "error":
            self.update(Text(self.text, style="red"))
        elif self.kind == "warn":
            self.update(Text(self.text, style="yellow"))
        else:
            self.update(Text(self.text, style="dim"))

    def on_mount(self) -> None:
        self.flush()


class ToolBlock(Static):
    """A tool call. Collapsed to one line; ctrl+o expands the output."""

    def __init__(self, call_id: str, name: str, args: Any) -> None:
        super().__init__("", classes="block tool")
        self.call_id, self.tool_name, self.args = call_id, name, args
        self.output = ""
        self.status = "running"
        self.expanded = False

    def _summary(self) -> str:
        a = self.args
        if isinstance(a, dict) and a:
            a = a.get("command") or a.get("path") or next(iter(a.values()))
        return str(a or "").splitlines()[0][:80] if a else ""

    def flush(self) -> None:
        mark = {"running": "~", "done": "ok", "error": "err"}[self.status]
        head = Text(f"{'v' if self.expanded else '>'} {self.tool_name} {self._summary()}  [{mark}]", style="dim")
        if self.expanded and self.output:
            head.append("\n" + self.output.rstrip(), style="")
        elif self.output:
            n = len(self.output.splitlines())
            head.append(f"  ({n} line{'s' if n != 1 else ''}, ctrl+o)", style="dim italic")
        self.update(head)

    def on_mount(self) -> None:
        self.flush()


class Composer(TextArea):
    """Enter sends, shift+enter (or ctrl+j) adds a newline."""

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(self) -> None:
        super().__init__("", id="composer", soft_wrap=True, show_line_numbers=False, tab_behavior="focus")

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            text = self.text
            self.text = ""
            self.post_message(self.Submitted(text))
        elif event.key in ("shift+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            self.insert("\n")


class ApprovalCard(Vertical, can_focus=True):
    """Inline approval: o once, a always, d deny with a note."""

    class Answered(Message):
        def __init__(self, request_id: str, decision: str, note: str = "") -> None:
            super().__init__()
            self.request_id, self.decision, self.note = request_id, decision, note

    def __init__(self, req: Any) -> None:
        super().__init__(id="approval")
        self.req = req
        self.noting = False

    def compose(self) -> ComposeResult:
        r = self.req
        text = Text()
        text.append(f"approve ({r.risk})  ", style="bold yellow")
        text.append(r.summary + "\n")
        if r.call is not None:
            text.append(f"{r.call.name} {r.call.arguments}\n", style="dim")
        text.append("o once   a always   d deny", style="bold")
        yield Static(text)
        yield Input(placeholder="why deny? (enter to send, empty is fine)", id="deny-note")

    def on_mount(self) -> None:
        self.query_one("#deny-note").display = False
        self.focus()

    def _answer(self, decision: str, note: str = "") -> None:
        self.post_message(self.Answered(self.req.request_id, decision, note))

    def on_key(self, event: events.Key) -> None:
        if self.noting:
            return
        k = event.key
        opts = self.req.options
        if k == "o" and "allow" in opts:
            self._answer("allow")
        elif k == "a" and "allow_always" in opts:
            self._answer("allow_always")
        elif k == "d":
            self.noting = True
            box = self.query_one("#deny-note", Input)
            box.display = True
            box.focus()
        elif k == "escape":
            self._answer("deny")
        else:
            return
        event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._answer("deny", event.value.strip())


def fuzzy(query: str, label: str) -> int | None:
    """Subsequence score, lower is better; None when no match."""
    q, t = query.lower(), label.lower()
    if not q:
        return 0
    pos, score = -1, 0
    for ch in q:
        nxt = t.find(ch, pos + 1)
        if nxt < 0:
            return None
        score += nxt - pos - 1 if pos >= 0 else nxt
        pos = nxt
    return score


class Palette(ModalScreen):
    """Fuzzy list. Dismisses with the chosen item's value, or None."""

    BINDINGS = [Binding("escape", "close", "close"), Binding("down", "move(1)", show=False),
                Binding("up", "move(-1)", show=False)]

    def __init__(self, items: list[tuple[str, Any]], placeholder: str = "type to search") -> None:
        super().__init__()
        self.items = items
        self.placeholder = placeholder
        self.shown: list[tuple[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-box"):
            yield Input(placeholder=self.placeholder, id="palette-input")
            yield OptionList(id="palette-list")

    def on_mount(self) -> None:
        self._filter("")
        self.query_one(Input).focus()

    def _filter(self, q: str) -> None:
        scored = [(s, i, it) for i, it in enumerate(self.items) if (s := fuzzy(q, it[0])) is not None]
        scored.sort(key=lambda x: (x[0], x[1]))
        self.shown = [it for _, _, it in scored]
        lst = self.query_one(OptionList)
        lst.clear_options()
        lst.add_options([Option(label) for label, _ in self.shown])
        if self.shown:
            lst.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        self._filter(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        lst = self.query_one(OptionList)
        i = lst.highlighted
        self.dismiss(self.shown[i][1] if i is not None and i < len(self.shown) else None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(self.shown[event.option_index][1])

    def action_move(self, d: int) -> None:
        lst = self.query_one(OptionList)
        lst.action_cursor_down() if d > 0 else lst.action_cursor_up()

    def action_close(self) -> None:
        self.dismiss(None)


class EditScreen(ModalScreen):
    """Edit the last answer. ctrl+s sends the edit, escape cancels."""

    BINDINGS = [Binding("ctrl+s", "save", "send edit"), Binding("escape", "cancel", "cancel")]

    def __init__(self, text: str) -> None:
        super().__init__()
        self.initial = text

    def compose(self) -> ComposeResult:
        with Vertical(id="edit-box"):
            yield Static("Edit the answer. ctrl+s sends it as your edit, esc cancels.")
            yield TextArea(self.initial, id="edit-area", soft_wrap=True)

    def on_mount(self) -> None:
        self.query_one("#edit-area").focus()

    def action_save(self) -> None:
        self.dismiss(self.query_one("#edit-area", TextArea).text)

    def action_cancel(self) -> None:
        self.dismiss(None)


class PanelView(Vertical):
    """Right-hand area for ui.panel. Shows one panel; choice panels get a selectable list."""

    class Chose(Message):
        def __init__(self, panel_id: str, choice: str) -> None:
            super().__init__()
            self.panel_id, self.choice = panel_id, choice

    def __init__(self) -> None:
        super().__init__(id="panel")
        self.panel_id = ""
        self.kind = ""
        self.options: list[tuple[str, str]] = []
        self.display = False

    def compose(self) -> ComposeResult:
        yield Static("", id="panel-title")
        with VerticalScroll(id="panel-scroll"):
            yield Static("", id="panel-body")
        yield OptionList(id="panel-choices")

    def show(self, panel_id: str, kind: str, title: str, text: str, data: dict[str, Any]) -> None:
        self.panel_id, self.kind = panel_id, kind
        self.query_one("#panel-title", Static).update(Text(f"{title or kind}  (esc closes)", style="bold"))
        self.query_one("#panel-body", Static).update(render_panel(kind, text, data))
        lst = self.query_one("#panel-choices", OptionList)
        self.options = choice_options(data) if kind == "choice" else []
        lst.clear_options()
        lst.display = bool(self.options)
        if self.options:
            lst.add_options([Option(label) for _, label in self.options])
            lst.highlighted = 0
        self.display = True

    def hide(self) -> None:
        self.display = False
        self.panel_id = ""

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if 0 <= event.option_index < len(self.options):
            self.post_message(self.Chose(self.panel_id, self.options[event.option_index][0]))
