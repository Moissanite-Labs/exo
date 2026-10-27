"""Small widgets: transcript blocks, composer, brand bar, status strip, approval card, palette, edit screen, panel view."""

from __future__ import annotations

import re
import time
from typing import Any

from rich.console import Group
from rich.markdown import Markdown
from rich.cells import cell_len
from rich.padding import Padding
from rich.segment import Segment
from rich.style import Style
from rich.table import Table
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from exo_tui.panels import choice_options, render_panel
from exo_tui.theme import (DOT, PROMPT, SPINNER, chips, gauge, header_rule, human_tokens, reduced_motion,
                           rich_style)

IDLE_HINT = "ask anything " + DOT + " /help"
BUSY_HINT = "lands at the next tool round " + DOT + " up pulls a queued one back " + DOT + " ctrl+c stops the turn"


def reveal(widget: Widget) -> None:
    """Fade a panel in on arrival. Motion reports state (something new came in); off under reduced motion."""
    if reduced_motion():
        return
    widget.styles.opacity = 0.0
    widget.styles.animate("opacity", 1.0, duration=0.5, easing="out_cubic")


class Block(Static):
    """One transcript entry. ``text`` is the truth; ``flush`` paints it.

    Kinds: user, assistant, error, warn, note, hint (a tip), memory (a cold one-liner), peer, taste, finding, intro.
    peer, taste, finding and intro (the short orientation) are panels: a coloured left rule (CSS) and a header line
    drawn here."""

    PANELS = {"peer": ("cold", "PEER"), "taste": ("cold", "TASTE"), "finding": ("cold", "FINDING"),
              "report": ("cold", ""), "intro": ("cold", "INTRO")}
    ROW = re.compile(r"^(\s+)(\S.*?)(\s{2,})(\d+)(\s*)(.*)$")  # `  recent tail   29   2 messages`

    def __init__(self, text: str = "", kind: str = "assistant", head: str = "", meta: str = "",
                 steer: bool = False, body: str = "") -> None:
        super().__init__("", classes=f"block {kind}")
        self.text = text
        self.kind = kind
        self.body = body  # what a panel shows; ``text`` stays the full line for search and tests
        self.head = head  # panel header after the kind's label, e.g. "razi . other"
        self.meta = meta  # panel footer line, dim
        self.shown: Any = None
        self.steer = steer  # a user message sent while a turn was running
        self.steer_state = "sending"  # delivered only after confirmation from the core
        self.pending = False  # a steer the core still holds: up or /unsteer pulls it back (D-045)
        self.streaming = False  # text still arriving: paint cheap plain text, Markdown once the block closes

    def paint(self, renderable: Any) -> None:
        self.shown = renderable  # kept so tests and snapshots can read what was drawn
        self.update(renderable)

    def _panel(self) -> Group:
        role, label = self.PANELS[self.kind]
        head = f"{label} {DOT} {self.head}" if label and self.head else (self.head or label)
        lines: list[Text] = [header_rule(head, role, self.size.width or 60)]
        lines.append(self._report() if self.kind == "report" else Text(self.body or self.text, style=rich_style("fg")))
        if self.meta:
            lines.append(Text(self.meta, style=rich_style("fg3")))
        return Group(*lines)

    def _report(self) -> Text:
        """A command report (/context): label and note dim, the number bright, the first line plain."""
        out = Text()
        for i, ln in enumerate(self.text.splitlines()):
            m = self.ROW.match(ln)
            if m:
                out.append(m[1] + m[2] + m[3], style=rich_style("fg2"))
                out.append(m[4], style=rich_style("fg", "bold"))
                out.append(m[5] + m[6], style=rich_style("fg3"))
            else:
                out.append(ln, style=rich_style("fg" if i == 0 else "fg3"))
            out.append("\n")
        out.rstrip()
        return out

    def flush(self) -> None:
        k = self.kind
        if k == "assistant" and self.streaming:
            self.paint(Padding(Text(self.text, style=rich_style("fg2")), (0, 0, 0, 2)))
        elif k == "assistant":
            self.paint(Padding(Markdown(self.text, style=rich_style("fg2")), (0, 0, 0, 2)))
        elif k == "user":
            row = Table.grid(padding=(0, 1))
            lead = Text()
            if self.steer:
                state = "queued" if self.pending else self.steer_state
                role = "accent2" if self.pending else "ok" if state == "delivered" else "fg3"
                lead.append(f"steer {state} ", style=rich_style(role))
            lead.append(PROMPT, style=rich_style("accent", "bold"))
            row.add_column(no_wrap=True)
            row.add_column(ratio=1)
            row.add_row(lead, Text(self.text, style=rich_style("fg")))
            self.paint(row)
        elif k == "error":
            self.paint(Text("✕ " + self.text, style=rich_style("err")))
        elif k in self.PANELS:
            self.paint(self._panel())
        elif k == "hint":  # tutorial tips: dim italic, clearly not the agent's words
            t = Text(style=rich_style("fg3", "italic"))
            body = self.text[5:] if self.text.startswith("tip: ") else self.text
            t.append("tip", style=rich_style("cold", "italic"))
            t.append(f" {DOT} {body}")
            self.paint(t)
        elif k in ("memory", "job"):  # cold system one-liners
            self.paint(Text(self.text, style=rich_style("cold")))
        elif k == "warn":
            self.paint(Text(self.text, style=rich_style("accent2")))
        else:
            self.paint(Text(self.text, style=rich_style("fg3")))

    def on_mount(self) -> None:
        self.flush()
        if self.kind in self.PANELS:
            reveal(self)

    def on_resize(self) -> None:
        if self.kind in self.PANELS:
            self.flush()


class ToolBlock(Static):
    """A tool call: one line with a state glyph, the name, short args and elapsed time.
    Collapsed to one line; ctrl+o expands the output."""

    def __init__(self, call_id: str, name: str, args: Any) -> None:
        super().__init__("", classes="block tool")
        self.call_id, self.tool_name, self.args = call_id, name, args
        self.output = ""
        self.status = "running"
        self.expanded = False
        self.t0 = time.monotonic()
        self.elapsed: float | None = None  # seconds, set when the call finishes; None for replayed history
        self.frame = 0
        self.shown: Any = None  # last thing drawn, for tests and snapshots

    def _summary(self) -> str:
        a = self.args
        if isinstance(a, dict) and a:
            a = a.get("command") or a.get("path") or next(iter(a.values()))
        return str(a or "").splitlines()[0][:80] if a else ""

    def glyph(self) -> Text:
        if self.status == "done":
            return Text("✓", style=rich_style("ok"))
        if self.status == "error":
            return Text("✕", style=rich_style("err"))
        ch = SPINNER[self.frame % len(SPINNER)] if not reduced_motion() else "◇"
        return Text(ch, style=rich_style("accent"))

    def clock(self) -> str:
        if self.elapsed is not None:
            secs: float | None = self.elapsed
        else:
            secs = time.monotonic() - self.t0 if self.status == "running" else None
        if secs is None:
            return ""
        return f"{secs:.1f}s" if secs < 100 else f"{int(secs // 60)}m{int(secs % 60):02d}s"

    def flush(self) -> None:
        left = Text(no_wrap=True, overflow="ellipsis")
        left.append_text(self.glyph())
        left.append(" " + self.tool_name, style=rich_style("fg2", "bold"))
        summ = self._summary()
        if summ:
            left.append("  " + summ, style=rich_style("fg3"))
        hint = Text(no_wrap=True, style=rich_style("fg3", "italic"))
        if self.output and not self.expanded:
            n = len(self.output.splitlines())
            hint.append(f"{n} line{'s' if n != 1 else ''} {DOT} ctrl+o")
        clock = Text(self.clock(), no_wrap=True, style=rich_style("fg3"))
        row = Table.grid(expand=True, padding=(0, 0, 0, 2))  # fixed columns: the clock lines up with or without a hint
        row.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        row.add_column(justify="right", no_wrap=True, width=18)
        row.add_column(justify="right", no_wrap=True, width=6)
        row.add_row(left, hint, clock)
        if self.expanded and self.output:
            body = Text()
            for ln in self.output.rstrip().splitlines():
                body.append("│ ", style=rich_style("line2"))
                body.append(ln + "\n", style=rich_style("fg2"))
            body.rstrip()
            self.shown = Group(row, Padding(body, (0, 0, 0, 2)))
        else:
            self.shown = row
        self.update(self.shown)

    def finish(self, status: str, secs: float | None = None) -> None:
        self.status = status
        self.elapsed = secs if secs else time.monotonic() - self.t0
        self.flush()

    def on_mount(self) -> None:
        self.flush()

    def on_resize(self) -> None:
        self.flush()


FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s")
LISTMARK = re.compile(r"^(\s*)([-*+]|\d+[.)])(\s)")
INLINE = ((re.compile(r"`[^`\n]+`"), ("accent2", "")), (re.compile(r"\*\*[^*\n]+\*\*"), ("fg", "bold")),
          (re.compile(r"(?<![\w*])\*[^*\s][^*\n]*\*(?![\w*])"), ("fg2", "italic")))


class Composer(TextArea):
    """Enter sends, shift+enter (or ctrl+j) adds a newline. In the big editor pane (``pane``, D-046) Enter adds a
    newline and ctrl+s or ctrl+enter sends. The caret is a block in the signal colour (CSS).
    ``key_hook`` (set by the app) sees a key first and returns True when it used it: the completion menu, history
    and ghost text live there. ``ghost`` is dim text drawn after the caret on a one-line input."""

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    BINDINGS = [Binding("ctrl+l", "numbers", "line numbers", show=False), Binding("ctrl+t", "editor", "$EDITOR", show=False)]

    def action_numbers(self) -> None:
        self.app.toggle_numbers()

    def action_editor(self) -> None:
        if self.pane:
            self.app.edit_in_editor()

    def __init__(self) -> None:
        super().__init__("", id="composer", soft_wrap=True, show_line_numbers=False, tab_behavior="focus",
                         placeholder=IDLE_HINT)
        self.cursor_blink = not reduced_motion()
        self.key_hook: Any = None
        self.pane = False  # the big editor is open: Enter is a newline, ctrl+s sends, markdown is lightly coloured
        self.ghost = ""
        self.last_typed = float("-inf")  # monotonic time of the last printable key

    def at_end(self) -> bool:
        return self.cursor_location == self.document.end

    def action_copy(self) -> None:
        """ctrl+c copies a selection; with nothing selected it is the stop key (D-043)."""
        if self.selected_text:
            super().action_copy()
        else:
            self.app.action_ctrl_c()

    async def _on_key(self, event: events.Key) -> None:
        if event.is_printable:
            self.last_typed = time.monotonic()
        if self.key_hook is not None and self.key_hook(event.key):
            event.stop()
            event.prevent_default()
            return
        if (event.key == "enter" and not self.pane) or (self.pane and event.key in ("ctrl+s", "ctrl+enter")):
            event.stop()
            event.prevent_default()
            text = self.text
            if self.pane and not text.strip():
                return  # nothing to send: keep the pane open
            self.text = ""
            self.last_typed = float("-inf")
            self.post_message(self.Submitted(text))
        elif event.key in ("shift+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            self.insert("\n")

    def get_line(self, line_index: int) -> Text:
        line = super().get_line(line_index)
        if self.pane:
            self.markdown(line, line_index)
        return line

    def in_fence(self, upto: int) -> bool:
        """Is line ``upto`` inside a ``` block (a fence line itself counts as outside)?"""
        inside = False
        for i in range(upto):
            if FENCE.match(self.document.get_line(i)):
                inside = not inside
        return inside

    def markdown(self, line: Text, index: int) -> None:
        """Light markdown colour for the pane: headings, list marks, quotes, fences, code, bold and italic."""
        s = line.plain
        if FENCE.match(s):
            line.stylize(rich_style("fg3"))
            return
        if self.in_fence(index):
            line.stylize(rich_style("accent2"))
            return
        if HEADING.match(s):
            line.stylize(rich_style("cold", "bold"))
            return
        if s.lstrip().startswith(">"):
            line.stylize(rich_style("fg3", "italic"))
            return
        if m := LISTMARK.match(s):
            line.stylize(rich_style("accent", "bold"), len(m[1]), len(m[1]) + len(m[2]))
        for rx, (role, extra) in INLINE:
            for m in rx.finditer(s):
                line.stylize(rich_style(role, extra), m.start(), m.end())

    def render_line(self, y: int) -> Strip:
        strip = super().render_line(y)
        if not self.ghost or "\n" in self.text or not self.text or y != 0 or not self.at_end():
            return strip
        x = cell_len(self.text)
        width = strip.cell_length
        if x >= width - 1 or x + len(self.ghost) >= width:  # soft-wrapped or cramped: skip rather than garble
            return strip
        dim = Style.parse(rich_style("fg3"))
        left = strip.crop(0, x)
        cursor = strip.crop(x, x + 1)
        cstyle = cursor._segments[0].style if cursor._segments else None  # noqa: SLF001 (keep the caret colour)
        first = Segment(self.ghost[0], cstyle if self.has_focus and cstyle else dim)
        rest = Segment(self.ghost[1:], dim) if len(self.ghost) > 1 else None
        tail = strip.crop(x + len(self.ghost), width)
        return Strip([*left, first, *([rest] if rest else []), *tail], width)


class CompletionMenu(Static):
    """The dropdown under the composer: hairline above, mono rows, an orange rule on the selected row. Shows at most
    ``ROWS`` rows, scrolled to keep the selection in view; with no rows it can still show one dim hint line."""

    ROWS = 8

    def __init__(self) -> None:
        super().__init__("", id="cmenu")
        self.display = False

    def show(self, rows: list[tuple[str, str, str]], sel: int, hint: str = "", total: int = 0) -> None:
        """rows = (label, args, description). Hides itself when there is nothing to draw."""
        if not rows and not hint:
            self.display = False
            return
        out = Text(no_wrap=True, overflow="ellipsis")
        top = max(0, min(sel - self.ROWS + 1, len(rows) - self.ROWS)) if len(rows) > self.ROWS else 0
        width = max((len(r[0]) for r in rows[top:top + self.ROWS]), default=0)
        for i, (label, args, desc) in enumerate(rows[top:top + self.ROWS], start=top):
            on = i == sel
            out.append("\u258c " if on else "  ", style=rich_style("accent"))
            out.append(label.ljust(width), style=rich_style("fg", "bold") if on else rich_style("fg2"))
            if args:
                out.append("  " + args, style=rich_style("accent2") if on else rich_style("fg3"))
            if desc:
                out.append("  " + desc, style=rich_style("fg2") if on else rich_style("fg3"))
            out.append("\n")
        if hint:
            out.append("  " + hint, style=rich_style("fg3"))
        elif len(rows) > self.ROWS:
            out.append(f"  {len(rows)} matches {DOT} tab completes {DOT} esc closes", style=rich_style("fg3"))
        else:
            out.append("  tab completes " + DOT + " \u2191\u2193 move " + DOT + " esc closes", style=rich_style("fg3"))
        self.update(out)
        self.display = True


class ComposeHeader(Static):
    """The one-line head of the big editor: word and line count, then the keys. Hidden unless the pane is open."""

    def __init__(self) -> None:
        super().__init__("", id="cheader")
        self.shown: Any = None
        self.args: tuple[Any, bool] | None = None

    def show(self, composer: "Composer", numbers: bool = False) -> None:
        self.args = (composer, numbers)
        text = composer.text
        words, lines = len(text.split()), composer.document.line_count
        stats = f"{words} word{'s' if words != 1 else ''} {DOT} {lines} line{'s' if lines != 1 else ''}"
        keys = chips([("ctrl+s", "send"), ("enter", "new line"), ("esc", "close, draft stays"),
                      ("ctrl+l", "numbers " + ("on" if numbers else "off")), ("ctrl+t", "$EDITOR")])
        self.shown = Group(header_rule("compose", "cold", self.size.width or 80, stats), keys)
        self.update(self.shown)

    def on_resize(self) -> None:
        if self.args:
            self.show(*self.args)


class PromptMark(Static):
    """The mark left of the composer: `>`, or `steer >` while a turn runs, `answer >` for a question."""

    def set_state(self, busy: bool, question: bool = False) -> None:
        t = Text(no_wrap=True)
        if question:
            t.append("answer ", style=rich_style("cold"))
        elif busy:
            t.append("steer ", style=rich_style("accent2"))
        t.append(PROMPT, style=rich_style("accent", "bold"))
        self.update(t)


class BrandBar(Widget):
    """`E X O`, the agent, the thread, and a live dot while a turn runs."""

    def __init__(self) -> None:
        super().__init__(id="brand")
        self.agent = ""
        self.thread = ""
        self.live = False
        self.frame = 0

    def set(self, agent: str, thread: str, live: bool) -> None:
        if (agent, thread, live) != (self.agent, self.thread, self.live):
            self.agent, self.thread, self.live = agent, thread, live
            self.refresh()

    def tick(self, frame: int) -> None:
        self.frame = frame
        if self.live:
            self.refresh()

    def render(self) -> Table:
        left = Text(no_wrap=True, overflow="ellipsis")
        left.append("◢ ", style=rich_style("accent"))
        left.append("E X O", style=rich_style("fg", "bold"))
        if self.agent:
            left.append(f"  {DOT}  ", style=rich_style("line2"))
            left.append(self.agent, style=rich_style("fg2"))
        if self.thread:
            left.append(f"  {DOT}  ", style=rich_style("line2"))
            left.append(self.thread, style=rich_style("fg3"))
        right = Text(no_wrap=True)
        if self.live:
            pulse = reduced_motion() or (self.frame // 4) % 2 == 0
            right.append("● ", style=rich_style("accent" if pulse else "accent2"))
            right.append("LIVE", style=rich_style("accent", "bold"))
        else:
            right.append("○ ", style=rich_style("fg3"))
            right.append("IDLE", style=rich_style("fg3"))
        row = Table.grid(expand=True)
        row.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        row.add_column(justify="right", no_wrap=True)
        row.add_row(left, right)
        return row


class StatusStrip(Widget):
    """Instruments on one line: served model (and the fallback it took), memory gauge, cache share, cost, findings."""

    def __init__(self) -> None:
        super().__init__(id="status")
        self.d: dict[str, Any] = {"model": "", "requested": "", "ctx": 0, "window": 0, "cache": None, "cost": "\u2013",
                                  "findings": 0, "bypass": False, "auto": 0}

    def set(self, **d: Any) -> None:
        self.d.update(d)
        self.refresh()

    def render(self) -> Text:
        d = self.d
        w = self.size.width or 100
        t = Text(no_wrap=True, overflow="ellipsis")

        def label(s: str) -> None:
            t.append(s + " ", style=rich_style("fg3"))

        def sep() -> None:
            t.append(f"  {DOT}  ", style=rich_style("line2"))

        if d.get("full_session"):
            t.append("permissions: full for all sessions" if d["full_session"] == "*" else "permissions: full only " + d["full_session"], style=rich_style("accent2", "bold reverse"))
            sep()
        elif d["bypass"]:  # D-065: first, so a narrow window cuts the instruments, never this
            t.append("permissions: bypass", style=rich_style("accent2", "bold reverse"))
            if w >= 80:
                t.append(f" {d['auto']} auto-approved", style=rich_style("accent2"))
            sep()
        if d.get("rounds") is not None:
            value = "unlimited" if d["rounds"] == 0 else str(d["rounds"])
            t.append(f"rounds: {value}", style=rich_style("accent2", "bold"))
            sep()
        label("MODEL")
        fell = bool(d["requested"] and d["model"] and d["requested"] != d["model"])
        t.append(d["model"] or "-", style=rich_style("accent" if fell else "fg2", "bold" if fell else ""))
        if fell:
            t.append(f" ← {d['requested']}", style=rich_style("fg3"))
        frac = d["ctx"] / d["window"] if d["window"] else 0.0
        sep()
        label("MEM")
        if d["window"]:
            t.append_text(gauge(frac, 10, "accent2" if frac >= 0.85 else "cold"))
            t.append(f" {min(999, round(100 * frac))}%", style=rich_style("fg2"))
        else:
            t.append("capacity unknown", style=rich_style("fg2"))
        if w >= 70 and d["ctx"]:
            t.append(f" {human_tokens(d['ctx'])}", style=rich_style("fg3"))
        if w >= 56:
            sep()
            label("CACHE")
            t.append("-" if d["cache"] is None else f"{round(100 * d['cache'])}%", style=rich_style("fg2"))
        sep()
        label("COST")
        t.append(str(d["cost"]), style=rich_style("accent2" if d["cost"] == "unknown" else "fg2"))
        if d["findings"]:
            sep()
            n = d["findings"]
            t.append(f"{n} finding{'s' if n != 1 else ''}", style=rich_style("accent", "bold"))
        return t


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
        self.confirming = False  # "a" was pressed: allow-always waits for Enter

    def body(self) -> Group:
        r = self.req
        width = self.size.width or 60
        out: list[Any] = [header_rule(f"APPROVAL {DOT} {r.risk}", "accent", width)]
        if not (self.noting or self.confirming):
            pairs = [("o", "once")] if "allow" in r.options else []
            if "allow_always" in r.options:
                pairs.append(("a, Enter", "always"))
            pairs.append(("d, Enter", "deny"))
            keys = Text("Choose: " if self.has_focus else "Ctrl+F, then ", style=rich_style("accent2"))
            keys.append_text(chips(pairs))
            out.append(keys)
        out.append(Text(r.summary, style=rich_style("fg")))
        if r.call is not None:
            call = Text(f"{r.call.name} {r.call.arguments}", style=rich_style("fg3"), no_wrap=True, overflow="ellipsis")
            out.append(call)
        if self.noting:
            out.append(Text("why deny? enter sends, empty is fine", style=rich_style("fg3")))
        elif self.confirming:
            out.append(Text("allow always? enter confirms, any other key cancels", style=rich_style("accent2")))
        return Group(*out)

    def compose(self) -> ComposeResult:
        yield Static(self.body(), id="approval-body")
        yield Input(placeholder="why deny? (enter to send, empty is fine)", id="deny-note")

    def on_resize(self) -> None:
        self.query_one("#approval-body", Static).update(self.body())

    def on_mount(self) -> None:
        self.query_one("#deny-note").display = False
        reveal(self)

    def _redraw(self) -> None:
        self.query_one("#approval-body", Static).update(self.body())

    def on_focus(self) -> None:
        self._redraw()

    def on_blur(self) -> None:
        self.confirming = False
        if not self.noting:
            self._redraw()

    def _answer(self, decision: str, note: str = "") -> None:
        self.post_message(self.Answered(self.req.request_id, decision, note))

    def on_key(self, event: events.Key) -> None:
        if self.noting:
            if event.key == "escape":
                self.noting = False
                self.query_one("#deny-note").display = False
                self.focus()
                self._redraw()
                event.stop()
            return
        if not self.has_focus:  # editable inputs never authorize; only a deliberately focused card acts
            return
        k = event.key
        opts = self.req.options
        if self.confirming:
            self.confirming = False
            self._redraw()
            if k == "enter":
                self._answer("allow_always")
            event.stop()
            return
        if k == "o" and "allow" in opts:
            self._answer("allow")
        elif k in ("a", "A", "shift+a") and "allow_always" in opts:
            self.confirming = True
            self._redraw()
        elif k == "d":
            self.noting = True
            self.query_one("#approval-body", Static).update(self.body())
            box = self.query_one("#deny-note", Input)
            box.display = True
            box.focus()
        else:
            if k == "escape":
                self.app.query_one(Composer).focus()
                event.stop()
            return  # esc included: it never answers for you
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
        role = "accent" if kind == "approval" else "cold"
        self.query_one("#panel-title", Static).update(
            header_rule(f"{kind} {DOT} {title}" if title else kind, role, (self.size.width or 40) - 3, "esc closes"))
        self.set_class(role == "accent", "gate")
        self.query_one("#panel-body", Static).update(render_panel(kind, text, data))
        lst = self.query_one("#panel-choices", OptionList)
        self.options = choice_options(data) if kind == "choice" else []
        lst.clear_options()
        lst.display = bool(self.options)
        if self.options:
            lst.add_options([Option(label) for _, label in self.options])
            lst.highlighted = 0
        self.display = True
        reveal(self)

    def hide(self) -> None:
        self.display = False
        self.panel_id = ""

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if 0 <= event.option_index < len(self.options):
            self.post_message(self.Chose(self.panel_id, self.options[event.option_index][0]))


class Welcome(Static):
    """The empty start: what to type and which keys matter. Gone with the first line of the transcript."""

    def __init__(self, agent: str, model: str, memory: str = "") -> None:
        super().__init__("", id="welcome")
        self.agent, self.model, self.memory = agent, model, memory
        self.shown: Any = None

    def on_mount(self) -> None:
        head = Text()
        head.append("\u25e2 ", style=rich_style("accent"))
        head.append("E X O", style=rich_style("fg", "bold"))
        bits = [self.agent, self.model or "-"] + ([f"memory {self.memory}"] if self.memory else [])
        info = Text(f"  {DOT}  ".join(bits), style=rich_style("fg3"))
        keys = chips([("enter", "send"), ("@", "file"), ("!", "shell"), ("/", "commands"), ("ctrl+p", "palette"),
                      ("ctrl+c", "stop")])
        self.shown = Group(head, info, Text(""), keys)
        self.update(self.shown)
        reveal(self)
