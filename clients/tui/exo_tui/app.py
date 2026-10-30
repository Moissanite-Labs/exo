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
from textual.widgets import Input, OptionList, Static, TextArea
from rich.theme import Theme as RichTheme

from exo.config import exo_home
from exo.core.types import ContentPart, from_dict
from exo.core.trace import money
from exo import complete as cm
from exo.protocol import schema as s
from exo_tui.onboarding import INTRO_PROMPT, INTRO_SKILL, ORIENTATION, ORIENTATION_NOTE, Onboarding, load_catalog
from exo_tui.prefs import LAYOUTS, Prefs
from exo_tui import theme as th
from exo_tui.hero import Hero, hero_rows
from exo_tui.boot import STEPS as BOOT_STEPS
from exo_tui.boot import BootScreen
from exo_tui.providers import ProvidersScreen, refuse_key_in_composer, setup_advice
from exo_tui.widgets import (BUSY_HINT, IDLE_HINT, ApprovalCard, Block, BrandBar, Composer, ComposeHeader, CompletionMenu,
                             EditScreen, Palette, PanelView, PromptMark, StatusStrip, ToolBlock, Welcome)

CLIENT_ID = "exo-tui"
LOST = "connection_lost"  # post's code for a command whose reply never came or that never went out (D-060: not resent)
DEFAULT_WINDOW = 0  # unknown until the core reports this model's capacity
MAX_FILE = 64 * 1024
ROUND_LIMIT_NOTE = ("The core /limits policy may override this window choice. The limit counts tool rounds, not time. A turn can continue after this window closes; "
                    "reopen its thread and use /stop to interrupt it.")
WINDOW = 60  # transcript entries mounted when a session opens; older ones stay as plain data
PAGE = 60  # entries added per "load earlier"
FLUSH_EVERY = 0.1  # streaming repaint interval in seconds (at most 10 a second)
LONG_TURN_MS = 30_000  # the same line as the turn.slow trace rule: wall minus tool time, approval waits and model time
AT_RE = re.compile(r"(?<!\S)@(\S+)")
# name -> (group, description). /help is generated from this table, so every command shows up there.
COMMANDS = {
    "new": ("session", "start a new thread: a fresh focus, same memory; /new private is not remembered"),
    "tab": ("session", "open a new conversation in another terminal window: /tab [directory]"),
    "sessions": ("session", "pick one of this agent's threads to switch to"),
    "resume": ("session", "same as /sessions"),
    "fork": ("session", "fork this session"),
    "rename": ("session", "give this thread a new title: /rename <title>; shows in /sessions"),
    "undo": ("session", "undo the last turn"),
    "clear": ("session", "clear the transcript view (history is kept)"),
    "cwd": ("session", "show this session's working folder"),
    "context": ("session", "show what the next model call carries: each part with its tokens (read-only)"),
    "compact": ("session", "summarize now what is eligible in this agent's memory, then show tokens before and after"),
    "restart": ("session", "reopen this session in a new terminal tab and exit this one"),
    "stop": ("session", "stop the running turn (what was done is kept); /stop <task> stops a running subagent"),
    "limits": ("session", "principal core-wide round and worker deadline controls: /limits [rounds|worker-timeout default|off|number]"),
    "rounds": ("session", "window tool-round choice for new turns: /rounds [default|off|number]"),
    "unsteer": ("session", "pull your last queued steer message back into the composer, if it has not landed yet"),
    "exit": ("session", "quit"),
    "quit": ("session", "same as /exit"),
    "model": ("model", "pick the model for this session; /model <ref> sets it"),
    "default": ("model", "set the default model for new sessions: /default <ref>, or /default alone to pick (same as /model default)"),
    "providers": ("model", "set up model providers: add or replace a key, test it, remove it, pick the default"),
    "usage": ("model", "show context size, cost and the model in use"),
    "panel": ("tools", "reopen the last panel"),
    "approval-authority": ("tools", "status, grant or revoke parent approval authority for active delegated descendants"),
    "tasks": ("tools", "list the subagents this session handed work to, with their status"),
    "open": ("tools", "watch a subagent's session: /open <task> (a task id, a prefix of it, or its session id)"),
    "steer": ("tools", "send a note to a running subagent: /steer <task> <text> (the parent's receipt records it)"),
    "peers": ("tools", "list the other live sessions (Exo and Hermes) you can message"),
    "send": ("tools", "message another session yourself: /send <peer> <text>"),
    "handoff": ("other", "have the agent write a handoff note for the next session: /handoff [path]"),
    "intro": ("other", "an orientation from the agent: what Exo is, what it can do, how to use it"),
    "tutorial": ("other", "tips the first time something comes up: /tutorial on, off or reset (bare shows state)"),
    "copy": ("other", "copy to the clipboard: /copy (last answer), /copy error, /copy all"),
    "compose": ("other", "open the big editor for a long prompt: /compose, /compose task (scaffold), /compose edit ($EDITOR)"),
    "layout": ("other", "inverse puts the input on top and the newest message under it; classic is the usual order: /layout inverse|classic"),
    "help": ("other", "list every command"),
}
LOCAL_COMMANDS = {n: d for n, (_, d) in COMMANDS.items()}
GROUPS = ("session", "model", "tools", "other")
SCAFFOLD = "## Goal\n\n## Context\n\n## Constraints\n\n## Done when\n"  # /compose task
# The core's memory_status state, as the boot line and the empty start say it. Unknown (an older core) says nothing.
MEMORY_WORDS = {"on": "ready", "off": "off, no principal store", "failed": "did not start", "none": "log only"}
MEMORY_SHORT = ("off", "failed")  # no green check on the boot line for these


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
    .peer, .taste, .finding, .report, .intro { margin: 1 0; background: $surface; padding: 0 1;
                                               border-left: outer $cold; }
    #welcome { margin: 1 0; }
    .peer { border-left: double $cold; }
    #delegated-approvals { height: auto; padding: 0 2; color: $warning; }
    #activity { height: 1; color: $fg-3; padding: 0 2; background: $deep; }
    #compose { height: auto; background: $background; border-top: solid $line; padding: 0 2; }
    #prompt { width: auto; height: 1; margin-right: 1; }
    #composer { height: auto; min-height: 1; max-height: 8; border: none; padding: 0; background: $background;
                width: 1fr; }
    #composer:focus { border: none; }
    #composer .text-area--cursor { background: $accent; color: #0a0a0a; text-style: none; }
    #composer .text-area--cursor-line { background: $background; }
    #composer .text-area--placeholder { color: $fg-3; }
    #cheader { display: none; height: 2; padding: 0 2; background: $background; }
    #left.composing #cheader { display: block; }
    #left.composing #transcript { display: none; }
    #left.composing #compose { height: 1fr; }
    #left.composing #composer { height: 1fr; max-height: 100%; }
    #left.composing #composer .text-area--cursor-line { background: $surface; }
    #left.inverse #compose { border-top: none; border-bottom: solid $line; }
    #left.inverse #cmenu { border-top: none; border-bottom: solid $line; }
    #cmenu { height: auto; max-height: 10; background: $background; border-top: solid $line; padding: 0 2; }
    #status { height: 2; padding: 0 2; border-top: solid $line; background: $surface; }
    #status.inverse { border-top: none; border-bottom: solid $line; }
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
        Binding("ctrl+g", "compose", "compose", priority=True),
        Binding("escape", "escape", "esc", show=False),
        Binding("ctrl+c", "ctrl_c", "stop", show=False),
        Binding("ctrl+f", "answer_card", "answer", priority=True, show=False),
        Binding("f6", "load_earlier", "earlier", priority=True, show=False),
    ]

    def __init__(self, client: Any, session: str | None = None, resume: bool = False,
                 seat: str | None = None, title: str = "", home: str | None = None,
                 auto_intro: bool = False, boot_anim: bool = False, provider_check: bool = False,
                 skip_permissions: bool = False, full_access: bool = False, cwd: str | None = None, remote: bool = False,
                 max_rounds: int | None = None) -> None:
        super().__init__()
        if skip_permissions and full_access:
            raise ValueError("Choose bypass or full access, not both")
        self.full_access = full_access
        self.skip_permissions = skip_permissions  # exo --skip-permissions: ask the core for bypass at boot (D-065)
        self.max_rounds = max_rounds
        self.perm: dict[str, Any] = {}  # the core's /permissions json; {} when it has no gate
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
        self.remote = remote
        self.local_cwd = os.getcwd()
        self.new_cwd = cwd if remote else str(Path(cwd or self.local_cwd).expanduser().resolve())
        self.session_cwd = self.new_cwd or ""
        self.model = ""
        self.memory = ""  # the memory plugin's state (a MEMORY_WORDS key; "" unknown), read once at boot
        self.clipboard_verified = False
        self.window = DEFAULT_WINDOW
        self.capacities: dict[str, int] = {}
        self.ctx_tokens = 0
        self.cost: float | None = None  # session USD from priced calls; None = no priced call (D-032: unknown, never 0)
        self.calls_seen = 0  # model calls that used tokens, seen by this client
        self.unpriced = 0  # of those, calls with no price
        self.call_usage = False  # a model.call's usage.updated comes next; one with no model.call is a roll-up
        self.costs: dict[str, tuple[float | None, int, int]] = {}  # (cost, calls_seen, unpriced) of threads left
        self.findings = 0  # open harness findings (D-025), polled after each turn
        self.finding_states = ""  # "3 open, 2 fixed": the same findings by state, for the notice
        self.busy = False
        self.turn_t0 = 0.0  # when the running turn began; 0 once text or reasoning has streamed
        self.expanded = False
        self.ready = asyncio.Event()
        self.cur: Block | None = None
        self.cur_id: str | None = None
        self.last_answer: tuple[str | None, str] = (None, "")
        self.tools: dict[str, ToolBlock] = {}
        self.approvals: list[s.ApprovalRequested] = []
        self.delegated_tasks: dict[str, dict] = {}
        self.delegated_requests: dict[str, dict] = {}
        self.delegated_closed: dict[str, int] = {}
        self._delegated_revision = self._delegated_asked = self._delegated_shown = 0
        self._delegated_stale = False
        self.threadless: set[str] = set()  # requests of no thread (a held peer message): every thread shows them
        self.card: ApprovalCard | None = None
        self._card_transition = asyncio.Lock()  # the shared widget id is reusable only after removal finishes
        self.reconcile_every = 3.0  # how often an open card is checked against the core's own list (D-045)
        self._reconciled = 0.0
        self.pending_steers: list[tuple[str, Block]] = []  # (message id, its transcript line) not yet delivered
        self.sending_messages: dict[Block, set[str]] = {}  # delivery events may arrive before turn.send's reply
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
        self.intro_pending = ""  # "first" or "manual", retained until this intro turn completes
        self.intro_turn = ""
        self.intro_reply = False
        self.sent: list[Any] = []  # every command body this client sent, for tests and debugging
        self._dirty = False
        self.hist = cm.History.for_seat(home or exo_home(), seat)  # up-arrow and ghost text (D-037)
        self.catalog: list[cm.Item] = cm.build_catalog(LOCAL_COMMANDS)
        self.arg_ctx: dict[str, list[cm.Item]] = {}  # cached argument data by kind; fetched once when first needed
        self.arg_at: dict[str, float] = {}
        self.files = cm.FileIndex(os.getcwd(), executor_required=bool(
            getattr(getattr(client, "engine", None), "executor_required", True)))
        self.sug = cm.Suggestion()
        self.sel = 0
        self.sug_text = ""
        self.menu_off = ""  # text the menu was closed with esc for; it stays shut until the text changes
        self.guide = cm.Guide()
        self.tip: cm.Tip | None = None
        self.gstate = cm.State()
        self.prefs = Prefs(home or exo_home())  # saved layout (D-046)
        self.layout = self.prefs.layout()  # "inverse" (input on top, newest first) or "classic"
        self.composing = False  # the big editor pane (D-046)
        self.numbers = False  # line numbers in the pane
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
                yield Static("", id="delegated-approvals")
                yield ComposeHeader()
                with Horizontal(id="compose"):
                    yield PromptMark(PROMPT_IDLE, id="prompt")
                    yield Composer()
                yield CompletionMenu()
            yield PanelView()
        yield StatusStrip()

    def on_mount(self) -> None:
        self.brand, self.strip, self.prompt = self.query_one(BrandBar), self.query_one(StatusStrip), self.query_one(PromptMark)
        self.composer = self.query_one(Composer)  # cached: a modal screen on top hides them from query_one
        self.menu = self.query_one(CompletionMenu)
        self.composer.key_hook = self.composer_key
        self.cheader = self.query_one(ComposeHeader)
        self.apply_layout(self.layout, save=False)
        self.composer.focus()
        self.set_interval(1.0, self.refresh_activity)  # ticks the "waiting for <model>" counter
        self.set_interval(1.0, self.reconcile_approvals)
        self.set_interval(3.0, self.refresh_delegated_approvals)
        self.set_interval(3.0, lambda: self.run_worker(self.load_permissions(), exit_on_error=False))  # another window may switch it
        if self.motion:
            self.set_interval(10.0, self.update_guidance)  # a pause, not an animation; events also call it
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

    def boot_mark(self, step: str, detail: str = "", ok: bool = True) -> None:
        if self.boot_screen is not None:
            self.boot_screen.mark(step, detail, ok)

    def boot_end(self) -> None:
        if self.boot_screen is not None:
            for step, _ in BOOT_STEPS:
                self.boot_screen.mark(step, "-")

    async def boot_over(self) -> None:
        """End the boot sequence and wait until it is off the stack. Its dismiss pops the top screen, so a screen
        pushed while it runs (the first-run model picker or providers panel) would close in its place."""
        self.boot_end()
        if self.boot_screen is not None:
            await self.boot_screen.closed.wait()

    # ------------------------------------------------------------ helpers
    @property
    def transcript(self) -> VerticalScroll:
        return self.query_one("#transcript", VerticalScroll)

    @property
    def inverse(self) -> bool:
        return self.layout == "inverse"

    def chrono(self) -> list[Any]:
        """Transcript widgets oldest first, whichever way the layout stacks them."""
        kids = list(self.transcript.children)
        return kids[::-1] if self.inverse else kids

    def to_latest(self) -> None:
        """Show the newest content: the bottom in classic, the top in inverse. After layout, so a fresh mount counts."""
        tr = self.transcript
        tr.call_after_refresh(tr.scroll_home if self.inverse else tr.scroll_end, animate=False)

    def transcript_text(self) -> str:
        old = [e[2] if e[0] == "block" else f"{e[2]} {e[5]}" for e in self.older]  # history not mounted yet
        return "\n".join(old + [w.text if isinstance(w, Block) else f"{w.tool_name} {w.output}"
                                for w in self.chrono() if isinstance(w, (Block, ToolBlock))])

    async def full_permissions_args(self, all_sessions: bool = False) -> str:
        sid = "all" if all_sessions else self.session
        if not sid:
            raise ValueError("Open a thread before enabling full permissions")
        status = (await self.client.call(s.CommandRun(name="permissions", args="json"))).get("output")
        if not isinstance(status, dict) or status.get("supports_full") is not True:
            raise ValueError("This core does not support session-scoped full permissions")
        if status.get("full_available") is False:
            raise ValueError("Full permissions are unavailable with the required tool executor")
        if all_sessions:
            if status.get("supports_full_all") is not True:
                raise ValueError("This core does not support full permissions for all sessions")
            return "full all"
        snapshot = await self.client.call(s.SessionGet(session=sid))
        if (snapshot.get("session") or {}).get("id") != sid or self.session != sid:
            raise ValueError("The selected thread changed; retry full permissions")
        return "full " + sid

    @property
    def bypass(self) -> bool:
        return self.perm.get("mode") == "bypass"

    async def load_permissions(self) -> None:
        """The core's permission mode for the banner. An older core or one without the gate leaves it empty."""
        try:
            out = (await self.client.call(s.CommandRun(name="permissions", args="json"))).get("output")
        except Exception:
            out = None
        perm = out if isinstance(out, dict) else {}
        if perm != self.perm:
            self.perm = perm
            self.refresh_status()

    def status_text(self) -> str:
        """The status strip as plain text (the strip itself draws instruments from the same numbers)."""
        pct = str(min(999, round(100 * self.ctx_tokens / self.window))) + "%" if self.window else "capacity unknown"
        bug = f" · {self.findings} findings" if self.findings else ""
        cache = "" if self.cache_share is None else f" · cache {round(100 * self.cache_share)}%"
        perm = f"permissions: bypass ({self.perm.get('auto_approved', 0)} auto-approved) · " if self.bypass else ""
        if self.perm.get("mode") == "full":
            perm = ("permissions: full for all sessions (including delegates) · " if self.perm.get("full_session") == "*" else "permissions: full only " + str(self.perm.get("full_session")) + " (all tool calls; other threads ask) · ")
        rounds = "" if self.max_rounds is None else f"window rounds: {'unlimited' if self.max_rounds == 0 else self.max_rounds} · "
        return f"{perm}{rounds}{self.seat or 'main'} · {self.model or '-'} · ctx {pct}{cache} · {self.cost_label()}{bug}"

    def cost_label(self) -> str:
        """The D-032 rule, formatted by the same `money` /trace uses: "-" before any call, "unknown" when no call is
        priced, "$x+" when some are not, never "$0.00" for an unpriced call."""
        if self.cost is None:
            return "unknown" if self.calls_seen else "\u2013"
        return money(self.cost, self.unpriced)

    def refresh_status(self) -> None:
        self.strip.set(model=self.model, requested=self.requested, ctx=self.ctx_tokens,
                                        window=self.window, cache=self.cache_share, cost=self.cost_label(),
                                        findings=self.findings, bypass=self.bypass,
                                        full_session=self.perm.get("full_session") if self.perm.get("mode") == "full" else None,
                                        auto=int(self.perm.get("auto_approved") or 0), rounds=self.max_rounds)
        self.brand.set(self.seat or "main", self.thread_label, self.busy)
        self.prompt.set_state(self.busy, bool(self.question))
        self.composer.placeholder = BUSY_HINT if self.busy else self.tip_line() or IDLE_HINT

    def wire(self, width: int = 9) -> Text:
        """A packet travelling along a wire: data in flight. Still under reduced motion."""
        pos = (self.frame // 2) % width if self.motion else width // 2
        t = Text(no_wrap=True)
        for k in range(width):
            t.append("\u25cf" if k == pos else "\u2500", style=th.rich_style("accent" if k == pos else "line2"))
        return t

    def refresh_activity(self) -> None:
        bits = []
        if self.pending_steers:
            n = len(self.pending_steers)
            bits.append(f"{n} steer{'s' if n != 1 else ''} queued (Up to edit)")
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
        tr = self.transcript
        if self.inverse and tr.children:  # newest first: the batch goes in above everything, last of it on top
            tr.mount(*widgets[::-1], before=0)
        elif self.inverse:
            tr.mount(*widgets[::-1])
        else:
            tr.mount(*widgets)
        self.to_latest()

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
            self.to_latest()

    # ------------------------------------------------------------ boot and events
    async def boot(self) -> None:
        hello: dict[str, Any] | None = None
        try:
            hello = await self.client.call(s.Hello(client=CLIENT_ID, capabilities=["markdown", "diff"],
                                                   screen_cols=self.size.width or None))
            self.boot_mark("core", "connected")
            try:
                for m in (await self.client.call(s.ModelsList())).get("models", []):
                    self.capacities[m.get("route", "")] = int(m.get("context") or 0)
                    if m.get("role") == "main":
                        self.model = self.model or m.get("route", "")
                self.window = self.capacities.get(self.model, 0)
                self.boot_mark("model", self.model)
                self.commands = (await self.client.call(s.CommandsList())).get("commands", [])
                self.catalog = cm.build_catalog(LOCAL_COMMANDS, self.commands)  # the core's list, once per session
            except Exception:
                pass
            try:
                self.tutorial = (await self.client.call(s.ConfigGet(key="tutorial.enabled"))).get("value") is True
            except Exception:
                self.tutorial = False  # not set yet
            try:  # the guidance line is on unless the user turned it off (/tutorial off, or this key)
                self.guide.enabled = (await self.client.call(s.ConfigGet(key="guidance.enabled"))).get("value") is not False
            except Exception:
                pass
            self.gstate.first_run = not self.onb.onboarded
            self.update_guidance()
            try:  # the plugin's real state, not an assumed "ready" (the core says why in a notice)
                st = (await self.client.call(s.CommandRun(name="memory_status"))).get("output") or {}
                self.memory = str(st.get("state") or "") if isinstance(st, dict) else ""
            except Exception:
                self.memory = ""
            if self.resume and not self.session:
                rows = (await self.client.call(s.SessionList(limit=50, seat=self.seat)))["sessions"]
                # a thread, not a child's log, with something in it: every launch opens one, used or not
                rows = [r for r in rows if r.get("kind") != "subagent" and r.get("message_count")]
                self.session = rows[0]["id"] if rows else None
            if self.session:
                await self.resume_or_new(self.session)
            else:
                await self.new_session(announce=False)
            if self.skip_permissions:  # the launch flag is this principal window sending /permissions bypass (D-065)
                try:
                    out = (await self.client.call(s.CommandRun(name="permissions", args="bypass launch"))).get("output")
                    self.note(out if isinstance(out, str) else "permissions: bypass")
                except Exception as exc:
                    self.note(f"--skip-permissions: {getattr(exc, 'message', None) or exc}", "error")
            if self.full_access:
                try:
                    args = await self.full_permissions_args()
                    out = (await self.client.call(s.CommandRun(name="permissions", args=args, session=self.session))).get("output")
                    self.note(out if isinstance(out, str) else "permissions: full only " + self.session)
                except Exception as exc:
                    self.note(f"--full-access: {getattr(exc, 'message', None) or exc}", "error")
            if self.max_rounds is not None:
                self.note(ROUND_LIMIT_NOTE, "warn")
            await self.load_permissions()
            self.boot_mark("agent", self.seat or "main")
            self.boot_mark("memory", MEMORY_WORDS.get(self.memory, ""), ok=self.memory not in MEMORY_SHORT)
            self.run_worker(self.pump(hello.get("seq", 0)), exit_on_error=False)
            await self.boot_over()  # the provider check below may push a screen
            want_intro = self.auto_intro and not self.resume and self.onb.intro_due
            # A first run with no working provider goes to /providers first; the intro waits for a key,
            # because the orientation is an agent turn and would only fail without one.
            ready = await self.first_run_providers() if self.provider_check else True
            if want_intro and ready is False:
                self.intro_after_providers = True
            elif want_intro:
                await self.intro("first")
        except Exception as exc:
            self.note(f"cannot start: {type(exc).__name__}: {exc}", "error")
            if hello is not None and not any(w.name == "pump" for w in self.workers):
                # whatever failed after hello, the window can still /new and send: replies and approvals must show
                self.run_worker(self.pump(hello.get("seq", 0)), exit_on_error=False)
        self.boot_end()
        self.refresh_status()
        self.ready.set()

    async def pump(self, since: int) -> None:
        try:
            async for ev in self.client.events(since=since, reconnect=True):
                try:
                    self.observe_delegated(ev)
                    if ev.session not in (None, self.session):
                        continue
                    self.handle(ev)
                except Exception as exc:  # a bad event must not stop the feed, and the harness is told (D-025)
                    self.note(f"event {ev.type}: {type(exc).__name__}: {exc}", "error")
                    if ev.type != "harness.error":  # never report the report of an error
                        self.report_error(f"tui event {ev.type}", exc)
        except Exception as exc:  # the feed itself died: say so, never go quiet (D-025)
            self.note(f"event feed stopped: {type(exc).__name__}: {exc}. /restart reopens this thread.", "error")
            self.report_error("tui event feed", exc)

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
            by = (out.get("by_state") or {}) if isinstance(out, dict) else {}
            self.finding_states = ", ".join(f"{by[k]} {k}" for k in ("open", "fixed", "verified", "reopened") if by.get(k))
        except Exception:
            return
        if self.findings > before:
            self.close_cur()
            self.add(Block(f"{self.findings} open", "finding", head=f"{self.findings} open",
                           body="The harness noticed something odd and filed it." + (f" Now: {self.finding_states}." if self.finding_states else ""), meta=f"/findings {th.DOT} /trace"))
            self.hint("finding")
        self.refresh_status()

    @staticmethod
    def cache_of(u: Any) -> float | None:
        total = u.input_tokens + u.cache_read_tokens + u.cache_write_tokens  # the whole prompt (D-080)
        return u.cache_read_tokens / total if total else None

    def handle(self, ev: s.Event) -> None:
        b = ev.body
        if isinstance(b, s.TurnStarted):
            self.busy = True
            self.turn_t0 = time.monotonic()
            self.model = b.model or self.model
            self.window = self.capacities.get(self.model, 0)
            self.requested, self.served = self.model, ""  # what this turn asked for; ModelCall says what served
            self.close_cur()
        elif isinstance(b, s.TurnCompleted):
            if self.intro_turn and b.turn_id == self.intro_turn:
                self.intro_completed(b.stop_reason)
            self.gstate.turns += 1
            self.gstate.long_turn = (b.timing.get("wall_ms", 0) - b.timing.get("tool_ms", 0)
                                     - b.timing.get("approval_ms", 0)) >= LONG_TURN_MS
            self.busy = False
            self.turn_t0 = 0.0
            self.running_tools = 0
            self.close_cur()
            self.hint_budget = 1
            if (b.timing.get("wall_ms", 0) - b.timing.get("tool_ms", 0) - b.timing.get("approval_ms", 0)
                    - b.timing.get("model_ms", 0)) >= LONG_TURN_MS:
                self.hint("long_turn")
            self.run_worker(self.refresh_findings(), exit_on_error=False)
            self.update_guidance()
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
            if (self.intro_pending and m.role == "user" and m.text() == INTRO_PROMPT
                    and m.meta.get("author") == "principal"):
                self.intro_turn = m.meta.get("turn_id") or ""
                self.intro_reply = False
                if self.intro_pending == "first":  # it started: a failure or no reply does not resend it every launch
                    self.onb.mark_intro_started()
            elif self.intro_turn and m.role == "assistant" and m.meta.get("turn_id") == self.intro_turn:
                route = str(m.meta.get("model") or "")
                provider, sep, model = route.partition(":")
                self.intro_reply = bool(m.text().strip() and sep and model and provider and provider != "fake"
                                        and not m.tool_calls and not m.meta.get("interrupted") and not m.meta.get("error"))
            if m.role == "user" and m.id:
                self.steer_landed(m.id)
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
                        self.to_latest()
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
            else:
                self.update_older_tool(b.call_id, b.text, append=True)
        elif isinstance(b, s.ToolCompleted):
            self.running_tools = max(0, self.running_tools - 1)
            if tb := self.tools.get(b.result.call_id):
                tb.output = b.result.text() or tb.output
                tb.finish("error" if b.result.is_error else "done", b.duration_ms / 1000 if b.duration_ms else None)
            else:
                self.update_older_tool(b.result.call_id, b.result.text(), status="error" if b.result.is_error else "done")
        elif isinstance(b, s.ApprovalRequested):
            if ev.session is None:  # no live session could take it (D-026): the card stays across thread switches
                self.threadless.add(b.request_id)
            self.approvals.append(b)
            self.show_approval()
            self.hint("approval")
        elif isinstance(b, s.ApprovalResolved):
            self.approvals = [a for a in self.approvals if a.request_id != b.request_id]
            self.threadless.discard(b.request_id)
            if self.card is not None and self.card.req.request_id == b.request_id:
                self.run_worker(self.close_card(b.request_id), exit_on_error=False)
        elif isinstance(b, s.SteerCancelled):
            if self.intro_pending and b.text == INTRO_PROMPT:
                self.intro_pending, self.intro_turn, self.intro_reply = "", "", False
            self.drop_pending_steer(b.message_id)
        elif isinstance(b, s.QuestionAsked):
            self.question = b.question_id
            opts = f" [{' / '.join(b.options)}]" if b.options else ""
            self.note(f"question: {b.text}{opts}  (type your answer)")
        elif isinstance(b, s.UsageUpdated):
            u, own, self.call_usage = b.usage, self.call_usage, False
            if b.session_total and b.session_total.cost_usd is not None:
                self.cost = b.session_total.cost_usd
            if not own:  # no model.call before it: a finished subagent's total, not this thread's context or model
                if u.cost_usd is None and (u.input_tokens or u.output_tokens or u.cache_read_tokens
                                           or u.cache_write_tokens):  # the child's calls had no price (D-032)
                    self.calls_seen, self.unpriced = self.calls_seen + 1, self.unpriced + 1
            else:
                self.ctx_tokens = u.input_tokens + u.cache_read_tokens + u.cache_write_tokens
                self.model = self.served or b.model or self.model  # a fallback that served stays visible
                if self.cache_share is None:
                    self.cache_share = self.cache_of(u)
        elif isinstance(b, s.ModelCall):
            self.gstate.provider_error = bool(b.error)
            self.call_usage = b.usage is not None  # the loop sends this call's usage.updated right after it
            if b.route:
                self.model = self.served = b.route  # the route that served, not the one asked for
                self.window = self.capacities.get(b.route, 0)
            if b.requested:
                self.requested = b.requested
            if b.usage is not None and (b.usage.input_tokens or b.usage.output_tokens or b.usage.cache_read_tokens
                                        or b.usage.cache_write_tokens):
                self.calls_seen += 1
                self.unpriced += b.usage.cost_usd is None
            if b.usage is not None and (share := self.cache_of(b.usage)) is not None:
                self.cache_share = share
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
            if b.state == "done":
                self.gstate.job_done = b.job_id
                self.update_guidance()
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

    async def leave_thread(self, to: str = "") -> None:
        """Before another thread opens: the pump drops this one's events from now on, so a turn.completed or
        approval.resolved for it would never arrive. Its turn state, card, queued steers and numbers go now. Its COST
        counts wait for a return to it: the core's session_total keeps counting the whole thread, so the '+' for an
        unpriced call must stay with it (D-032). `to` gets back what this window last saw of it, if anything.
        A request of no thread stays: it is every thread's."""
        async with self._card_transition:
            if self.card is not None and self.card.req.request_id not in self.threadless:
                await self.card.remove()  # keep its slot until gone; close/restore use the same transition
                self.card = None
            self.approvals = [a for a in self.approvals if a.request_id in self.threadless]
        self.busy, self.turn_t0, self.running_tools, self.cur_id, self.question = False, 0.0, 0, None, None
        self.pending_steers.clear()
        self.sending_messages.clear()
        self.agents.clear()
        if self.session:
            self.costs[self.session] = (self.cost, self.calls_seen, self.unpriced)
        (self.cost, self.calls_seen, self.unpriced), self.call_usage = self.costs.pop(to, (None, 0, 0)), False
        self.ctx_tokens, self.cache_share, self.served, self.requested = 0, None, "", ""
        self.refresh_activity()

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
                    out.append(("tool", tc["id"], tc["name"], tc.get("arguments"), "running", ""))
            elif m["role"] == "tool":
                for r in m.get("tool_results", []):
                    if (n := by_id.get(r["call_id"])) is not None:
                        out[n] = out[n][:4] + ("error" if r.get("is_error") else "done",
                                               "".join(p.get("text", "") for p in r.get("content", [])))
        return out

    def update_older_tool(self, call_id: str, text: str, *, append: bool = False, status: str = "") -> None:
        """Live tools beyond the mounted history window still update the stored transcript."""
        for i, entry in enumerate(self.older):
            if entry[0] == "tool" and entry[1] == call_id:
                output = entry[5] + text if append else text or entry[5]
                self.older[i] = entry[:4] + (status or entry[4], output)
                return

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
            if self.inverse:  # the oldest end is the bottom
                self.transcript.mount(self.earlier_row)
            else:
                self.transcript.mount(self.earlier_row, before=0)
        if self.earlier_row is not None:
            if n:
                where = "bottom" if self.inverse else "top"
                self.earlier_row.update(Text(f"{n} earlier message{'s' if n != 1 else ''}. "
                                             f"Press F6 or scroll to the {where} to load more.", style="dim italic"))
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
            if self.inverse:  # older goes below, newest of the page first; the reader's place does not move
                await tr.mount(*widgets[::-1], before=self.earlier_row) if self.earlier_row is not None \
                    else await tr.mount(*widgets[::-1])
                self.update_earlier_row()
                return
            anchor = next((w for w in tr.children if w is not self.earlier_row), None)
            await tr.mount(*widgets, before=anchor if anchor is not None else 0)
            self.update_earlier_row()
            if anchor is not None:  # keep what the reader was looking at in place
                tr.call_after_refresh(tr.scroll_to_widget, anchor, animate=False, top=True)
        finally:
            self._loading = False

    def watch_transcript_scroll(self, old: float, new: float) -> None:
        if not self.older:
            return
        if self.inverse:  # the oldest end is the bottom
            end = self.transcript.max_scroll_y
            if end > 0 and old < end - 1 <= new:
                self.action_load_earlier()
        elif new <= 0 < old:
            self.action_load_earlier()

    @staticmethod
    def label_of(info: dict[str, Any]) -> str:
        name = info.get("title") or str(info.get("id", ""))[-8:]
        return f"thread {name}" + (" (private)" if info.get("kind") == "private" else "")

    def set_session_cwd(self, cwd: str | None) -> None:
        self.session_cwd = cwd or ""
        root = self.local_cwd if self.remote else self.session_cwd
        if root and self.files.cwd != Path(root):
            self.files = cm.FileIndex(root, executor_required=self.files.executor_required)

    async def load_session(self, sid: str, resume: bool = True) -> None:
        got = await self.client.call(s.SessionResume(session=sid) if resume else s.SessionGet(session=sid))
        if sid != self.session:
            await self.leave_thread(sid)
        self.session = sid
        self.draw_delegated_approvals()
        info = got["session"]
        self.set_session_cwd(info.get("cwd"))
        self.busy = info.get("status") == "live"  # a turn already running there: LIVE, steer, ctrl+c stops it
        self.seat = self.seat or info.get("seat")
        self.model = info.get("model") or self.model
        self.window = self.capacities.get(self.model, 0)
        self.thread_label = self.label_of(info)
        self.clear_transcript()
        self.render_history(got.get("messages", []))
        n = sum(1 for m in got.get("messages", []) if not (m.get("meta") or {}).get("note"))
        title = info.get("title") or ""
        what = "private thread" if info.get("kind") == "private" else "thread"
        self.note(f"resumed {what} {title + ' ' if title else ''}({sid}, {n} messages)", "note")
        await self.restore_approvals()  # a request that waited before this window showed the thread (D-045)
        self.refresh_status()

    async def resume_or_new(self, sid: str) -> None:
        """Resume thread ``sid``; an id the core does not know (a typo, a deleted thread) opens a new thread."""
        try:
            await self.load_session(sid)
        except Exception as exc:
            if getattr(exc, "code", None) != "not_found":
                raise
            await self.new_session(announce=True)
            self.note(f"no thread {sid}; started a new one", "warn")  # after new_session, which clears the transcript

    async def new_session(self, kind: str = "", announce: bool = True) -> None:
        """A new thread of the same agent: a fresh focus with the same memory (D-028)."""
        got = await self.client.call(s.SessionCreate(title=self.title_arg, seat=self.seat, kind=kind, cwd=self.new_cwd))
        await self.leave_thread()
        self.session = got["session"]["id"]
        self.draw_delegated_approvals()
        self.set_session_cwd(got["session"].get("cwd"))
        self.model = got["session"].get("model") or self.model
        self.window = self.capacities.get(self.model, 0)
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
            self.transcript.mount(Welcome(self.seat or "main", self.model, MEMORY_WORDS.get(self.memory, "")))
        await self.restore_approvals()  # a request of no thread shows in a new thread too (D-026)
        self.refresh_status()

    # ------------------------------------------------------------ sending
    async def post(self, body: Any) -> s.Response:
        import httpx

        self.sent.append(body)
        try:
            resp = await self.client.send(body)
        except (httpx.TransportError, OSError) as exc:  # the request may have reached the core: say so, never resend
            if isinstance(exc, httpx.ConnectError):  # no connection, so no byte went out; a refused peer check says why
                self.note(f"not sent: {str(exc) or 'no connection to the core'}", "error")
            else:
                self.note("connection lost; this may not have been delivered. Check before you send it again.", "error")
            if isinstance(body, s.ApprovalRespond):
                self.run_worker(self.restore_approvals(), exit_on_error=False)
            return s.Response(id="", ok=False, error=s.ErrorInfo(code=LOST, message=str(exc)))
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
            self.hist.add(text)
            self.sent_line(text)
            self.run_worker(self.submit(text), exit_on_error=False)
            if self.composing:
                self.close_compose()

    # ------------------------------------------------------------ completion and guidance (D-037)
    def composer_key(self, key: str) -> bool:
        """First look at a composer key: the dropdown, history and ghost text. True means the key was used."""
        c = self.composer
        if c.text != self.sug_text:  # a paste or a fast typist: the Changed message has not been handled yet
            self.refresh_suggest()
        if key in ("right", "end") and c.ghost and self.ghost_ok and c.at_end():
            self.put(c.text + c.ghost)
            return True
        if key == "escape" and c.ghost and self.ghost_ok:  # esc dismisses ghost text first
            c.ghost, self.ghost_ok = "", False
            c.refresh()
            return True
        items = self.sug.items
        shown = bool(items) and self.sug.mode in ("command", "arg", "file") and self.menu_off != c.text
        if shown:
            if key in ("down", "up"):
                self.sel = (self.sel + (1 if key == "down" else -1)) % len(items)
                self.draw_menu()
                return True
            if key == "tab":
                self.accept(items[self.sel])
                return True
            if key == "enter":
                if cm.complete_text(c.text, self.sug, items[self.sel]).strip() == c.text.strip():
                    return False  # already complete: send it
                self.accept(items[self.sel])
                return True
            if key == "escape":
                self.menu_off = c.text
                self.draw_menu()
                return True
            return False
        if self.composing:  # in the big editor up and down only move the caret; history would eat the draft
            if key == "escape":  # after the menu and ghost text: close the pane, the draft stays in the composer
                self.close_compose()
                return True
            return False
        row, last = c.cursor_location[0], c.document.line_count - 1
        if key == "up" and not c.text and self.pending_steers:  # D-045: up pulls the queued steer back to edit
            self.run_worker(self.unsteer(), exit_on_error=False)
            return True
        if key == "up" and row == 0:
            v = self.hist.prev(c.text)
            if v is not None:
                self.put(v)
            return True
        if key == "down" and self.hist.browsing and row == last:
            self.put(self.hist.next() or "")
            return True
        if key == "tab":
            if c.ghost and self.ghost_ok and c.at_end():
                self.put(c.text + c.ghost)
                return True
            if not c.text and self.tip is not None and self.tip.fill:
                self.put(self.tip.fill)
                return True
        return False

    ghost_ok = False

    def put(self, text: str) -> None:
        """Set the composer text from code (history, completion), caret at the end."""
        self._expect = text
        self.composer.text = text
        self.composer.move_cursor(self.composer.document.end)

    _expect = ""

    def accept(self, item: cm.Item) -> None:
        self.put(cm.complete_text(self.composer.text, self.sug, item))

    def on_text_area_changed(self, event: Any) -> None:
        if event.text_area is not self.composer:
            return
        if self.composer.text != self._expect:
            self.hist.reset()  # the user edited a recalled line: it is a draft now
        self.refresh_suggest()
        if self.composing:
            self.cheader.show(self.composer, self.numbers)

    def expire_ctx(self) -> None:
        now = time.monotonic()
        for k in [k for k, t0 in self.arg_at.items() if now - t0 > 30]:
            self.arg_ctx.pop(k, None)
            self.arg_at.pop(k, None)

    def refresh_suggest(self) -> None:
        c = self.composer
        self.expire_ctx()
        if self.hist.browsing or not c.at_end():
            self.sug = cm.Suggestion()
        else:
            self.sug = cm.suggest(c.text, self.catalog, self.arg_ctx,
                                  self.files if self.remote or self.session_cwd else None)
        if self.sug.need and self.sug.need not in self.arg_at:
            self.arg_at[self.sug.need] = time.monotonic()
            self.run_worker(self.fetch_ctx(self.sug.need), exit_on_error=False)
        if self.sug.mode == "command" and self.sug.items and c.text == "/":
            self.hint("slash_menu")  # tutorial mode: the first time the menu opens, say what it is
        self.sel, self.sug_text = 0, c.text
        c.ghost, self.ghost_ok = ("", False) if self.hist.browsing else cm.ghost(c.text, self.sug, self.hist)
        c.refresh()
        self.draw_menu()

    def draw_menu(self) -> None:
        sug, text = self.sug, self.composer.text
        if sug.mode == "shell":
            self.menu.show([], 0, sug.hint)
        elif sug.items and sug.mode in ("command", "arg", "file") and self.menu_off != text:
            if sug.mode == "command":
                rows = [(it.label, it.args, cm.short_desc(it.meta)) for it in sug.items]
            else:
                rows = [(it.label, "", cm.short_desc(it.meta)) for it in sug.items]
            self.menu.show(rows, self.sel)
        else:
            self.menu.show([], 0)

    async def fetch_ctx(self, kind: str) -> None:
        """Load one kind of argument data (once, then cached for 30 seconds). Failures leave the list empty."""
        items: list[cm.Item] = []
        try:
            if kind == "models":
                res = await self.client.call(s.ModelsList())  # the picker's source: unsupported models are already left out
                seen: set[str] = set()
                for r in res.get("models", []):
                    if r["route"] not in seen:
                        seen.add(r["route"])
                        items.append(cm.Item(r["route"], r["route"], f"role {r['role']}"))
                for r in res.get("available", []):
                    if r["route"] not in seen:
                        seen.add(r["route"])
                        items.append(cm.Item(r["route"], r["route"]))
            elif kind == "threads":
                rows = (await self.client.call(s.SessionList(limit=30))).get("sessions", [])
                items = [cm.Item(r["id"], r.get("title") or r["id"], f"{r.get('message_count', 0)} msgs  {r['id']}")
                         for r in rows if r.get("kind") != "subagent"]
            elif kind == "grants":
                out = (await self.client.call(s.CommandRun(name="grants", args="list", session=self.session))).get("output")
                for line in str(out or "").splitlines():
                    gid, _, rest = line.partition("  ")
                    if gid and not gid.startswith("no "):
                        items.append(cm.Item(gid, gid, rest.strip()))
            elif kind == "providers":
                from exo import providers_setup as ps
                items = [cm.Item(r["provider"], r["provider"], str(r.get("status") or ""))
                         for r in await asyncio.to_thread(ps.rows)]
            elif kind == "handoff_path":
                out = (await self.client.call(s.CommandRun(name="handoff_facts", args="", session=self.session))).get("output")
                if isinstance(out, dict) and out.get("path"):
                    items = [cm.Item(str(out["path"]), str(out["path"]), "default path")]
            elif kind == "peers":
                res = await self.client.call(s.Peers(action="list", session=self.session))
                items = [cm.Item(r["name"], r["name"], f"{r.get('platform', '')} {r.get('title') or ''}".strip())
                         for r in res.get("peers", [])]
        except Exception:
            items = []
        self.arg_ctx[kind] = items
        self.arg_at[kind] = time.monotonic()
        self.refresh_suggest()

    def sent_line(self, text: str) -> None:
        """Bookkeeping for a line the user just sent: the tip is spent, the rule it came from retires."""
        self.tip = None
        if text.startswith("/"):
            self.guide.used(text[1:].split(" ", 1)[0])
            if text.startswith("/providers"):
                self.gstate.provider_error = False
        if "job_output" in text or self.gstate.job_done and self.gstate.job_done in text:
            self.gstate.job_done = ""
        self.refresh_status()

    def tip_line(self) -> str:
        if self.tip is None:
            return ""
        return self.tip.text + (f"  {th.DOT} tab fills it in" if self.tip.fill else "")

    def update_guidance(self) -> None:
        """Pick at most one dim line for the empty composer from local rules (no model call, rate limited)."""
        if self.tip is not None or not hasattr(self, "composer"):
            return
        st = self.gstate
        st.busy, st.findings = self.busy, self.findings
        if st.first_run and (self.onb.onboarded or self.intro_pending or st.turns):
            st.first_run = False
        tip = self.guide.pick(st)
        if tip is not None:
            self.tip = tip
            st.long_turn = st.long_turn and tip.key != "trace"
            if tip.key.startswith("job:"):
                st.job_done = ""
            self.refresh_status()

    def file_parts(self, text: str) -> list[ContentPart]:
        parts, seen = [], set()
        for m in AT_RE.finditer(text):
            rel = m.group(1).rstrip(".,;:)")
            p = Path(rel).expanduser()
            if not p.is_absolute():
                if not self.remote and not self.session_cwd:
                    continue  # an older core has not supplied an effective directory
                p = self.files.cwd / p
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
        parts = text[1:].split(maxsplit=1) if text.startswith("/") else []
        name, args = (parts[0], parts[1] if len(parts) > 1 else "") if parts else ("", "")
        known = set(LOCAL_COMMANDS) | {c["name"] for c in self.commands}
        # Only simple command names are eligible for typo help; /etc/hosts and paths are literal text.
        command = name in known or (bool(re.fullmatch(r"[a-z][a-z-]*", name)) and cm.closest(name, known))
        if self.question and not command:
            qid, self.question = self.question, None
            self.add(Block(text, "user"))
            resp = await self.post(s.QuestionAnswer(question_id=qid, answer=text))
            if resp.error is not None and resp.error.code == LOST:
                self.question = qid  # still open as far as this window knows
                self.keep_text(text)
            return
        if command:
            await self.slash(name, args.strip())
            return
        shown, send = text, text
        if text.startswith("!"):
            cmd = text[1:].strip()
            send = f"Run this shell command with your shell tool and show me the output:\n{cmd}"
        steering = self.busy
        block = Block(shown, "user", steer=steering)
        self.add(block)
        self.hint("steer" if steering else "first_message")
        self.close_cur()
        received: set[str] = set()
        session = self.session
        self.sending_messages[block] = received
        try:
            resp = await self.post(s.TurnSend(session=session, text=send, parts=self.file_parts(text),
                                              mode="steer" if steering else "send",
                                              max_rounds=None if steering else self.max_rounds))
        finally:
            self.sending_messages.pop(block, None)
        if self.session != session:
            return  # a late reply belongs to the thread left behind, not this composer's draft or queue
        if resp.error is not None and resp.error.code == LOST:
            self.keep_text(text)
        if resp.ok and (steering or resp.result.get("steered")) and (mid := resp.result.get("message_id")):
            block.steer = True
            block.pending = mid not in received
            if block.pending:  # queued, not delivered: it can be pulled back
                self.pending_steers.append((mid, block))
            else:
                block.steer_state = "delivered"
        elif resp.ok:
            block.steer = False  # the turn finished meanwhile; the core accepted an ordinary new turn
        elif steering:
            block.steer_state = "delivery unknown" if resp.error and resp.error.code == LOST else "not sent"
        block.flush()
        self.refresh_activity()

    def keep_text(self, text: str) -> None:
        """A message whose delivery is unknown goes back in an empty composer, to send again or drop."""
        if not self.composer.text:
            self.put(text)

    def steer_landed(self, message_id: str) -> None:
        """The steer reached the conversation (message.completed): it is no longer pending."""
        for received in self.sending_messages.values():
            received.add(message_id)
        for mid, block in self.pending_steers:
            if mid == message_id:
                self.pending_steers.remove((mid, block))
                block.pending = False
                block.steer_state = "delivered"
                if block.is_mounted:
                    block.flush()
                self.refresh_activity()
                return

    def drop_pending_steer(self, message_id: str) -> None:
        for mid, block in self.pending_steers:
            if mid == message_id:
                self.pending_steers.remove((mid, block))
                block.remove()
                self.refresh_activity()
                return

    async def unsteer(self) -> bool:
        """Pull the newest queued steer message back into the composer (D-045). Never stops the turn."""
        if not self.session:
            return False
        if not self.pending_steers:
            self.note("nothing queued to pull back: a steer lands at the next tool round", "hint")
            return False
        mid, block = self.pending_steers[-1]
        session = self.session
        resp = await self.post(s.CommandRun(name="unsteer", args=mid, session=session))
        if self.session != session:
            return False  # the old thread's pulled-back text must never enter the new composer's draft
        if resp.error is not None and resp.error.code == LOST:
            return False  # unknown whether it was pulled back: it stays shown as pending
        if not resp.ok:
            return False
        gone = (resp.result.get("output") or {}).get("cancelled", []) if resp.ok else []
        if not gone:  # it landed while we asked
            self.steer_landed(mid)
            if resp.ok:
                self.note("nothing queued to pull back: that steer already landed", "hint")
            return False
        self.drop_pending_steer(mid)
        text = block.text
        cur = self.composer.text
        self.put((text + "\n" + cur) if cur.strip() else text)
        self.composer.focus()
        return True

    async def subagent(self, verb: str, args: str) -> None:
        """/tasks, /open, /steer and /stop <task>: the core's ``subagent`` built-in (#97). The core decides who may act."""
        if verb != "list" and not args.strip():
            self.note({"open": "usage: /open <task>", "send": "usage: /steer <task> <text>",
                       "stop": "usage: /stop <task>"}[verb] + "  (see /tasks)", "warn")
            return
        resp = await self.post(s.CommandRun(name="subagent", args=f"{verb} {args}".strip(), session=self.session))
        out = (resp.result.get("output") or {}) if resp.ok else {}
        if verb == "list":
            rows = out.get("tasks", [])
            self.note("\n".join([f"{len(rows)} subagent task(s)"] + [
                f"  {r['task_id']}  [{r['status']}{', live' if r['live'] else ''}]  {r['title']}" for r in rows]))
        elif verb == "open" and resp.ok:
            if not out.get("session"):
                self.note(f"task {out['task_id']} has no session yet ({out['status']})", "warn")
                return
            await self.load_session(out["child_session"], resume=False)  # read-only look: resume would repair a live loop
            self.note(f"subagent {out['task_id']} is {out['status']}. " + (
                "/steer <task> <text> sends it a note, /stop <task> stops it, " if out["steerable"] else "finished; ")
                + "/sessions goes back to a thread", "hint")
        elif resp.ok:
            self.note(("note sent to " if verb == "send" else "stopped ") + out["task_id"] + "; the parent's receipt records it")

    async def slash(self, name: str, args: str) -> None:
        if not self.busy:
            self.hint_budget = 1
        if name == "approval-authority":
            if not self.session or args.strip() not in ("", "status", "grant", "revoke"):
                self.note("Select a parent thread. usage: /approval-authority [status|grant|revoke]", "warn")
                return
            status = await self.post(s.CommandRun(name=name, args="status", session=self.session))
            output = status.result.get("output") if status.ok else None
            if not isinstance(output, dict) or output.get("supports_delegated_approval") is not True:
                self.note("Selected core does not support delegated approval authority", "warn")
                return
            if args.strip() in ("grant", "revoke"):
                status = await self.post(s.CommandRun(name=name, args=args.strip(), session=self.session))
                output = status.result.get("output") if status.ok else None
            if output:
                self.note(json.dumps(output))
            return
        known = {n for n in LOCAL_COMMANDS} | {c["name"] for c in self.commands}
        if name not in known:  # D-037: a typo gets a suggestion, not a round trip and an error
            guess = cm.closest(name, known)
            if guess:
                self.note(f"unknown command /{name}. did you mean /{guess}? It is in the composer: Enter runs it.", "warn")
                self.composer.text = f"/{guess}" + (f" {args}" if args else "")
                self.composer.move_cursor(self.composer.document.end)
                return
        if args and name in {"exit", "quit", "unsteer", "restart", "peers", "clear", "cwd",
                             "usage", "intro", "help", "undo", "compact", "context", "panel"}:
            self.note(f"usage: /{name}", "warn")
            return
        if name == "trace" and args and not args.isdigit():
            self.note("usage: /trace [count]", "warn")
            return
        if name == "findings" and args not in ("", "all", "count", "open", "fixed", "verified", "reopened"):
            self.note("usage: /findings [state]", "warn")
            return
        if name in ("exit", "quit"):
            self.exit()
        elif name == "stop" and args:
            await self.subagent("stop", args)
        elif name == "stop":
            self.stop_turn()
        elif name == "limits":
            probe = await self.post(s.CommandRun(name="limits", args="json", session=self.session))
            state = (probe.result or {}).get("output") if probe.ok else None
            if not isinstance(state, dict) or state.get("supported") is not True:
                self.note("This core does not support /limits. Upgrade the core first.", "warn")
                return
            if args.strip() not in ("", "status", "json"):
                changed = await self.post(s.CommandRun(name="limits", args=args.strip(), session=self.session))
                if not changed.ok:
                    return
                state = changed.result.get("output") or {}
            self.note(state.get("text", "Core run-limit status unavailable."))
        elif name == "rounds":
            value = args.strip().lower()
            if value == "default":
                self.max_rounds = None
            elif value == "off":
                self.max_rounds = 0
            elif value.isascii() and value.isdigit():
                self.max_rounds = int(value)
            elif value:
                self.note("usage: /rounds [default|off|nonnegative number]", "warn")
                return
            mode = "default" if self.max_rounds is None else "unlimited" if self.max_rounds == 0 else str(self.max_rounds)
            self.note(f"window tool-round choice for new turns: {mode}. "
                      + ROUND_LIMIT_NOTE)
            self.refresh_status()
        elif name in ("tasks", "open", "steer"):
            await self.subagent({"tasks": "list"}.get(name, "send" if name == "steer" else name), args)
        elif name == "unsteer":
            await self.unsteer()
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
            elif word:
                self.open_providers(word)
            else:
                self.open_providers()
        elif name == "new":
            if args not in ("", "private"):
                self.note("usage: /new [private]", "warn")
                return
            await self.new_session("private" if args.strip() == "private" else "")
            self.hint("new_thread")
        elif name == "tab":
            from exo_tui import restart as rs
            directory = args.strip() or self.session_cwd
            if not directory:
                self.note("choose a working directory: /tab <directory>", "warn")
                return
            if self.remote:
                # This is a server-side path; file attachments remain local to this client.
                cwd = directory
            else:
                from exo_tui import directory as checked_directory
                path = Path(directory).expanduser()
                if not path.is_absolute():
                    path = Path(self.session_cwd or self.local_cwd) / path
                try:
                    cwd = checked_directory(str(path))
                except ValueError as exc:
                    self.note(str(exc), "warn")
                    return
            cmd = rs.new_command(cwd)
            result = await asyncio.to_thread(rs.open_tab, cmd)
            self.note("opened a new conversation window" if result == "opened" else "open a new window and run: " + result)
        elif name == "intro":
            await self.intro("manual")
        elif name == "handoff":
            await self.handoff(args)
        elif name == "tutorial":
            await self.tutorial_command(args)
        elif name == "layout":
            self.layout_command(args)
        elif name == "compose":
            self.compose_command(args)
        elif name in ("sessions", "resume"):
            await self.pick_session(args.strip())
        elif name == "model":
            word, _, rest = args.partition(" ")
            if word == "default":
                await self.set_default(rest.strip())
            else:
                await self.set_model(args)
        elif name == "rename":
            if not args.strip():
                self.note("usage: /rename <title>", "warn")
                return
            resp = await self.post(s.CommandRun(name="rename", args=args, session=self.session))
            if resp.ok:
                self.note(f"renamed to: {resp.result['output']['session']['title']}")
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
            full_request = name == "permissions" and args in ("full", "full all")
            permission_draft = "/permissions " + args
            if full_request:
                try:
                    args = await self.full_permissions_args(all_sessions=args == "full all")
                except Exception as exc:
                    self.note(str(exc), "error")
                    self.keep_text(permission_draft)
                    return
            resp = await self.post(s.CommandRun(name=name, args="json" if name == "context" else args,
                                                session=self.session))
            if resp.ok:
                out = resp.result.get("output")
                if out and name == "context":
                    if isinstance(out, dict) and out.get("schema_version") == 1:
                        self.ctx_tokens = out["tokens"]["total"]
                        self.window = int(out.get("capacity") or 0)
                        self.capacities[out["model"]] = self.window
                        self.refresh_status()
                        out = out["text"]
                    self.close_cur()
                    self.add(Block(out if isinstance(out, str) else str(out), "report", head="context " + th.DOT + " next call"))
                elif out:
                    self.note(out if isinstance(out, str) else str(out))
            if name == "permissions":
                if full_request and not resp.ok:
                    self.keep_text(permission_draft)
                await self.load_permissions()

    # ------------------------------------------------------------ onboarding (D-030)
    async def intro(self, kind: str) -> None:
        """Send the orientation as a normal user turn. It shows as "/intro"; the agent gets the longer prompt."""
        if self.intro_pending:
            return
        if not self.onb.onboarded:  # until an intro completes, /intro (the composer offers it) is still the first run
            kind = "first"
        if self.provider_check and not await self.first_run_providers():
            self.intro_after_providers = kind == "first"
            return
        self.add(Block("/intro", "user"))
        self.close_cur()
        if await self.intro_skill_missing():  # the turn could only improvise: show the short version instead
            self.add(Block(ORIENTATION, "intro", head="Exo in brief", meta=ORIENTATION_NOTE))
            self.onb.mark_onboarded()
            if kind == "first":
                self.run_worker(self.set_tutorial(True, quiet=True), exit_on_error=False)
            return
        self.intro_pending = kind
        self.intro_turn, self.intro_reply = "", False
        try:
            resp: s.Response | None = await self.post(s.TurnSend(session=self.session, text=INTRO_PROMPT,
                                                                 mode="steer" if self.busy else "send"))
        except Exception:
            resp = None
        if resp is None or (resp.error is not None and resp.error.code == LOST):
            self.intro_pending, self.intro_turn, self.intro_reply = "", "", False
            self.note("Could not start orientation. /intro retries when the core is available.", "warn")
            return
        if not resp.ok:
            self.intro_pending = ""  # the turn never started, so a first run stays un-onboarded

    async def intro_skill_missing(self) -> bool:
        """The core listed its skills and none is exo-intro (a pip install without the installer's skill pack)."""
        try:
            rows = (await self.client.call(s.SkillsList())).get("skills", [])
        except Exception:
            return False  # cannot tell: send the turn as before
        return not any(str(r.get("name", "")).split(":")[-1].lower() == INTRO_SKILL for r in rows)

    def intro_completed(self, stop_reason: str) -> None:
        """Only a successful real reply from this exact intro turn completes orientation."""
        kind, self.intro_pending = self.intro_pending, ""
        success = stop_reason == "end" and self.intro_reply
        self.intro_turn, self.intro_reply = "", False
        if not success:
            return
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
        await self.client.call(s.ConfigSet(key="guidance.enabled", value=on))  # tips and the guidance line go together
        self.guide.enabled = on
        if not on:
            self.tip = None
            self.refresh_status()
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
        """Try native helpers, retaining OSC52 as an explicitly unverified fallback."""
        import shutil
        import subprocess

        self.clipboard_verified = False
        for name, flags in (("pbcopy", []), ("wl-copy", []), ("xclip", ["-selection", "clipboard"]),
                            ("xsel", ["--clipboard", "--input"])):
            exe = shutil.which(name)
            if not exe:
                continue
            try:
                result = subprocess.run([exe, *flags], input=text.encode("utf-8"), timeout=3, check=False)
                if result.returncode == 0:
                    self.clipboard_verified = True
                    return
            except (OSError, subprocess.TimeoutExpired):
                pass
        super().copy_to_clipboard(text)


    def last_block(self, kind: str, blocks: list[Block]) -> str:
        """Newest text of a kind: mounted blocks first, then history that is not mounted yet."""
        if text := next((b.text for b in reversed(blocks) if b.kind == kind), ""):
            return text
        if kind == "assistant" and self.last_answer[1] and not self.older:
            return self.last_answer[1]
        return next((e[2] for e in reversed(self.older) if e[0] == "block" and e[1] == kind), "")

    def copy_command(self, what: str) -> None:
        blocks = [w for w in self.chrono() if isinstance(w, Block)]
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
        self.note(f"copied {label} ({len(text)} chars) to the clipboard" if self.clipboard_verified
                  else f"sent {label} ({len(text)} chars) via OSC52; clipboard receipt cannot be verified")

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
                  "      ctrl+o tool output   ctrl+y accept  ctrl+e edit  ctrl+r reject",
                  "      ctrl+c stops a running turn (idle: quit hint; with a selection: copy)",
                  "      ctrl+f focuses the approval card   ctrl+g opens/closes the big composer",
                  "      Up in an empty composer pulls a queued steer back to edit; at the first line it recalls history",
                  "      F6 loads earlier transcript messages"]
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
            if self.intro_after_providers:
                await self.intro_when_ready()

    # ------------------------------------------------------------ providers (D-034)
    def open_providers(self, initial: str = "", add: bool = False) -> None:
        if self.modal_open():
            return
        self.push_screen(ProvidersScreen(initial, add), self.providers_done)

    def providers_done(self, result: Any) -> None:
        self.composer.focus()
        if isinstance(result, tuple) and result[0] == "default":
            self.run_worker(self.pick_provider_default(result[1]), exit_on_error=False)
            return
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

    async def first_run_providers(self, show_setup: bool = True) -> bool:
        """Check the running core's main route; credentials alone cannot start orientation."""
        try:
            result = await self.client.call(s.CommandRun(name="providers"))
            state = (result.get("output") or {}).get("main") or {}
        except Exception:
            self.note("Could not check the main model route. /providers checks setup; /intro retries orientation.", "warn")
            return False
        if not state.get("ready"):
            try:
                needs = state.get("needs_model") or []
                if not needs and not show_setup:
                    return False
                if len(needs) == 1:
                    self.note(f"{needs[0]} is set up; choose a default model before /intro.")
                    await self.pick_provider_default(needs[0])
                else:
                    self.note(setup_advice((result.get("output") or {}).get("providers") or []) if not needs
                              else "Choose a default main model in /providers before /intro.")
                    self.open_providers()
            except Exception:  # the app closed while the check ran
                pass
            return False
        return True

    async def intro_when_ready(self) -> None:
        """The panel closed on a first run: send the waiting /intro once a provider works."""
        try:
            ready = await self.first_run_providers(show_setup=False)
        except Exception:
            ready = False
        if ready:
            self.intro_after_providers = False
            await self.intro("first")
        else:
            self.note("Still no provider. /providers adds one; /intro then gives the orientation.")

    async def pick_session(self, want: str = "") -> None:
        rows = (await self.client.call(s.SessionList(limit=50)))["sessions"]
        rows = [r for r in rows if r.get("kind") != "subagent"]  # subagent logs open with memory_zoom, not here
        if want:  # /resume <thread>: an id, or the best title match
            hit = next((r for r in rows if r["id"] == want), None) or next(iter(cm.rank(want, [
                cm.Item(r["id"], r.get("title") or r["id"]) for r in rows])), None)
            if hit is None:
                self.note(f"no thread matches {want!r}; /resume alone lists them", "warn")
            else:
                await self.load_session(hit.value if isinstance(hit, cm.Item) else hit["id"])
            return
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
            self.window = self.capacities.get(ref, 0)
            self.refresh_status()

    def observe_delegated(self, ev: s.Event) -> None:
        """Observe only task/approval metadata globally; child content never enters this thread."""
        b = ev.body
        if not isinstance(b, (s.TaskUpdated, s.ApprovalRequested, s.ApprovalResolved)):
            return
        self._delegated_revision += 1
        revision = self._delegated_revision
        if isinstance(b, s.TaskUpdated):
            task = dict(self.delegated_tasks.get(b.task_id, {}), task_id=b.task_id, revision=revision)
            for key in ("title", "status", "child_session"):
                if value := getattr(b, key):
                    task[key] = value
            if ev.session:
                task["parent"] = ev.session
            self.delegated_tasks[b.task_id] = task
        elif isinstance(b, s.ApprovalRequested):
            self.delegated_requests[b.request_id] = {"session": ev.session, "revision": revision}
            self.delegated_closed.pop(b.request_id, None)
        else:
            self.delegated_requests.pop(b.request_id, None)
            self.delegated_closed[b.request_id] = revision
        self.draw_delegated_approvals()

    def draw_delegated_approvals(self) -> None:
        edges = {t["child_session"]: t for t in self.delegated_tasks.values()
                 if t.get("child_session") and t.get("parent") and t.get("status") in ("queued", "running")}
        children = {}
        for request in self.delegated_requests.values():
            sid = request.get("session")
            if not sid or sid == self.session:
                continue
            child, visited = sid, set()
            while child in edges and child not in visited:
                visited.add(child)
                child = edges[child]["parent"]
                if child == self.session:
                    children[sid] = edges[sid]
                    break
        lines = [f"Delegated approval{' (last known)' if self._delegated_stale else ''}: "
                 f"{task.get('title') or sid} ({sid}). /open {task['task_id']} to review."
                 for sid, task in list(children.items())[:5]]
        if len(children) > 5:
            lines.append(f"{len(children) - 5} more delegated threads need approval. /tasks lists tasks.")
        for cue in self.query("#delegated-approvals"):
            cue.update(Text("\n".join(lines)))
            cue.display = bool(lines)

    async def refresh_delegated_approvals(self) -> None:
        """Task records alone prove ancestry. Merge events newer than either snapshot request."""
        self._delegated_asked += 1
        asked, revision = self._delegated_asked, self._delegated_revision
        try:
            tasks = await self.client.send(s.TasksList())
            approvals = await self.client.send(s.ApprovalsList())
            if not tasks.ok or not approvals.ok:
                raise ValueError("metadata unavailable")
        except Exception:
            if asked >= self._delegated_shown:
                self._delegated_stale = True
                self.draw_delegated_approvals()
            return
        if asked < self._delegated_shown:
            return
        self._delegated_shown = asked
        fresh = {t["task_id"]: dict(t) for t in tasks.result.get("tasks", []) if t.get("task_id")}
        fresh.update({tid: t for tid, t in self.delegated_tasks.items() if t.get("revision", 0) > revision})
        self.delegated_tasks = fresh
        pending = {a["request_id"]: {"session": a.get("session")} for a in approvals.result.get("approvals", [])
                   if a.get("request_id") and self.delegated_closed.get(a["request_id"], 0) <= revision}
        pending.update({rid: a for rid, a in self.delegated_requests.items() if a.get("revision", 0) > revision})
        self.delegated_requests = pending
        self.delegated_closed = {rid: version for rid, version in self.delegated_closed.items() if version > revision}
        self._delegated_stale = False
        self.draw_delegated_approvals()

    # ------------------------------------------------------------ approvals
    def show_approval(self) -> None:
        if self.card is not None or not self.approvals:
            return
        self.card = ApprovalCard(self.approvals[0])
        self.query_one("#left").mount(self.card, before=self.query_one("#activity"))

    def action_answer_card(self) -> None:
        """ctrl+f: put the focus on the approval card from anywhere; the draft in the composer stays as it is."""
        if self.card is not None and not self.modal_open():
            if self.card.noting:
                self.card.query_one("#deny-note").focus()
            else:
                self.card.focus()

    async def reconcile_approvals(self) -> None:
        """D-045: the core's list is the truth. A card whose request the core no longer holds (a missed
        approval.resolved, a restarted core, a replayed old request) is removed instead of lingering."""
        if not (self.approvals or self.card):
            return
        now = time.monotonic()
        if now - self._reconciled < self.reconcile_every:
            return
        self._reconciled = now
        # Only requests already shown when the list was asked for can be stale: one that arrives while the list is
        # in flight is newer than the answer and must not be closed by it.
        before = {a.request_id for a in self.approvals} | ({self.card.req.request_id} if self.card else set())
        try:  # the whole list: a session filter would leave out the requests of no thread, which show here too
            resp = await self.client.send(s.ApprovalsList())
        except Exception:
            return
        if not resp.ok:
            return
        gone = before - {a.get("request_id") for a in resp.result.get("approvals", [])}
        self.approvals = [a for a in self.approvals if a.request_id not in gone]
        self.threadless -= gone
        if self.card is not None and self.card.req.request_id in gone:
            await self.close_card(self.card.req.request_id)

    async def restore_approvals(self) -> None:
        """After an answer whose reply was lost: the core's list is the truth, so a request it still holds gets its
        card back. So does a request of no thread (D-026), which every thread shows."""
        try:
            resp = await self.client.send(s.ApprovalsList())
        except Exception:
            return
        if not resp.ok:
            return
        async with self._card_transition:
            shown = {a.request_id for a in self.approvals} | ({self.card.req.request_id} if self.card else set())
            for a in resp.result.get("approvals", []):
                if a.get("session") not in (None, self.session) or a.get("request_id") in shown:
                    continue
                if a.get("session") is None:
                    self.threadless.add(a.get("request_id", ""))
                self.approvals.append(from_dict(s.ApprovalRequested, a, strict=False))
            self.show_approval()
        await self.refresh_delegated_approvals()

    async def close_card(self, request_id: str) -> bool:
        """True when this call closed the request's card; False when it was already gone."""
        async with self._card_transition:
            card = self.card
            if card is None or card.req.request_id != request_id:
                return False  # a resolved event or another closer already replaced this request's card
            restore = self.focused is card or card.has_focus_within
            removal = card.remove()
            moved = self.focused  # removal hands the focus to a neighbour at once; only a later move is the user's
            await removal
            self.card = None
            self.show_approval()
            if restore and self.focused is moved and not self.modal_open():
                self.query_one(Composer).focus()
            return True

    async def on_approval_card_answered(self, msg: ApprovalCard.Answered) -> None:
        self.approvals = [a for a in self.approvals if a.request_id != msg.request_id]
        # A queued click must never close the replacement card, and a repeat of a key (the card is already gone)
        # answers nothing: only the call that closed this request's card notes and sends.
        if not await self.close_card(msg.request_id):
            return
        self.note(f"approval: {msg.decision}" + (f" ({msg.note})" if msg.note else ""))
        self.run_worker(self.answer_approval(s.ApprovalRespond(request_id=msg.request_id, decision=msg.decision,
                                                               note=msg.note, client=CLIENT_ID)), exit_on_error=False)

    async def answer_approval(self, body: s.ApprovalRespond) -> None:
        """The card goes before the answer is sent. If the core refuses the answer, its list says whether the request
        still waits; if it does, the card comes back to be answered again."""
        if not (await self.post(body)).ok:
            await self.restore_approvals()

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

    # ------------------------------------------------------------ layout (D-046)
    def arrange(self) -> None:
        """Put the input, menu, activity line and transcript in the order the layout wants."""
        left = self.query_one("#left")
        order = [self.cheader, self.query_one("#compose"), self.menu, self.query_one("#activity"), self.query_one("#delegated-approvals"), self.transcript]
        if not self.inverse:
            order = [self.transcript, self.query_one("#delegated-approvals"), self.query_one("#activity"), self.cheader, self.query_one("#compose"), self.menu]
        prev = None
        for w in order:
            if prev is None:
                left.move_child(w, before=0)
            else:
                left.move_child(w, after=prev)
            prev = w
        if self.card is not None:  # the approval card stays right above the activity line (its own code mounts it so)
            left.move_child(self.card, before=self.query_one("#activity"))
        left.set_class(self.inverse, "inverse")
        main = self.query_one("#main")  # the status strip goes with the input: top in inverse, bottom in classic
        screen = main.screen  # the strip and #main are both children of the app's own screen
        if self.inverse:
            screen.move_child(self.strip, before=main)
        else:
            screen.move_child(self.strip, after=main)
        self.strip.set_class(self.inverse, "inverse")

    def apply_layout(self, name: str, save: bool = True) -> None:
        if name not in LAYOUTS:
            return
        flip = name != self.layout
        self.layout = name
        if save:
            try:
                self.prefs.set_layout(name)
            except OSError:
                pass
        if flip:  # the same widgets, the other way round
            for w in list(self.transcript.children):  # each one to the front in turn: the order reverses
                self.transcript.move_child(w, before=0)
        self.arrange()
        self.to_latest()

    def layout_command(self, args: str) -> None:
        word = args.strip().lower()
        if not word:
            self.note(f"layout: {self.layout}. /layout inverse puts the input on top with the newest message under it; "
                      "/layout classic is the usual order.")
        elif word in LAYOUTS:
            self.apply_layout(word)
            self.note(f"layout: {word} (saved)")
        else:
            self.note("usage: /layout inverse|classic", "warn")

    # ------------------------------------------------------------ the big editor (D-046)
    def action_compose(self) -> None:
        if self.modal_open():
            return
        self.close_compose() if self.composing else self.open_compose()

    def open_compose(self, scaffold: str = "") -> None:
        c = self.composer
        if scaffold:
            have = c.text.rstrip("\n")
            c.text = (have + "\n\n" if have else "") + scaffold
            row = len(c.text.split("\n")) - len(scaffold.split("\n"))  # the first heading
            self.composing = True
            c.pane = True
            c.move_cursor((row + 1, 0))  # on the blank line under it
        else:
            self.composing = True
            c.pane = True
            c.move_cursor(c.document.end)
        c.show_line_numbers = self.numbers
        c.tab_behavior = "indent"
        c.indent_width = 2
        self.query_one("#left").add_class("composing")
        c.placeholder = "write the whole prompt here. Enter adds a line, ctrl+s sends."
        self.cheader.show(c, self.numbers)
        c.focus()
        c.refresh()

    def close_compose(self) -> None:
        if not self.composing:
            return
        self.composing = False
        c = self.composer
        c.pane = False
        c.show_line_numbers = False
        c.tab_behavior = "focus"
        self.query_one("#left").remove_class("composing")
        self.refresh_status()
        c.focus()
        c.refresh()

    def toggle_numbers(self) -> None:
        if self.composing:
            self.numbers = not self.numbers
            self.composer.show_line_numbers = self.numbers
            self.cheader.show(self.composer, self.numbers)

    def compose_command(self, args: str) -> None:
        word = args.strip().lower()
        if word in ("", "open"):
            self.open_compose()
        elif word == "task":
            self.open_compose(SCAFFOLD)
        elif word in ("close", "off"):
            self.close_compose()
        elif word == "edit":
            self.edit_in_editor()
        else:
            self.note("usage: /compose [task|edit|close]", "warn")

    def edit_in_editor(self) -> None:
        """Hand the draft to $VISUAL or $EDITOR and take the result back. The terminal is given to the editor."""
        from exo_tui.editor import run_editor
        c = self.composer

        def go() -> str | None:
            try:
                with self.suspend():
                    return run_editor(c.text)
            except Exception:  # a driver that cannot suspend (tests, some embeds): run it directly
                return run_editor(c.text)

        try:
            out = go()
        except FileNotFoundError:
            self.note("no editor found: set $VISUAL or $EDITOR", "warn")
            return
        if out is None:
            self.note("the editor did not save a draft", "warn")
            return
        self.put(out)
        if self.composing:
            self.cheader.show(c, self.numbers)

    # ------------------------------------------------------------ focus follows the user
    FOCUS_KEEP = (ApprovalCard, PanelView, Input, TextArea, OptionList)

    def on_key(self, event: Any) -> None:
        """A printable key that reached the app with nothing to take it (focus on the transcript, or nowhere) goes into
        the composer. A panel, the completion menu or the approval card that holds focus keeps its own keys."""
        if self.modal_open() or not event.is_printable or event.character is None:
            return
        f = self.focused
        if f is not None and f is not self.transcript:
            return
        c = self.composer
        c.focus()
        c.last_typed = time.monotonic()
        c.insert(event.character)
        event.stop()
        event.prevent_default()

    def on_click(self, event: Any) -> None:
        """A click on the transcript or any plain surface returns focus to the composer."""
        if self.modal_open():
            return
        try:
            w = self.screen.get_widget_at(event.screen_x, event.screen_y)[0]
        except Exception:
            return
        while w is not None:
            if isinstance(w, self.FOCUS_KEEP):
                return
            w = w.parent
        self.composer.focus()

    # ------------------------------------------------------------ actions
    def modal_open(self) -> bool:
        return len(self.screen_stack) > 1

    def action_escape(self) -> None:
        """Esc is soft (D-043): a guidance line, then a panel, then nothing. It never stops a turn."""
        view = self.query_one(PanelView)
        if self.composing:  # the pane closes first; the draft stays in the composer
            self.close_compose()
        elif self.tip is not None:
            self.tip = None
            self.refresh_status()
        elif view.display and view.panel_id:
            pid = view.panel_id
            self.respond_panel(pid, "dismissed")
            self.drop_panel(pid)

    def stop_turn(self) -> None:
        """The deliberate stop: ctrl+c while a turn runs, or /stop. The composer text stays as it is."""
        if not (self.busy and self.session):
            self.note("nothing is running", "hint")
            return
        self.note("stopped", "hint")
        self.hint("interrupt")
        self.run_worker(self.post(s.TurnInterrupt(session=self.session)), exit_on_error=False)

    def action_ctrl_c(self) -> None:
        if self.busy and self.session:
            self.stop_turn()
        else:
            self.action_help_quit()  # idle: Textual's own notice that ctrl+q quits; a single press never quits

    def composer_typed_recently(self, within: float = 1.5) -> bool:
        c = self.composer
        return bool(c.text) or time.monotonic() - c.last_typed < within

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
