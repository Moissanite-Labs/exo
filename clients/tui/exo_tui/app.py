"""The Exo chat screen: transcript, activity strip, composer, status line, right-hand panel."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Static
from rich.theme import Theme as RichTheme

from exo.config import exo_home
from exo.core.types import ContentPart
from exo.core.trace import money
from exo.protocol import schema as s
from exo_tui.onboarding import INTRO_PROMPT, Onboarding, load_catalog
from exo_tui import theme as th
from exo_tui.hero import Hero, hero_rows
from exo_tui.boot import STEPS as BOOT_STEPS
from exo_tui.boot import BootScreen
from exo_tui.providers import ProvidersScreen, refuse_key_in_composer
from exo_tui.widgets import (BUSY_HINT, IDLE_HINT, ApprovalCard, Block, BrandBar, Composer, EditScreen, Palette,
                             PanelView, PromptMark, StatusStrip, ToolBlock, Welcome)

CLIENT_ID = "exo-tui"
DEFAULT_WINDOW = 200_000
MAX_FILE = 64 * 1024
WINDOW = 60  # transcript entries mounted when a session opens; older ones stay as plain data
PAGE = 60  # entries added per "load earlier"
FLUSH_EVERY = 0.1  # streaming repaint interval in seconds (at most 10 a second)
LONG_TURN_MS = 60_000  # the same line as the turn.slow trace rule: wall minus tool time and approval waits
AT_RE = re.compile(r"(?<!\S)@(\S+)")
# name -> (group, description). /help is generated from this table, so every command shows up there.
COMMANDS = {
    "new": ("session", "start a new thread: a fresh focus, same memory; /new private is not remembered"),
    "sessions": ("session", "pick one of this agent's threads to switch to"),
    "resume": ("session", "same as /sessions"),
    "fork": ("session", "fork this session"),
    "undo": ("session", "undo the last turn"),
    "clear": ("session", "clear the transcript view (history is kept)"),
    "cwd": ("session", "show this session's working folder"),
    "context": ("session", "show what the next model call carries: each part with its tokens (read-only)"),
    "compact": ("session", "summarize now what is eligible in this agent's memory, then show tokens before and after"),
    "restart": ("session", "reopen this session in a new terminal tab and exit this one"),
    "exit": ("session", "quit"),
    "quit": ("session", "same as /exit"),
    "model": ("model", "pick the model for this session; /model <ref> sets it"),
    "default": ("model", "set the default model for new sessions: /default <ref>, or /default alone to pick (same as /model default)"),
    "providers": ("model", "set up model providers: add or replace a key, test it, remove it, pick the default"),
    "usage": ("model", "show context size, cost and the model in use"),
    "panel": ("tools", "reopen the last panel"),
    "peers": ("tools", "list the other live sessions (Exo and Hermes) you can message"),
    "send": ("tools", "message another session yourself: /send <peer> <text>"),
    "handoff": ("other", "have the agent write a handoff note for the next session: /handoff [path]"),
    "intro": ("other", "an orientation from the agent: what Exo is, what it can do, how to use it"),
    "tutorial": ("other", "tips the first time something comes up: /tutorial on, off or reset (bare shows state)"),
    "copy": ("other", "copy to the clipboard: /copy (last answer), /copy error, /copy all"),
    "help": ("other", "list every command"),
}
LOCAL_COMMANDS = {n: d for n, (_, d) in COMMANDS.items()}
GROUPS = ("session", "model", "tools", "other")


PROMPT_IDLE = th.PROMPT  # the mark left of the composer; PromptMark redraws it with steer state


class ExoApp(App):
    TITLE = "exo"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { background: $background; color: $foreground; }
    #brand { height: 2; padding: 0 1; border-bottom: solid $line; background: $background; }
    #main { height: 1fr; }
    #left { width: 1fr; }
    #transcript { height: 1fr; padding: 1 2 0 2; background: $deep; scrollbar-size-vertical: 1;
                  scrollbar-background: $deep; scrollbar-color: $line-2; scrollbar-color-hover: $fg-3;
                  scrollbar-color-active: $accent; }
    .block { margin: 0; }
    .user, .assistant, .error, .hint { margin: 1 0; }
    .peer, .taste, .finding, .report { margin: 1 0; background: $surface; padding: 0 1; border-left: outer $cold; }
    #welcome { margin: 1 0; }
    .peer { border-left: double $cold; }
    #activity { height: 1; color: $fg-3; padding: 0 2; background: $deep; }
    #compose { height: auto; background: $background; border-top: solid $line; padding: 0 2; }
    #prompt { width: auto; height: 1; margin-right: 1; }
    #composer { height: auto; min-height: 1; max-height: 8; border: none; padding: 0; background: $background;
                width: 1fr; }
    #composer:focus { border: none; }
    #composer .text-area--cursor { background: $accent; color: #0a0a0a; text-style: none; }
    #composer .text-area--cursor-line { background: $background; }
    #composer .text-area--placeholder { color: $fg-3; }
    #status { height: 2; padding: 0 2; border-top: solid $line; background: $surface; }
    #approval { height: auto; border-left: outer $accent; background: $surface; padding: 0 1; margin: 0 2 1 2; }
    #hero { margin: 0; }
    #approval Input { border: none; background: $panel; height: 1; padding: 0 1; }
    #approval Input:focus { border: none; }
    #panel { width: 45%; border-left: outer $cold; background: $surface; padding: 0 1; }
    #panel.gate { border-left: outer $accent; }
    #panel-title { height: 1; }
    #panel-scroll { height: 1fr; }
    #panel-choices { height: auto; max-height: 10; background: $surface; border: none; }
    OptionList { background: $surface; border: none; }
    OptionList > .option-list--option-highlighted { background: $accent; color: #0a0a0a; text-style: bold; }
    OptionList:focus > .option-list--option-highlighted { background: $accent; color: #0a0a0a; }
    Palette, EditScreen { align: center middle; background: $background 70%; }
    #palette-box, #edit-box { width: 80%; height: 70%; border: solid $line-2; border-left: outer $accent;
                              background: $surface; padding: 0 1; }
    #palette-input { border: none; background: $panel; }
    #palette-input:focus { border: none; }
    #edit-area { height: 1fr; }
    BootScreen { align: center middle; background: $deep; }
    #boot-art { width: 36; height: auto; }
    """
    BINDINGS = [
        Binding("ctrl+p", "palette", "palette", priority=True),
        Binding("ctrl+o", "toggle_tools", "tool output", priority=True),
        Binding("ctrl+y", "verdict('accept')", "accept", priority=True),
        Binding("ctrl+e", "edit_answer", "edit", priority=True),
        Binding("ctrl+r", "verdict('reject')", "reject", priority=True),
        Binding("escape", "escape", "esc", show=False),
        Binding("f6", "load_earlier", "earlier", priority=True, show=False),
    ]

    def __init__(self, client: Any, session: str | None = None, resume: bool = False,
                 seat: str | None = None, title: str = "", home: str | None = None,
                 auto_intro: bool = False, boot_anim: bool = False, provider_check: bool = False) -> None:
        super().__init__()
        self.motion = not th.reduced_motion()
        self.boot_anim = boot_anim and self.motion  # only the real entry point asks; never under reduced motion
        self.boot_screen: BootScreen | None = None
        self.register_theme(th.EXO_THEME)
        self.register_theme(th.PLAIN_THEME)
        if th.no_color():
            self.theme = "exo-plain"
            self.ansi_color = True
        else:
            self.theme = "exo"
        self.console.push_theme(RichTheme({
            "markdown.h1": th.rich_style("fg", "bold"), "markdown.h2": th.rich_style("fg", "bold"),
            "markdown.h3": th.rich_style("fg", "bold"), "markdown.h4": th.rich_style("fg2", "bold"),
            "markdown.code": th.rich_style("accent2"), "markdown.code_block": th.rich_style("fg2"),
            "markdown.link": th.rich_style("cold"), "markdown.link_url": th.rich_style("cold", "underline"),
            "markdown.item.bullet": th.rich_style("accent"), "markdown.item.number": th.rich_style("accent"),
            "markdown.block_quote": th.rich_style("fg3", "italic"), "markdown.hr": th.rich_style("line2"),
            "markdown.strong": th.rich_style("fg", "bold"), "markdown.em": "italic",
        }))
        self.served = ""  # the route a model call actually used this turn
        self.requested = ""  # the route the turn asked for; differs from self.model when a fallback served
        self.cache_share: float | None = None  # share of the last call's input that came from the cache
        self.thread_label = ""
        self.frame = 0
        self.client = client
        self.session = session
        self.resume = resume
        self.seat = seat
        self.title_arg = title
        self.model = ""
        self.window = DEFAULT_WINDOW
        self.ctx_tokens = 0
        self.cost: float | None = None  # session USD from priced calls; None = no priced call (D-032: unknown, never 0)
        self.calls_seen = 0  # model calls that used tokens, seen by this client
        self.unpriced = 0  # of those, calls with no price
        self.findings = 0  # open harness findings (D-025), polled after each turn
        self.busy = False
        self.turn_t0 = 0.0  # when the running turn began; 0 once text or reasoning has streamed
        self.expanded = False
        self.ready = asyncio.Event()
        self.cur: Block | None = None
        self.cur_id: str | None = None
        self.last_answer: tuple[str | None, str] = (None, "")
        self.tools: dict[str, ToolBlock] = {}
        self.approvals: list[s.ApprovalRequested] = []
        self.card: ApprovalCard | None = None
        self.question: str | None = None
        self.running_tools = 0
        self.agents: dict[str, str] = {}
        self.panels: dict[str, tuple[str, str, str, str, dict]] = {}
        self.panel_order: list[str] = []
        self.panel_touched: set[str] = set()
        self.commands: list[dict[str, Any]] = []
        self.onb = Onboarding(home or exo_home())  # first run and seen hints (D-030)
        self.auto_intro = auto_intro  # only the real entry point turns this on; tests and daemons never do
        self.provider_check = provider_check  # D-034 first-run note; the real entry point only, like auto_intro
        self.tutorial = False  # mirrors config tutorial.enabled
        self.hints = load_catalog()
        self.hint_budget = 1  # one tip per turn
        self.intro_after_providers = False  # a first run is waiting for a provider before it sends /intro
        self.intro_pending = ""  # "first" or "manual" from sending /intro until its turn starts
        self.sent: list[Any] = []  # every command body this client sent, for tests and debugging
        self._dirty = False
        self.older: list[tuple] = []  # history entries not mounted yet, oldest first
        self.earlier_row: Static | None = None
        self._loading = False

    # ------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        yield BrandBar()
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield VerticalScroll(id="transcript")
                yield Static("", id="activity")
                with Horizontal(id="compose"):
                    yield PromptMark(PROMPT_IDLE, id="prompt")
                    yield Composer()
            yield PanelView()
        yield StatusStrip()

    def on_mount(self) -> None:
        self.brand, self.strip, self.prompt = self.query_one(BrandBar), self.query_one(StatusStrip), self.query_one(PromptMark)
        self.composer = self.query_one(Composer)  # cached: a modal screen on top hides them from query_one
        self.composer.focus()
        self.set_interval(1.0, self.refresh_activity)  # ticks the "waiting for <model>" counter
        if self.motion:
            self.set_interval(0.12, self.tick)  # spinners, the live dot and the packet on the wire
        self.watch(self.transcript, "scroll_y", self.watch_transcript_scroll, init=False)
        self.refresh_status()
        if self.boot_anim:
            self.boot_screen = BootScreen()
            self.push_screen(self.boot_screen, lambda _: self.query_one(Composer).focus())
        self.run_worker(self.boot(), exit_on_error=False)

    def tick(self) -> None:
        self.frame += 1
        self.brand.tick(self.frame)
        if self.running_tools:
            for tb in self.tools.values():
                if tb.status == "running" and tb.is_mounted:
                    tb.frame = self.frame
                    tb.flush()
        if self.busy:
            self.refresh_activity()

    def boot_mark(self, step: str, detail: str = "") -> None:
        if self.boot_screen is not None:
            self.boot_screen.mark(step, detail)

    def boot_end(self) -> None:
        if self.boot_screen is not None:
            for step, _ in BOOT_STEPS:
                self.boot_screen.mark(step, "-")

    # ------------------------------------------------------------ helpers
    @property
    def transcript(self) -> VerticalScroll:
        return self.query_one("#transcript", VerticalScroll)

    def transcript_text(self) -> str:
        old = [e[2] if e[0] == "block" else f"{e[2]} {e[5]}" for e in self.older]  # history not mounted yet
        return "\n".join(old + [w.text if isinstance(w, Block) else f"{w.tool_name} {w.output}"
                                for w in self.transcript.children if isinstance(w, (Block, ToolBlock))])

    def status_text(self) -> str:
        """The status strip as plain text (the strip itself draws instruments from the same numbers)."""
        pct = min(999, round(100 * self.ctx_tokens / self.window)) if self.window else 0
        bug = f" · {self.findings} findings" if self.findings else ""
        cache = "" if self.cache_share is None else f" · cache {round(100 * self.cache_share)}%"
        return f"{self.seat or 'main'} · {self.model or '-'} · ctx {pct}%{cache} · {self.cost_label()}{bug}"

    def cost_label(self) -> str:
        """The D-032 rule, formatted by the same `money` /trace uses: "-" before any call, "unknown" when no call is
        priced, "$x+" when some are not, never "$0.00" for an unpriced call."""
        if self.cost is None:
            return "unknown" if self.calls_seen else "\u2013"
        return money(self.cost, self.unpriced)

    def refresh_status(self) -> None:
        self.strip.set(model=self.model, requested=self.requested, ctx=self.ctx_tokens,
                                        window=self.window, cache=self.cache_share, cost=self.cost_label(),
                                        findings=self.findings)
        self.brand.set(self.seat or "main", self.thread_label, self.busy)
        self.prompt.set_state(self.busy, bool(self.question))
        self.composer.placeholder = BUSY_HINT if self.busy else IDLE_HINT

    def wire(self, width: int = 9) -> Text:
        """A packet travelling along a wire: data in flight. Still under reduced motion."""
        pos = (self.frame // 2) % width if self.motion else width // 2
        t = Text(no_wrap=True)
        for k in range(width):
            t.append("\u25cf" if k == pos else "\u2500", style=th.rich_style("accent" if k == pos else "line2"))
        return t

    def refresh_activity(self) -> None:
        bits = []
        if self.running_tools:
            bits.append("running tool" if self.running_tools == 1 else f"running {self.running_tools} tools")
        elif self.busy and self.turn_t0:
            bits.append(f"waiting for {self.model or 'model'} {int(time.monotonic() - self.turn_t0)}s")
        elif self.busy:
            bits.append("working")
        live = sum(1 for v in self.agents.values() if v in ("queued", "running"))
        if live:
            bits.append(f"{live} subagent{'s' if live != 1 else ''}")
        line = Text(no_wrap=True, overflow="ellipsis")
        if bits:
            line.append_text(self.wire())
            line.append("  " + f" {th.DOT} ".join(bits), style=th.rich_style("fg3"))
        for bar in self.query("#activity"):  # absent before the screen mounts or after it closes
            bar.update(line)

    def add(self, widget: Block | ToolBlock) -> None:
        self.add_many([widget])

    def add_many(self, widgets: list[Block | ToolBlock]) -> None:
        """One mount call and one scroll for a whole batch."""
        if not widgets:
            return
        if any(not (isinstance(w, Block) and w.kind in ("note", "warn", "job", "memory")) for w in widgets):
            for w in self.query("#welcome, #hero"):  # a system line does not end the empty start; a message does
                w.remove()
        self.transcript.mount(*widgets)
        self.transcript.scroll_end(animate=False)

    def close_cur(self) -> None:
        """End the streaming block: paint it as Markdown once, and stop appending to it."""
        c, self.cur = self.cur, None
        if c is not None and c.streaming:
            c.streaming = False
            if c.is_mounted:
                c.flush()

    def note(self, text: str, kind: str = "note") -> None:
        self.close_cur()
        self.add(Block(text, kind))

    def mark_dirty(self) -> None:
        if not self._dirty:
            self._dirty = True
            self.set_timer(FLUSH_EVERY, self.flush)

    def flush(self) -> None:
        self._dirty = False
        if self.cur is not None and self.cur.is_mounted:
            self.cur.flush()
            self.transcript.scroll_end(animate=False)

    # ------------------------------------------------------------ boot and events
    async def boot(self) -> None:
        try:
            hello = await self.client.call(s.Hello(client=CLIENT_ID, capabilities=["markdown", "diff"],
                                                   screen_cols=self.size.width or None))
            self.boot_mark("core", "connected")
            try:
                for m in (await self.client.call(s.ModelsList())).get("models", []):
                    if m.get("role") == "main":
                        self.model = self.model or m.get("route", "")
                        if m.get("context"):
                            self.window = int(m["context"])
                            break
                self.boot_mark("model", self.model)
                self.commands = (await self.client.call(s.CommandsList())).get("commands", [])
            except Exception:
                pass
            try:
                self.tutorial = (await self.client.call(s.ConfigGet(key="tutorial.enabled"))).get("value") is True
            except Exception:
                self.tutorial = False  # not set yet
            if self.resume and not self.session:
                rows = (await self.client.call(s.SessionList(limit=20, seat=self.seat)))["sessions"]
                rows = [r for r in rows if r.get("kind") != "subagent"]  # resume a thread, not a child's log
                self.session = rows[0]["id"] if rows else None
            if self.session:
                await self.load_session(self.session)
            else:
                await self.new_session(announce=False)
            self.boot_mark("agent", self.seat or "main")
            self.boot_mark("memory", "ready")
            self.run_worker(self.pump(hello.get("seq", 0)), exit_on_error=False)
            want_intro = self.auto_intro and not self.resume and not self.onb.onboarded
            # A first run with no working provider goes to /providers first; the intro waits for a key,
            # because the orientation is an agent turn and would only fail without one.
            ready = await self.first_run_providers() if self.provider_check else True
            if want_intro and ready is False:
                self.intro_after_providers = True
            elif want_intro:
                await self.intro("first")
        except Exception as exc:
            self.note(f"cannot start: {type(exc).__name__}: {exc}", "error")
        self.boot_end()
        self.refresh_status()
        self.ready.set()

    async def pump(self, since: int) -> None:
        async for ev in self.client.events(since=since, reconnect=True):
            if ev.session not in (None, self.session):
                continue
            try:
                self.handle(ev)
            except Exception as exc:  # a bad event must not stop the feed, and the harness is told (D-025)
                self.note(f"event {ev.type}: {type(exc).__name__}: {exc}", "error")
                if ev.type != "harness.error":  # never report the report of an error
                    self.report_error(f"tui event {ev.type}", exc)

    def report_error(self, where: str, exc: Exception) -> None:
        """Send a caught exception to the core as a harness.error event."""
        import traceback

        body = {"where": where, "error_type": type(exc).__name__, "message": str(exc),
                "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)[-3:])}
        self.run_worker(self.client.send(s.CommandRun(name="harness_error", args=json.dumps(body),
                                                      session=self.session)), exit_on_error=False)

    async def refresh_findings(self) -> None:
        await asyncio.sleep(0.5)  # the core files findings just after the turn ends
        try:
            out = (await self.client.call(s.CommandRun(name="findings", args="count"))).get("output")
            before, self.findings = self.findings, int(out.get("open", 0)) if isinstance(out, dict) else 0
        except Exception:
            return
        if self.findings > before:
            self.close_cur()
            self.add(Block(f"{self.findings} open", "finding", head=f"{self.findings} open",
                           body="The harness noticed something odd and filed it.", meta=f"/findings {th.DOT} /trace"))
            self.hint("finding")
        self.refresh_status()

    @staticmethod
    def cache_of(u: Any) -> float | None:
        total = u.input_tokens + u.cache_read_tokens
        return u.cache_read_tokens / total if total else None

    def handle(self, ev: s.Event) -> None:
        b = ev.body
        if isinstance(b, s.TurnStarted):
            self.busy = True
            if self.intro_pending:
                self.intro_started()
            self.turn_t0 = time.monotonic()
            self.model = b.model or self.model
            self.requested, self.served = self.model, ""  # what this turn asked for; ModelCall says what served
            self.close_cur()
        elif isinstance(b, s.TurnCompleted):
            self.busy = False
            self.turn_t0 = 0.0
            self.running_tools = 0
            self.close_cur()
            self.hint_budget = 1
            if (b.timing.get("wall_ms", 0) - b.timing.get("tool_ms", 0) - b.timing.get("approval_ms", 0)) >= LONG_TURN_MS:
                self.hint("long_turn")
            self.run_worker(self.refresh_findings(), exit_on_error=False)
        elif isinstance(b, s.ReasoningDelta):
            self.turn_t0 = 0.0
        elif isinstance(b, s.MessageDelta):
            self.turn_t0 = 0.0
            if self.cur is None or self.cur_id != b.message_id:
                self.cur_id = b.message_id
                self.close_cur()
                self.cur = Block("", "assistant")
                self.cur.streaming = True
                self.add(self.cur)
            self.cur.text += b.text
            self.mark_dirty()
        elif isinstance(b, s.MessageCompleted):
            m = b.message
            if m.role == "assistant":
                text = m.text()
                if text:
                    if self.cur is None or self.cur_id != m.id:
                        if self.cur is None or not self.cur.text:
                            self.cur = Block(text, "assistant")
                            self.add(self.cur)
                    self.cur.text = text
                    self.cur.streaming = False
                    if self.cur.is_mounted:
                        self.cur.flush()
                        self.transcript.scroll_end(animate=False)
                    self.last_answer = (m.id, text)
                self.close_cur()
        elif isinstance(b, s.ToolStarted):
            tb = ToolBlock(b.call.id, b.call.name, b.call.arguments)
            tb.expanded = self.expanded
            if b.call.name == "code_find":
                self.hint("code_find")
            self.tools[b.call.id] = tb
            self.running_tools += 1
            self.close_cur()
            self.add(tb)
        elif isinstance(b, s.ToolOutput):
            if tb := self.tools.get(b.call_id):
                tb.output += b.text
                tb.flush()
        elif isinstance(b, s.ToolCompleted):
            self.running_tools = max(0, self.running_tools - 1)
            if tb := self.tools.get(b.result.call_id):
                tb.output = b.result.text() or tb.output
                tb.finish("error" if b.result.is_error else "done", b.duration_ms / 1000 if b.duration_ms else None)
        elif isinstance(b, s.ApprovalRequested):
            self.approvals.append(b)
            self.show_approval()
            self.hint("approval")
        elif isinstance(b, s.ApprovalResolved):
            self.approvals = [a for a in self.approvals if a.request_id != b.request_id]
            if self.card is not None and self.card.req.request_id == b.request_id:
                self.close_card()
        elif isinstance(b, s.QuestionAsked):
            self.question = b.question_id
            opts = f" [{' / '.join(b.options)}]" if b.options else ""
            self.note(f"question: {b.text}{opts}  (type your answer)")
        elif isinstance(b, s.UsageUpdated):
            u = b.usage
            self.ctx_tokens = u.input_tokens + u.cache_read_tokens
            if b.session_total and b.session_total.cost_usd is not None:
                self.cost = b.session_total.cost_usd
            self.model = self.served or b.model or self.model  # a fallback that served stays visible
            if self.cache_share is None:
                self.cache_share = self.cache_of(u)
        elif isinstance(b, s.ModelCall):
            if b.route:
                self.model = self.served = b.route  # the route that served, not the one asked for
            if b.requested:
                self.requested = b.requested
            if b.usage is not None and (b.usage.input_tokens or b.usage.output_tokens or b.usage.cache_read_tokens
                                        or b.usage.cache_write_tokens):
                self.calls_seen += 1
                self.unpriced += b.usage.cost_usd is None
            if b.usage is not None and (b.usage.input_tokens or b.usage.cache_read_tokens):
                self.cache_share = self.cache_of(b.usage)
            if (b.requested and b.route and b.requested != b.route) or any(
                    a.get("outcome") == "fallback" for a in b.attempts):
                self.hint("route_change")
        elif isinstance(b, s.TaskUpdated):
            self.agents[b.task_id] = b.status
        elif isinstance(b, s.PeerMessage):  # D-026: a peer is not the user, so it gets its own block
            self.add(Block(f"peer message from {b.sender} ({b.trust})\n{b.text}", "peer",
                           head=f"{b.sender} {th.DOT} {b.trust}", body=b.text,
                           meta="not you: peers cannot approve for you" if b.trust not in ("principal",) else ""))
            self.hint("peer_message")
        elif isinstance(b, s.PeerDelivery):
            if b.direction == "out" or b.state in ("denied", "expired"):
                self.note(f"peer {b.peer}: {b.state}" + (f" ({b.detail})" if b.detail else ""),
                          "warn" if b.state != "delivered" and b.state != "released" else "note")
        elif isinstance(b, s.JobUpdated):  # D-032: one system line per state change, cold; a bad ending is warm
            code = "" if b.exit_code is None else f", exit {b.exit_code}"
            self.note(f"job {b.job_id} {b.state}{code}: {b.command[:80]}" + (f" ({b.detail})" if b.detail and b.state != "running" else ""),
                      "warn" if b.state in ("failed", "timeout", "lost") else "job")
        elif isinstance(b, s.UiPanel):
            self.on_ui_panel(b)
        elif isinstance(b, s.TasteUpdated):
            self.close_cur()
            self.add(Block(f"taste {b.action}: {b.text} {b.path}", "taste",
                           head={"add": "learned", "update": "updated", "remove": "removed"}.get(b.action, b.action),
                           body=b.text, meta=f"edit file {th.DOT} {b.path}" if b.path else ""))
        elif isinstance(b, s.MemorySummarized):
            what = f"log {b.start}-{b.end} folded" if b.level == 0 else f"level {b.level} summary merged"
            self.note(f"memory {th.DOT} {what} {th.DOT} {b.before_tokens} \u2192 {b.after_tokens} tok", "memory")
            self.hint("memory_summarized")
        elif isinstance(b, s.ContextCompacted):
            self.note(f"context compacted: {b.before_tokens} to {b.after_tokens} tokens", "memory")
        elif isinstance(b, s.SessionUpdated):
            if b.session.id == self.session:
                self.model = self.model or b.session.model or ""
                self.seat = self.seat or b.session.seat
        elif isinstance(b, s.Notice):
            self.note(f"{b.level}: {b.text}" if b.level != "info" else b.text, "warn" if b.level == "warn" else "note")
        elif isinstance(b, s.Error):
            self.note(f"error: {b.code}: {b.message}", "error")
        self.refresh_status()
        self.refresh_activity()

    # ------------------------------------------------------------ sessions
    def clear_transcript(self) -> None:
        self.transcript.remove_children()
        self.tools.clear()
        self.cur = None
        self.older = []
        self.earlier_row = None
        self.last_answer = (None, "")

    def history_entries(self, messages: list[dict[str, Any]]) -> list[tuple]:
        """Stored messages as plain tuples: ("block", kind, text) or ("tool", id, name, args, status, output)."""
        out: list[tuple] = []
        by_id: dict[str, int] = {}
        for m in messages:
            if (m.get("meta") or {}).get("note"):  # context notes from other threads are for the model (D-028)
                continue
            text = "".join(p.get("text", "") for p in m.get("content", []) if p.get("type") == "text")
            if m["role"] == "user" and text:
                peer = (m.get("meta") or {}).get("peer") or (m.get("meta") or {}).get("job")
                out.append(("block", "peer" if peer else "user", text))
            elif m["role"] == "assistant":
                if text:
                    out.append(("block", "assistant", text))
                    self.last_answer = (m.get("id"), text)
                for tc in m.get("tool_calls", []):
                    by_id[tc["id"]] = len(out)
                    out.append(("tool", tc["id"], tc["name"], tc.get("arguments"), "done", ""))
            elif m["role"] == "tool":
                for r in m.get("tool_results", []):
                    if (n := by_id.get(r["call_id"])) is not None:
                        out[n] = out[n][:4] + ("error" if r.get("is_error") else "done",
                                               "".join(p.get("text", "") for p in r.get("content", [])))
        return out

    def widgets_for(self, entries: list[tuple]) -> list[Block | ToolBlock]:
        widgets: list[Block | ToolBlock] = []
        for e in entries:
            if e[0] == "block":
                widgets.append(Block(e[2], e[1]))
            else:
                tb = ToolBlock(e[1], e[2], e[3])
                tb.status, tb.output = e[4], e[5]
                self.tools[e[1]] = tb
                widgets.append(tb)
        return widgets

    def render_history(self, messages: list[dict[str, Any]]) -> None:
        """Mount only the last WINDOW entries; the rest wait as data until asked for (F6 or scroll to the top)."""
        entries = self.history_entries(messages)
        self.older, recent = entries[:-WINDOW], entries[-WINDOW:]
        self.add_many(self.widgets_for(recent))
        self.update_earlier_row()

    def update_earlier_row(self) -> None:
        n = len(self.older)
        if n and self.earlier_row is None:
            self.earlier_row = Static("", id="earlier", classes="block note")
            self.transcript.mount(self.earlier_row, before=0)
        if self.earlier_row is not None:
            if n:
                self.earlier_row.update(Text(f"{n} earlier message{'s' if n != 1 else ''}. "
                                             "Press F6 or scroll to the top to load more.", style="dim italic"))
            else:
                self.earlier_row.remove()
                self.earlier_row = None

    def action_load_earlier(self) -> None:
        if self._loading or not self.older or self.modal_open():
            return
        self._loading = True
        self.run_worker(self.load_earlier(), exit_on_error=False)

    async def load_earlier(self) -> None:
        try:
            tr = self.transcript
            page, self.older = self.older[-PAGE:], self.older[:-PAGE]
            widgets = self.widgets_for(page)
            anchor = next((w for w in tr.children if w is not self.earlier_row), None)
            await tr.mount(*widgets, before=anchor if anchor is not None else 0)
            self.update_earlier_row()
            if anchor is not None:  # keep what the reader was looking at in place
                tr.call_after_refresh(tr.scroll_to_widget, anchor, animate=False, top=True)
        finally:
            self._loading = False

    def watch_transcript_scroll(self, old: float, new: float) -> None:
        if new <= 0 < old and self.older:
            self.action_load_earlier()

    @staticmethod
    def label_of(info: dict[str, Any]) -> str:
        name = info.get("title") or str(info.get("id", ""))[-8:]
        return f"thread {name}" + (" (private)" if info.get("kind") == "private" else "")

    async def load_session(self, sid: str) -> None:
        got = await self.client.call(s.SessionResume(session=sid))
        self.session = sid
        info = got["session"]
        self.seat = self.seat or info.get("seat")
        self.model = info.get("model") or self.model
        self.thread_label = self.label_of(info)
        self.clear_transcript()
        self.render_history(got.get("messages", []))
        n = sum(1 for m in got.get("messages", []) if not (m.get("meta") or {}).get("note"))
        title = info.get("title") or ""
        what = "private thread" if info.get("kind") == "private" else "thread"
        self.note(f"resumed {what} {title + ' ' if title else ''}({sid}, {n} messages)", "note")
        self.refresh_status()

    async def new_session(self, kind: str = "", announce: bool = True) -> None:
        """A new thread of the same agent: a fresh focus with the same memory (D-028)."""
        got = await self.client.call(s.SessionCreate(title=self.title_arg, seat=self.seat, kind=kind))
        self.session = got["session"]["id"]
        self.model = got["session"].get("model") or self.model
        self.thread_label = self.label_of(got["session"])
        self.clear_transcript()
        if kind == "private":
            self.note(f"private thread {self.session}: logged here, never added to shared memory")
        elif announce:
            self.note(f"new thread {self.session} (same memory)")
        else:  # a plain startup: no log line, the empty-start card says it all
            rows = hero_rows(self.size.height)
            if rows:
                self.transcript.mount(Hero(rows))
            self.transcript.mount(Welcome(self.seat or "main", self.model))
        self.refresh_status()

    # ------------------------------------------------------------ sending
    async def post(self, body: Any) -> s.Response:
        self.sent.append(body)
        resp = await self.client.send(body)
        if not resp.ok:
            self.note(f"error: {resp.error.code}: {resp.error.message}", "error")
        return resp

    def on_composer_submitted(self, msg: Composer.Submitted) -> None:
        text = msg.text.strip()
        if refuse_key_in_composer(text):  # a key pasted in the composer: drop it here, before any worker or echo
            text = ""
            self.note("Keys do not go in the composer. Type /providers, pick the provider and press a: "
                      "the key field is hidden.", "warn")
            return
        if text:
            self.run_worker(self.submit(text), exit_on_error=False)

    def file_parts(self, text: str) -> list[ContentPart]:
        parts, seen = [], set()
        for m in AT_RE.finditer(text):
            rel = m.group(1).rstrip(".,;:)")
            p = Path(rel).expanduser()
            if not p.is_absolute():
                p = Path(os.getcwd()) / p
            if rel in seen or not p.is_file():
                continue
            seen.add(rel)
            try:
                body = p.read_bytes()[:MAX_FILE].decode("utf-8", "replace")
            except OSError:
                continue
            parts.append(ContentPart.of_text(f"[file {rel}]\n{body}"))
        return parts

    async def submit(self, text: str) -> None:
        if self.question and not text.startswith("/"):
            qid, self.question = self.question, None
            self.add(Block(text, "user"))
            await self.post(s.QuestionAnswer(question_id=qid, answer=text))
            return
        if text.startswith("/"):
            name, _, args = text[1:].partition(" ")
            await self.slash(name, args.strip())
            return
        shown, send = text, text
        if text.startswith("!"):
            cmd = text[1:].strip()
            send = f"Run this shell command with your shell tool and show me the output:\n{cmd}"
        self.add(Block(shown, "user", steer=self.busy))
        self.hint("steer" if self.busy else "first_message")
        self.close_cur()
        await self.post(s.TurnSend(session=self.session, text=send, parts=self.file_parts(text),
                                   mode="steer" if self.busy else "send"))

    async def slash(self, name: str, args: str) -> None:
        if not self.busy:
            self.hint_budget = 1
        if name in ("exit", "quit"):
            self.exit()
        elif name == "restart":
            await self.restart()
        elif name == "peers":
            resp = await self.post(s.Peers(action="list", session=self.session))
            if resp.ok:
                rows = resp.result.get("peers", [])
                self.note("\n".join([f"you are {resp.result.get('me')}; {len(rows)} peer(s)"] + [
                    f"  {r['name']}  [{r['platform']}, {r['state']}]  {r.get('cwd') or ''}  {r.get('title') or ''}"
                    for r in rows] + [f"  held for you: {h['msg_id']} from {h['from']}" for h in resp.result.get("held", [])]))
        elif name == "send":
            to, _, text = args.partition(" ")
            if not to or not text.strip():
                self.note("usage: /send <peer> <text>  (see /peers)", "warn")
            else:
                await self.post(s.Peers(action="send", to=to, text=text.strip(), session=self.session, via="human"))
        elif name == "clear":
            self.clear_transcript()
        elif name == "cwd":
            got = await self.post(s.SessionGet(session=self.session))
            if got.ok:
                self.note(f"cwd: {(got.result.get('session') or {}).get('cwd') or '-'}")
        elif name == "usage":
            self.note(f"model {self.model or '-'}  context {self.ctx_tokens} tokens  cost {self.cost_label()}")
        elif name == "default":
            await self.set_default(args)
        elif name == "providers":
            word, _, rest = args.partition(" ")
            if word == "add" and rest.strip():
                if " " in rest.strip():  # never echo what came after the provider name
                    self.note("Keys do not go in the composer. Use /providers add <name>, then the hidden field.",
                              "warn")
                    return
                self.open_providers(rest.strip(), add=True)
            else:
                self.open_providers()
        elif name == "new":
            await self.new_session("private" if args.strip() == "private" else "")
            self.hint("new_thread")
        elif name == "intro":
            await self.intro("manual")
        elif name == "handoff":
            await self.handoff(args)
        elif name == "tutorial":
            await self.tutorial_command(args)
        elif name in ("sessions", "resume"):
            await self.pick_session()
        elif name == "model":
            word, _, rest = args.partition(" ")
            if word == "default":
                await self.set_default(rest.strip())
            else:
                await self.set_model(args)
        elif name == "fork":
            resp = await self.post(s.SessionFork(session=self.session, title=args))
            if resp.ok:
                await self.load_session(resp.result["session"]["id"])
        elif name == "undo":
            resp = await self.post(s.SessionUndo(session=self.session))
            if resp.ok:
                await self.load_session(self.session)
        elif name == "copy":
            self.copy_command(args.strip())
        elif name == "help":
            self.note(self.help_text())
        elif name == "panel":
            if self.panel_order:
                self.show_panel(self.panel_order[-1], report=True)
        else:  # /context and /compact are core commands too (command.run); the core that holds the agent lock does the work
            if name == "compact":
                self.note("compacting...")
            resp = await self.post(s.CommandRun(name=name, args=args, session=self.session))
            if resp.ok:
                out = resp.result.get("output")
                if out and name == "context":
                    self.close_cur()
                    self.add(Block(out if isinstance(out, str) else str(out), "report", head="context " + th.DOT + " next call"))
                elif out:
                    self.note(out if isinstance(out, str) else str(out))

    # ------------------------------------------------------------ onboarding (D-030)
    async def intro(self, kind: str) -> None:
        """Send the orientation as a normal user turn. It shows as "/intro"; the agent gets the longer prompt."""
        self.add(Block("/intro", "user"))
        self.close_cur()
        self.intro_pending = kind
        resp = await self.post(s.TurnSend(session=self.session, text=INTRO_PROMPT,
                                          mode="steer" if self.busy else "send"))
        if not resp.ok:
            self.intro_pending = ""  # the turn never started, so a first run stays un-onboarded

    def intro_started(self) -> None:
        """The intro turn began: only now is the home onboarded. A first run also turns tutorial mode on."""
        kind, self.intro_pending = self.intro_pending, ""
        self.onb.mark_onboarded()
        if kind == "first":
            self.run_worker(self.set_tutorial(True, quiet=True), exit_on_error=False)

    async def handoff(self, args: str) -> None:
        """/handoff [path] (D-032): the core gathers the facts, the agent writes the note (skill exo-handoff)."""
        from exo_tui.handoff import handoff_prompt
        resp = await self.post(s.CommandRun(name="handoff_facts", args=args.strip(), session=self.session))
        if not resp.ok:
            return
        facts = (resp.result or {}).get("output")
        if not isinstance(facts, dict):
            self.note("the core returned no handoff facts", "error")
            return
        self.add(Block("/handoff" + (f" {args.strip()}" if args.strip() else ""), "user"))
        self.close_cur()
        await self.post(s.TurnSend(session=self.session, text=handoff_prompt(facts),
                                   mode="steer" if self.busy else "send"))

    async def set_tutorial(self, on: bool, quiet: bool = False) -> None:
        resp = await self.post(s.ConfigSet(key="tutorial.enabled", value=on))
        if not resp.ok:
            return
        self.tutorial = on
        if not quiet:
            self.note("tutorial mode on: short tips appear the first time something comes up" if on
                      else "tutorial mode off")

    async def tutorial_command(self, args: str) -> None:
        word = args.strip().lower()
        if word in ("on", "off"):
            await self.set_tutorial(word == "on")
        elif word == "reset":
            self.onb.reset_hints()
            self.note("tips will show again")
        elif not word:
            left = len(self.hints) - len(self.onb.seen() & set(self.hints))
            self.note(f"tutorial mode is {'on' if self.tutorial else 'off'} ({left} of {len(self.hints)} tips "
                      "not yet shown). /tutorial on, /tutorial off, /tutorial reset")
        else:
            self.note("usage: /tutorial [on|off|reset]", "warn")

    def hint(self, key: str) -> None:
        """Show a tip for this moment: tutorial mode on, never shown before, at most one per turn. Client side
        only; the model never sees it."""
        h = self.hints.get(key)
        if not self.tutorial or not h or self.hint_budget < 1 or key in self.onb.seen():
            return
        self.hint_budget -= 1
        self.onb.mark_seen(key)
        more = f" ({h['then']})" if h.get("then") else ""
        self.note(f"tip: {h['text']}{more}", "hint")

    def copy_to_clipboard(self, text: str) -> None:
        """Textual only writes OSC 52, which macOS Terminal ignores; also hand the text to pbcopy."""
        super().copy_to_clipboard(text)
        import shutil
        import subprocess
        exe = shutil.which("pbcopy")
        if exe:
            try:
                subprocess.run([exe], input=text.encode("utf-8"), timeout=3, check=False)
            except Exception:
                pass

    def last_block(self, kind: str, blocks: list[Block]) -> str:
        """Newest text of a kind: mounted blocks first, then history that is not mounted yet."""
        if text := next((b.text for b in reversed(blocks) if b.kind == kind), ""):
            return text
        if kind == "assistant" and self.last_answer[1] and not self.older:
            return self.last_answer[1]
        return next((e[2] for e in reversed(self.older) if e[0] == "block" and e[1] == kind), "")

    def copy_command(self, what: str) -> None:
        blocks = [w for w in self.transcript.children if isinstance(w, Block)]
        if what in ("", "last"):
            text, label = self.last_block("assistant", blocks), "last answer"
        elif what in ("error", "last-error"):
            text, label = self.last_block("error", blocks), "last error"
        elif what == "all":
            text, label = self.transcript_text(), "transcript"
        else:
            self.note("usage: /copy [last|error|all]", "warn")
            return
        if not text:
            self.note(f"nothing to copy: no {label} yet", "warn")
            return
        self.copy_to_clipboard(text)
        self.note(f"copied {label} ({len(text)} chars) to the clipboard")

    def help_text(self) -> str:
        lines: list[str] = []
        for g in GROUPS:
            lines.append(f"{g}:")
            lines += [f"  /{n}  {d}" for n, (grp, d) in COMMANDS.items() if grp == g]
            if g == "other":
                lines += [f"  /{c['name']}  {c.get('description', '')}" for c in self.commands
                          if c["name"] not in COMMANDS]
        lines += ["copy: drag to select, then ctrl+c; or /copy, /copy error, /copy all",
                  "      (Terminal.app has no OSC 52, so exo also uses pbcopy; iTerm2: hold Option to select natively)",
                  "keys: @path adds a file   !cmd runs a shell command   ctrl+p palette",
                  "      ctrl+o tool output   ctrl+y accept  ctrl+e edit  ctrl+r reject"]
        return "\n".join(lines)

    async def restart(self) -> None:
        from exo_tui import restart as rs
        cmd = rs.restart_command(self.session)
        if not rs.has_opener():
            self.note("no terminal opener found; open a new tab and run: " + " ".join(cmd))
            return
        self.note("restarting in a new tab...")
        rs.schedule_restart(cmd)
        self.exit()

    async def set_default(self, ref: str) -> None:
        if not ref:
            await self.set_model("", default=True)  # same picker as /model, choosing the default
            return
        if ref.startswith("-"):
            self.note("not a model ref; use /default provider:model")
            return
        listed = await self.client.call(s.ModelsList())
        old = [m["route"] for m in listed.get("models", []) if m.get("role") == "main"]
        resp = await self.post(s.ModelsSet(model=ref))  # validates the ref and applies it now
        if not resp.ok:
            return
        routes = [ref, *[r for r in old if r != ref]]
        saved = await self.post(s.ConfigSet(key="models.roles.main", value=routes))
        if saved.ok:
            self.note(f"default model: {ref} (saved to config.json, fallbacks kept)")

    # ------------------------------------------------------------ providers (D-034)
    def open_providers(self, initial: str = "", add: bool = False) -> None:
        if self.modal_open():
            return
        self.push_screen(ProvidersScreen(initial, add), self.providers_done)

    def providers_done(self, result: Any) -> None:
        self.composer.focus()
        if isinstance(result, tuple) and result[0] == "default":
            self.run_worker(self.pick_provider_default(result[1]), exit_on_error=False)
        if self.intro_after_providers:
            self.run_worker(self.intro_when_ready(), exit_on_error=False)

    async def pick_provider_default(self, provider: str) -> None:
        """Choose the default model among one provider's routes, then the same flow as /default."""
        try:
            res = await self.client.call(s.ModelsList())
        except Exception as exc:
            self.note(f"could not list models: {type(exc).__name__}", "error")
            return
        seen, items = set(), []
        for r in [*res.get("models", []), *res.get("available", [])]:
            ref = r["route"]
            if ref not in seen and ref.split(":", 1)[0] == provider:
                seen.add(ref)
                items.append((ref, ref))
        if not items:
            self.note(f"no models listed for {provider} yet (try /providers, then t to test it)", "warn")
            return
        self.push_screen(Palette(items, f"default model: {provider}"),
                         lambda v: v and self.run_worker(self.set_default(v), exit_on_error=False))

    async def first_run_providers(self) -> bool | None:
        """No provider ready: one note says where to start, and the providers panel opens. Returns False when
        none is ready, True when one is, None when the check failed (silent, then treated as ready)."""
        try:
            from exo import providers_setup
            ready = await asyncio.to_thread(providers_setup.any_ready)
        except Exception:
            return None
        if not ready:
            try:
                self.note("No model provider is set up yet. Type /providers to add a key.")
                self.open_providers()
            except Exception:  # the app closed while the check ran
                pass
            return False
        return True

    async def intro_when_ready(self) -> None:
        """The panel closed on a first run: send the waiting /intro once a provider works."""
        try:
            from exo import providers_setup
            ready = await asyncio.to_thread(providers_setup.any_ready)
        except Exception:
            ready = True
        if ready:
            self.intro_after_providers = False
            await self.intro("first")
        else:
            self.note("Still no provider. /providers adds one; /intro then gives the orientation.")

    async def pick_session(self) -> None:
        rows = (await self.client.call(s.SessionList(limit=50)))["sessions"]
        rows = [r for r in rows if r.get("kind") != "subagent"]  # subagent logs open with memory_zoom, not here
        tag = {"private": "private, ", "scheduled": "scheduled, "}
        items = [(f"{r.get('title') or r['id']}  ({tag.get(r.get('kind') or '', '')}{r.get('message_count', 0)} msgs,"
                  f" {r.get('status', '')})", r["id"]) for r in rows]
        self.push_screen(Palette(items, "threads"), lambda v: v and self.run_worker(
            self.load_session(v), exit_on_error=False))

    async def set_model(self, ref: str, default: bool = False) -> None:
        if not ref:
            self.note("loading models...")
            res = await self.client.call(s.ModelsList())
            seen, items = set(), []
            for r in res.get("models", []):  # configured routes first, then everything the providers serve
                if r["route"] not in seen:
                    seen.add(r["route"])
                    items.append((f"{r['route']}  ({r['role']})", r["route"]))
            for r in res.get("available", []):
                if r["route"] not in seen:
                    seen.add(r["route"])
                    items.append((r["route"], r["route"]))
            pick = self.set_default if default else self.set_model
            self.push_screen(Palette(items, "default model (new sessions)" if default else "models"),
                             lambda v: v and self.run_worker(pick(v), exit_on_error=False))
            return
        if ref.startswith("-"):
            self.note("not a model ref; use /model provider:model, or /default provider:model")
            return
        resp = await self.post(s.ModelsSet(model=ref, session=self.session))
        if resp.ok:
            self.model = ref
            self.refresh_status()

    # ------------------------------------------------------------ approvals
    def show_approval(self) -> None:
        if self.card is not None or not self.approvals:
            return
        self.card = ApprovalCard(self.approvals[0])
        self.query_one("#left").mount(self.card, before=self.query_one("#activity"))

    def close_card(self) -> None:
        if self.card is not None:
            self.card.remove()
            self.card = None
        self.show_approval()
        if self.card is None:
            self.query_one(Composer).focus()

    def on_approval_card_answered(self, msg: ApprovalCard.Answered) -> None:
        self.approvals = [a for a in self.approvals if a.request_id != msg.request_id]
        self.note(f"approval: {msg.decision}" + (f" ({msg.note})" if msg.note else ""))
        self.close_card()
        self.run_worker(self.post(s.ApprovalRespond(request_id=msg.request_id, decision=msg.decision,
                                                    note=msg.note, client=CLIENT_ID)), exit_on_error=False)

    # ------------------------------------------------------------ panels
    def on_ui_panel(self, b: s.UiPanel) -> None:
        if b.action == "close":
            if b.panel_id in self.panels and b.panel_id not in self.panel_touched:
                self.respond_panel(b.panel_id, "ignored")
            self.drop_panel(b.panel_id)
            return
        known = b.panel_id in self.panels
        self.panels[b.panel_id] = (b.kind, b.title, b.text, b.reason, b.data)
        if b.panel_id not in self.panel_order:
            self.panel_order.append(b.panel_id)
        self.show_panel(b.panel_id, report=not known)

    def show_panel(self, pid: str, report: bool = False) -> None:
        kind, title, text, _reason, data = self.panels[pid]
        view = self.query_one(PanelView)
        view.show(pid, kind, title, text, data)
        if report:
            self.respond_panel(pid, "opened")
        if view.options:
            view.query_one("#panel-choices").focus()

    def drop_panel(self, pid: str) -> None:
        self.panels.pop(pid, None)
        self.panel_touched.discard(pid)
        if pid in self.panel_order:
            self.panel_order.remove(pid)
        view = self.query_one(PanelView)
        if view.panel_id == pid:
            if self.panel_order:
                self.show_panel(self.panel_order[-1])
            else:
                view.hide()
                self.query_one(Composer).focus()

    def respond_panel(self, pid: str, action: str, value: dict[str, Any] | None = None) -> None:
        if action != "opened":
            self.panel_touched.add(pid)
        self.run_worker(self.post(s.UiRespond(panel_id=pid, action=action, value=value or {})),
                        exit_on_error=False)

    def on_panel_view_chose(self, msg: PanelView.Chose) -> None:
        self.respond_panel(msg.panel_id, "acted", {"choice": msg.choice})
        self.query_one(Composer).focus()

    # ------------------------------------------------------------ actions
    def modal_open(self) -> bool:
        return len(self.screen_stack) > 1

    def action_escape(self) -> None:
        view = self.query_one(PanelView)
        if view.display and view.panel_id:
            pid = view.panel_id
            self.respond_panel(pid, "dismissed")
            self.drop_panel(pid)
        elif self.busy and self.session:
            self.hint("interrupt")
            self.run_worker(self.post(s.TurnInterrupt(session=self.session)), exit_on_error=False)

    def action_toggle_tools(self) -> None:
        if self.modal_open():
            return
        self.expanded = not self.expanded
        for tb in self.tools.values():
            if tb.is_mounted:
                tb.expanded = self.expanded
                tb.flush()

    def action_verdict(self, verdict: str) -> None:
        if self.modal_open() or not self.last_answer[1]:
            return
        self.run_worker(self.send_verdict(verdict, ""), exit_on_error=False)

    def action_edit_answer(self) -> None:
        if self.modal_open() or not self.last_answer[1]:
            return
        self.push_screen(EditScreen(self.last_answer[1]), lambda t: t is not None and self.run_worker(
            self.send_verdict("edit", t), exit_on_error=False))

    async def send_verdict(self, verdict: str, text: str) -> None:
        mid, _ = self.last_answer
        resp = await self.post(s.FeedbackSend(session=self.session, verdict=verdict, message_id=mid, text=text))
        if resp.ok:
            self.note(f"feedback sent: {verdict}")

    def action_palette(self) -> None:
        if self.modal_open():
            return
        items: list[tuple[str, Any]] = [(f"/{n}  {d}", ("slash", n)) for n, d in LOCAL_COMMANDS.items()]
        items += [(f"/{c['name']}  {c.get('description', '')}", ("slash", c["name"])) for c in self.commands]
        items += [("verdict: accept", ("verdict", "accept")), ("verdict: reject", ("verdict", "reject")),
                  ("verdict: edit", ("edit", "")), ("toggle tool output", ("tools", "")),
                  ("reopen last panel", ("slash", "panel"))]

        def chosen(v: Any) -> None:
            if not v:
                return
            kind, arg = v
            if kind == "slash":
                self.run_worker(self.slash(arg, ""), exit_on_error=False)
            elif kind == "verdict":
                self.action_verdict(arg)
            elif kind == "edit":
                self.action_edit_answer()
            elif kind == "tools":
                self.action_toggle_tools()

        self.push_screen(Palette(items, "commands, settings, sessions"), chosen)

    async def on_unmount(self) -> None:
        close = getattr(self.client, "aclose", None)
        if close:
            await close()
