"""In-harness worker route editor. Changes go through the principal-only core API."""
from __future__ import annotations

import json
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from exo.protocol import schema as s
from exo_tui.widgets import Palette

TIERS = ("fast", "standard", "deep")


class WorkerModelsScreen(ModalScreen):
    BINDINGS = [Binding("escape", "close", "close")]
    DEFAULT_CSS = """
    WorkerModelsScreen { align: center middle; background: $background 70%; }
    #worker-model-box { width: 94%; height: auto; max-height: 90%; padding: 1 2;
                        background: $surface; overflow-y: auto; }
    #worker-model-box Input { height: 3; }
    #worker-model-status, #worker-effective { height: auto; }
    """

    def __init__(self, client: Any) -> None:
        super().__init__()
        self.client = client
        self.state: dict[str, Any] | None = None
        self.saving = False
        self.picking = False
        self.loading = False

    def compose(self) -> ComposeResult:
        with Vertical(id="worker-model-box"):
            yield Label("Worker models")
            yield Static("Routes in order, separated by commas. First is preferred. "
                         "Leave a tier empty to inherit its fallback roles.")
            for tier in TIERS:
                yield Label(tier.capitalize())
                yield Input(placeholder="provider:model, provider:fallback", id=f"worker-{tier}", disabled=True)
                yield Button(f"Search {tier} models", id=f"worker-pick-{tier}")
            yield Static("", id="worker-effective", markup=False)
            yield Button("Save worker models", id="worker-save", disabled=True)
            yield Button("Reload settings", id="worker-reload")
            yield Static("Loading...", id="worker-model-status", markup=False)

    async def on_mount(self) -> None:
        await self.reload()

    async def reload(self) -> None:
        self.loading = True
        self.lock_controls()
        try:
            self.state = (await self.client.call(s.CommandRun(name="worker_models")))["output"]
            for tier in TIERS:
                field = self.query_one(f"#worker-{tier}", Input)
                field.value = ", ".join(self.state["roles"][tier])
                field.disabled = not self.state["editable"]
            self.query_one("#worker-save", Button).disabled = not self.state["editable"]
            self.query_one("#worker-effective", Static).update("\n".join(
                f"{t}: " + " → ".join(self.state["effective"][t]) for t in TIERS))
            self.query_one("#worker-model-status", Static).update(
                self.state["reason"] or self.state["scope"] + " " + self.state["note"])
        except Exception:
            self.state = None
            self.query_one("#worker-save", Button).disabled = True
            self.query_one("#worker-model-status", Static).update(
                "Worker settings could not be read. This core may need an update. Reload to retry.")
        finally:
            self.loading = False
            self.lock_controls()

    def lock_controls(self) -> None:
        busy = self.saving or self.loading
        edit = bool(self.state and self.state.get("editable")) and not busy
        self.query_one("#worker-save", Button).disabled = not edit
        self.query_one("#worker-reload", Button).disabled = busy
        for tier in TIERS:
            self.query_one(f"#worker-{tier}", Input).disabled = not edit
            self.query_one(f"#worker-pick-{tier}", Button).disabled = not edit

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if self.saving or self.loading:
            return
        if (event.button.id or "").startswith("worker-pick-"):
            if self.picking or not self.state or not self.state["editable"]:
                return
            tier = (event.button.id or "").removeprefix("worker-pick-")
            self.picking = True
            try:
                listed = await self.client.call(s.ModelsList())
                refs = sorted({r["route"] for r in [*listed.get("models", []), *listed.get("available", [])]
                               if r.get("route")})
                if self.is_mounted:
                    def picked(ref):
                        if ref and self.is_mounted:
                            field = self.query_one(f"#worker-{tier}", Input)
                            old = [r.strip() for r in field.value.split(",") if r.strip()]
                            field.value = ", ".join([ref, *[r for r in old[1:] if r != ref]])
                    self.app.push_screen(Palette([(r, r) for r in refs], f"Search {tier} models"), picked)
            except Exception:
                if self.is_mounted:
                    self.query_one("#worker-model-status", Static).update(
                        "Model catalog unavailable. You can type provider:model references directly.")
            finally:
                self.picking = False
            return
        if event.button.id == "worker-reload":
            await self.reload()
            return
        if event.button.id != "worker-save" or not self.state:
            return
        self.saving = True
        self.lock_controls()
        roles = {t: [r.strip() for r in self.query_one(f"#worker-{t}", Input).value.split(",") if r.strip()]
                 for t in TIERS}
        try:
            self.state = (await self.client.call(s.CommandRun(name="worker_models",
                args=json.dumps({"roles": roles, "revision": self.state["revision"]}))))["output"]
            self.query_one("#worker-effective", Static).update("\n".join(
                f"{t}: " + " → ".join(self.state["effective"][t]) for t in TIERS))
            self.query_one("#worker-model-status", Static).update("Saved and applied. " + self.state["note"])
        except Exception as exc:
            self.query_one("#worker-model-status", Static).update(
                {"conflict": "Settings changed. Reload before saving again.",
                 "bad_model": "Check model references, duplicate routes and provider role restrictions.",
                 "forbidden": "Principal authority is required.",
                 "save_failed": "Saving failed. Live routes were kept."}.get(getattr(exc, "code", ""),
                    "Save was not confirmed. Reload settings to check the current routes before retrying."))
        finally:
            self.saving = False
            self.lock_controls()

    def action_close(self) -> None:
        if not self.saving and not self.loading:
            self.dismiss()
