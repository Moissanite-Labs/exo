"""The /providers panel (D-034): see every model provider, add or replace a key, test, remove, pick a default.

Client side only. It calls the providers_setup engine in a thread (Keychain calls are subprocesses) and tells a
running core to read keys again with `command.run providers "reload <name>"`; only the name crosses the protocol.

Keys: the value is typed in a masked Input inside KeyScreen, never in the composer. It goes from that Input to
``providers_setup.set_key`` and nowhere else: not the transcript, ``app.sent``, notes, logs or error text. Errors
show the SetupError text (it never holds a key) or the exception type name.
"""

from __future__ import annotations

import asyncio
from typing import Any

from rich.console import Group
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from exo import providers_setup as ps
from exo.protocol import schema as s
from exo_tui.theme import DOT, chips, header_rule, rich_style

NAME_W = 12
STATUS_W = 22


def refuse_key_in_composer(text: str) -> bool:
    """True for `/providers add <name> <something>`: a key pasted into the composer. The caller must drop the text
    without echoing it."""
    parts = text.strip().split()
    return len(parts) > 3 and parts[0] == "/providers" and parts[1] == "add"


def error_text(exc: BaseException) -> str:
    """What may be shown for a failure: a SetupError message, else only the exception class name."""
    return str(exc) if isinstance(exc, ps.SetupError) else type(exc).__name__


def status_chip(r: dict[str, Any]) -> Text:
    st = str(r.get("status") or "")
    t = Text(no_wrap=True, overflow="ellipsis")
    if r.get("verified"):
        ms = (r.get("last_test") or {}).get("latency_ms")
        t.append("● ", style=rich_style("ok"))
        t.append("verified", style=rich_style("ok"))
        if ms:
            t.append(f" {ms}ms", style=rich_style("fg3"))
    elif st.startswith("failed"):
        t.append("● " + st, style=rich_style("accent"))
    elif st in ("key found", "installed"):
        t.append("● ", style=rich_style("fg2"))
        t.append(st, style=rich_style("fg2"))
    elif st == "running":
        t.append("● ", style=rich_style("cold"))
        t.append(st, style=rich_style("cold"))
    else:  # no key, not running, not installed, local
        t.append("○ " + st, style=rich_style("fg3"))
    return t


def where_text(r: dict[str, Any]) -> str:
    if r.get("needs") == "key":
        env = (r.get("key_env") or [""])[0]
        return f"{env} {DOT} {r['source']}" if r.get("source") else f"{env} {DOT} none"
    return "cli login" if r.get("needs") == "cli" else "no key needed"


def row_line(r: dict[str, Any], selected: bool, width: int) -> Text:
    t = Text(no_wrap=True, overflow="ellipsis")
    t.append("› " if selected else "  ", style=rich_style("accent", "bold"))
    t.append(f"{r['provider']:<{NAME_W}}", style=rich_style("fg", "bold") if selected else rich_style("fg"))
    chip = status_chip(r)
    t.append_text(chip)
    t.append(" " * max(1, STATUS_W - chip.cell_len))
    t.append(f"{where_text(r):<30}", style=rich_style("fg3"))
    n = r.get("models")
    t.append(f"{'' if n is None else str(n) + ' models':<10}", style=rich_style("fg3"))
    if r.get("default"):
        t.append("★ default", style=rich_style("accent"))
    return t


class KeyScreen(ModalScreen):
    """A small modal with one masked field. Dismisses with the typed value, or None. The field is cleared at once."""

    BINDINGS = [Binding("escape", "close", "cancel")]
    DEFAULT_CSS = """
    KeyScreen { align: center middle; background: $background 70%; }
    #key-box { width: 64; height: auto; border-left: outer $accent; background: $surface; padding: 0 1; }
    #key-input { border: none; background: $panel; height: 1; padding: 0 1; }
    #key-input:focus { border: none; }
    """

    def __init__(self, provider: str, var: str = "") -> None:
        super().__init__()
        self.provider = provider
        self.var = var

    def compose(self) -> ComposeResult:
        with Vertical(id="key-box"):
            yield Static(header_rule(f"KEY {DOT} {self.provider}", "accent", 60), id="key-head")
            yield Static(Text(f"Paste the API key{f' for {self.var}' if self.var else ''}. It is hidden as you type "
                              "and never shown. Saving reports whether it went to the Keychain, Secret Service, "
                              "or owner-only keys.env, and which source is effective.", style=rich_style("fg2")))
            yield Input(password=True, placeholder="paste key, then enter", id="key-input")
            yield Static(chips([("enter", "save"), ("esc", "cancel")]), id="key-keys")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        value = event.value
        event.input.value = ""
        self.dismiss(value)
        del value

    def action_close(self) -> None:
        self.query_one(Input).value = ""
        self.dismiss(None)


class ProvidersScreen(ModalScreen):
    """One row per provider. a add/replace key, t test, r remove, d default, esc close."""

    BINDINGS = [Binding("escape", "close", "close"), Binding("down", "move(1)", show=False),
                Binding("up", "move(-1)", show=False), Binding("j", "move(1)", show=False),
                Binding("k", "move(-1)", show=False)]
    DEFAULT_CSS = """
    ProvidersScreen { align: center middle; background: $background 70%; }
    #prov-box { width: 94%; height: auto; max-height: 90%; border-left: outer $cold; background: $surface;
                padding: 0 1; overflow-y: auto; }
    #prov-rows, #prov-msg, #prov-keys { height: auto; }
    """

    def __init__(self, initial: str = "", add: bool = False) -> None:
        super().__init__()
        self.table: list[dict[str, Any]] = []
        self.idx = 0
        self.initial = initial
        self.add_first = add
        self.msg = ""
        self.confirm = ""  # provider whose key removal waits for y or n
        self.work = False
        self.plain = ""  # the panel as text, for tests

    def compose(self) -> ComposeResult:
        with Vertical(id="prov-box"):
            yield Static("", id="prov-head")
            yield Static("", id="prov-rows")
            yield Static("", id="prov-msg")
            yield Static("", id="prov-keys")

    async def on_mount(self) -> None:
        await self.reload()
        if self.initial:
            names = [r["provider"] for r in self.table]
            if self.initial in names:
                self.idx = names.index(self.initial)
                self.paint()
                if self.add_first:
                    self.action_add()
            else:
                self.say(f"unknown provider {self.initial!r}")

    # --- state
    @property
    def current(self) -> dict[str, Any] | None:
        return self.table[self.idx] if 0 <= self.idx < len(self.table) else None

    async def reload(self) -> None:
        try:
            self.table = await asyncio.to_thread(ps.rows)
        except Exception as e:
            self.table, self.msg = [], f"could not read providers ({error_text(e)})"
        self.idx = min(self.idx, max(0, len(self.table) - 1))
        self.paint()

    def say(self, text: str) -> None:
        self.msg = text
        self.paint()

    def paint(self) -> None:
        w = self.size.width or 100
        self.query_one("#prov-head", Static).update(header_rule("PROVIDERS", "cold", max(20, w - 4)))
        lines = [row_line(r, i == self.idx, w) for i, r in enumerate(self.table)]
        self.query_one("#prov-rows", Static).update(Group(*lines) if lines else Text(self.msg or "loading...",
                                                                                      style=rich_style("fg3")))
        msg = Text(self.msg if lines else "", style=rich_style("fg2"), no_wrap=False, overflow="fold")
        self.plain = "\n".join([header_rule("PROVIDERS", "cold", 20).plain, *(ln.plain for ln in lines), self.msg])
        if self.confirm:
            msg = Text(f"remove the stored key for {self.confirm}?  ", style=rich_style("accent"))
            msg.append_text(chips([("y", "yes"), ("n", "no")]))
        self.query_one("#prov-msg", Static).update(msg)
        keys = [("a", "key"), ("t", "test"), ("r", "remove"), ("d", "default"), ("esc", "close")]
        self.query_one("#prov-keys", Static).update(Group(Text(""), chips(keys)))

    # --- keys
    def action_move(self, d: int) -> None:
        if self.table and not self.confirm:
            self.idx = (self.idx + d) % len(self.table)
            self.msg = ""
            self.paint()

    def action_close(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if self.work:
            return
        k = event.key
        if self.confirm:
            event.stop()
            name, self.confirm = self.confirm, ""
            if k == "y":
                self.run_worker(self.do_remove(name), exit_on_error=False)
            else:
                self.paint()
            return
        act = {"a": self.action_add, "t": self.action_test, "r": self.action_remove, "d": self.action_default}.get(k)
        if act is not None:
            event.stop()
            act()

    def action_add(self) -> None:
        r = self.current
        if r is None:
            return
        if r.get("needs") != "key":
            self.say(f"{r['provider']} takes no key")
            return
        name = r["provider"]
        self.app.push_screen(KeyScreen(name, (r.get("key_env") or [""])[0]),
                             lambda v: v and self.run_worker(self.do_set(name, v), exit_on_error=False))

    def action_test(self) -> None:
        if self.current:
            self.run_worker(self.do_test(self.current["provider"]), exit_on_error=False)

    def action_remove(self) -> None:
        r = self.current
        if r is None:
            return
        if r.get("needs") != "key":
            self.say(f"{r['provider']} has no stored key")
            return
        self.confirm = r["provider"]
        self.paint()

    def action_default(self) -> None:
        if self.current:
            self.dismiss(("default", self.current["provider"]))

    # --- work (each runs the engine in a thread)
    async def tell_core(self, name: str) -> None:
        """Tell a running core to read keys again. Only the provider name is sent. A failure is ignored."""
        app = self.app
        body = s.CommandRun(name="providers", args=f"reload {name}")
        try:
            app.sent.append(body)
            await app.client.send(body)
        except Exception:
            pass

    async def do_set(self, name: str, value: str) -> None:
        self.work = True
        try:
            self.say(f"saving key for {name}...")
            try:
                out = await asyncio.to_thread(ps.set_key, name, value)
            except Exception as e:
                self.say(f"could not save the key: {error_text(e)}")
                return
            finally:
                value = ""
            warning = out.get("warning") or ("an env var of the same name still wins" if out.get("shadowed_by_env") else "")
            effective = out.get("effective_source") or "not confirmed"
            self.say(f"key for {name} saved in {out.get('stored_in')} {DOT} effective source: {effective}"
                     + (f" {DOT} {warning}" if warning else ""))
            await self.tell_core(name)
            await self.reload()
        finally:
            self.work = False

    async def do_test(self, name: str) -> None:
        self.work = True
        try:
            self.say(f"testing {name}...")
            try:
                out = await asyncio.to_thread(ps.test_provider, name)
            except Exception as e:
                self.say(f"test failed: {error_text(e)}")
                return
            if out.get("ok"):
                ms = out.get("latency_ms")
                n = out.get("models")
                self.say(f"{name} ok" + (f" {DOT} {ms}ms" if ms else "") + (f" {DOT} {n} models" if n else ""))
            else:
                self.say(f"{name} failed: {out.get('error_class') or 'error'} {DOT} {out.get('detail') or ''}")
            await self.reload()
        finally:
            self.work = False

    async def do_remove(self, name: str) -> None:
        self.work = True
        try:
            try:
                out = await asyncio.to_thread(ps.remove_key, name)
            except Exception as e:
                self.say(f"could not remove the key: {error_text(e)}")
                return
            gone = ", ".join(out.get("removed_from") or []) or "nothing stored"
            env = out.get("still_in_env") or []
            self.say(f"removed from {gone}" + (f" {DOT} still set in the environment: {', '.join(env)}" if env else ""))
            await self.tell_core(name)
            await self.reload()
        finally:
            self.work = False
