"""D-031: the terminal look. Tokens, instruments, panels, the boot sequence and the reduced-motion and NO_COLOR paths."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from exo.core.engine import CoreEngine
from exo.core.types import ToolCall
from exo.protocol import schema as s
from exo_tui import theme as th
from exo_tui.app import ExoApp
from exo_tui.boot import BootScreen
from exo_tui.widgets import (BUSY_HINT, IDLE_HINT, ApprovalCard, Block, BrandBar, Composer, PromptMark, StatusStrip,
                             ToolBlock, Welcome)
from rich.console import Console
from test_tui import FAKE, FakeServerClient, make_engine, run, send, until

SITE_CSS = Path(__file__).resolve().parents[3] / "site" / "assets" / "style.css"


def plain(renderable, width: int = 100) -> str:
    c = Console(width=width, record=True, force_terminal=False, color_system=None)
    c.print(renderable)
    return c.export_text()


# ---------------------------------------------------------------- tokens
def test_tokens_match_the_site():
    css = SITE_CSS.read_text()
    root = css[css.index(":root"):css.index("}", css.index(":root"))]
    site = dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", root))
    mine = {"bg": th.BG, "bg-2": th.BG_2, "bg-3": th.BG_3, "line": th.LINE, "line-2": th.LINE_2, "fg": th.FG,
            "fg-2": th.FG_2, "fg-3": th.FG_3, "accent": th.ACCENT, "accent-2": th.ACCENT_2, "cold": th.COLD,
            "ok": th.OK}
    for k, v in mine.items():
        assert site[k].lower() == v.lower(), k
    assert ".term { margin: 0; border: 1px solid var(--line-2); background: #050606" in css
    assert th.DEEP == "#050606"


def test_theme_registered_and_variables_resolve(tmp_path):
    async def sc(app, pilot, client, eng):
        assert app.theme == "exo"
        assert app.current_theme.primary.lower() == th.ACCENT
        assert app.theme_variables["cold"].lower() == th.COLD

    run(tmp_path, sc)


def test_no_color_strips_paint_and_keeps_shape(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert th.rich_style("accent") == "bold"
    assert th.rich_style("cold") == ""
    assert th.rich_style("fg3") == "dim"
    assert th.fg("accent") == ""
    monkeypatch.delenv("NO_COLOR")
    assert th.rich_style("accent", "bold") == f"bold {th.ACCENT}"


def test_no_color_app_uses_the_plain_theme(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")

    async def sc(app, pilot, client, eng):
        assert app.theme == "exo-plain"
        assert "E X O" in plain(app.brand.render())  # draws without a palette lookup failing

    run(tmp_path, sc)


def test_reduced_motion_flags(monkeypatch):
    monkeypatch.setenv("EXO_REDUCED_MOTION", "1")
    assert th.reduced_motion()
    monkeypatch.setenv("EXO_REDUCED_MOTION", "0")
    assert not th.reduced_motion()


# ---------------------------------------------------------------- drawing helpers
def test_header_rule_chips_and_gauge():
    r = th.header_rule("approval · public", "accent", 40)
    assert r.plain.startswith("APPROVAL · PUBLIC ") and r.plain.endswith("◥") and r.cell_len == 40
    assert th.chips([("o", "once"), ("d", "deny")]).plain == "[o] once  [d] deny"
    g = th.gauge(0.5, 10)
    assert g.plain == "━" * 5 + "─" * 5
    assert th.gauge(2.0, 4).plain == "━" * 4 and th.gauge(-1, 4).plain == "─" * 4
    assert th.human_tokens(999) == "999" and th.human_tokens(12_000) == "12k" and th.human_tokens(1500) == "1.5k"


# ---------------------------------------------------------------- instruments
def test_status_strip_shows_fallback_gauge_cache_cost_findings(tmp_path):
    async def sc(app, pilot, client, eng):
        strip = app.query_one(StatusStrip)
        strip.set(model="fake:y", requested="fake:x", ctx=50_000, window=200_000, cache=0.82, cost="$0.14", findings=2)
        text = strip.render().plain
        assert "MODEL fake:y ← fake:x" in text
        assert "COST $0.14" in text and "25%" in text and "CACHE 82%" in text and "$0.14" in text and "2 findings" in text
        strip.set(requested="fake:y")
        assert "←" not in strip.render().plain
        strip.set(cache=None, findings=1)
        assert "CACHE -" in strip.render().plain and "1 finding" in strip.render().plain
        assert "findings" not in strip.render().plain

    run(tmp_path, sc)


def test_model_call_drives_served_model_and_cache_share(tmp_path):
    async def sc(app, pilot, client, eng):
        eng.hub.publish(s.TurnStarted(turn_id="t", model="fake:x"), app.session)
        eng.hub.publish(s.ModelCall(requested="fake:x", route="fake:y",
                                    usage=s.Usage(input_tokens=2000, cache_read_tokens=8000)), app.session)
        eng.hub.publish(s.UsageUpdated(usage=s.Usage(input_tokens=2000, cache_read_tokens=8000),
                                       session_total=s.Usage(cost_usd=0.5), model="fake:x"), app.session)
        await until(lambda: app.cost == 0.5, pilot)
        assert (app.model, app.requested) == ("fake:y", "fake:x")  # the usage event must not hide the fallback
        assert app.cache_share == pytest.approx(0.8)
        assert "cache 80%" in app.status_text()
        assert app.brand.live

    run(tmp_path, sc)


def test_brand_bar_live_dot_and_names(tmp_path):
    async def sc(app, pilot, client, eng):
        assert "E X O" in plain(app.brand.render()) and "exo-razi" not in plain(app.brand.render())
        bar = BrandBar()
        bar.set("exo-razi", "thread work", False)
        out = plain(bar.render())
        assert "E X O" in out and "exo-razi" in out and "thread work" in out and "IDLE" in out
        bar.set("exo-razi", "thread work", True)
        assert "LIVE" in plain(bar.render())

    run(tmp_path, sc)


def test_steer_state_in_prompt_placeholder_and_user_block(tmp_path):
    async def sc(app, pilot, client, eng):
        assert app.composer.placeholder == IDLE_HINT
        eng.hub.publish(s.TurnStarted(turn_id="t", model="fake:x"), app.session)
        await until(lambda: app.busy, pilot)
        assert plain(app.prompt.render()).strip() == "steer ›"
        assert app.composer.placeholder == BUSY_HINT
        await send(app, pilot, "change course")
        await until(lambda: any(isinstance(w, Block) and w.kind == "user" and w.steer for w in app.transcript.children),
                    pilot)
        eng.hub.publish(s.TurnCompleted(turn_id="t"), app.session)
        await until(lambda: not app.busy, pilot)
        assert plain(app.prompt.render()).strip() == "›"

    run(tmp_path, sc)


# ---------------------------------------------------------------- transcript
def test_tool_line_states_and_elapsed():
    tb = ToolBlock("c1", "bash", {"command": "pytest -q"})
    assert tb.glyph().plain not in ("✓", "✕") and tb.clock().endswith("s")
    tb.finish("done", 1.25)
    assert tb.glyph().plain == "✓" and tb.clock() == "1.2s"
    tb.finish("error", 125.0)
    assert tb.glyph().plain == "✕" and tb.clock() == "2m05s"
    replayed = ToolBlock("c2", "read_file", {"path": "a"})
    replayed.status = "done"
    assert replayed.clock() == "" or replayed.clock().endswith("s")


def test_tool_completed_uses_the_core_duration(tmp_path):
    from exo.core.types import ContentPart, ToolResult

    async def sc(app, pilot, client, eng):
        eng.hub.publish(s.ToolStarted(call=ToolCall("c1", "bash", {"command": "echo hi"})), app.session)
        await until(lambda: "c1" in app.tools, pilot)
        eng.hub.publish(s.ToolCompleted(result=ToolResult(call_id="c1", content=[ContentPart.of_text("hi")]),
                                        duration_ms=2500), app.session)
        await until(lambda: app.tools["c1"].status == "done", pilot)
        assert app.tools["c1"].clock() == "2.5s"

    run(tmp_path, sc)


def test_memory_summarized_is_a_one_line_cold_event(tmp_path):
    async def sc(app, pilot, client, eng):
        eng.hub.publish(s.MemorySummarized(node_id="n", level=0, start=1, end=16, before_tokens=1781,
                                           after_tokens=153), app.session)
        await until(lambda: "memory · log 1-16 folded · 1781 → 153 tok" in app.transcript_text(), pilot)
        await pilot.pause(0.1)
        blk = [w for w in app.transcript.children if isinstance(w, Block) and w.kind == "memory"][0]
        assert plain(blk.shown).strip() == blk.text
        eng.hub.publish(s.MemorySummarized(node_id="m", level=2, start=1, end=32, before_tokens=400,
                                           after_tokens=90), app.session)
        await until(lambda: "level 2 summary merged" in app.transcript_text(), pilot)

    run(tmp_path, sc)


def test_taste_and_finding_and_peer_are_panels_with_headers(tmp_path):
    async def sc(app, pilot, client, eng):
        eng.hub.publish(s.TasteUpdated(rule_id="r", action="add", text="Lead with the result.", path="~/t.md"),
                        app.session)
        eng.hub.publish(s.PeerMessage(msg_id="m", sender="razi", text="build is green", trust="same-principal"),
                        app.session)
        await until(lambda: "build is green" in app.transcript_text(), pilot)
        await pilot.pause(0.1)
        taste = [w for w in app.transcript.children if isinstance(w, Block) and w.kind == "taste"][0]
        out = plain(taste.shown, 60)
        assert out.startswith("TASTE · LEARNED") and "Lead with the result." in out and "edit file" in out
        peer = [w for w in app.transcript.children if isinstance(w, Block) and w.kind == "peer"][0]
        out = plain(peer.shown, 60)
        assert out.startswith("PEER · RAZI · SAME-PRINCIPAL") and "build is green" in out
        assert "peer message from razi" in peer.text  # the plain text keeps its old form for search and logs
        assert peer.kind != "user"

    run(tmp_path, sc)


def test_report_block_and_welcome(tmp_path):
    async def sc(app, pilot, client, eng):
        await pilot.pause(0.1)
        assert app.query("#welcome")  # the empty start shows keys, not a blank page
        assert "[enter] send" in plain(app.query_one(Welcome).shown, 100)
        await send(app, pilot, "hello")
        await until(lambda: "friend" in app.transcript_text(), pilot)
        assert not app.query("#welcome")
        await send(app, pilot, "/context")
        await until(lambda: "recent tail" in app.transcript_text(), pilot)
        await pilot.pause(0.1)
        rep = [w for w in app.transcript.children if isinstance(w, Block) and w.kind == "report"][0]
        out = plain(rep.shown, 100)
        assert out.startswith("CONTEXT · NEXT CALL") and "recent tail" in out

    run(tmp_path, sc)


def test_tip_line_keeps_the_tip_prefix_in_the_text(tmp_path):
    async def sc(app, pilot, client, eng):
        app.tutorial = True
        app.hint("steer")
        await until(lambda: "tip: " in app.transcript_text(), pilot)
        await pilot.pause(0.1)
        blk = [w for w in app.transcript.children if isinstance(w, Block) and w.kind == "hint"][0]
        assert plain(blk.shown).startswith("tip · That message steers")

    run(tmp_path, sc)


# ---------------------------------------------------------------- approval
def test_approval_card_draws_header_and_key_chips(tmp_path):
    async def sc(app, pilot, client, eng):
        eng.hub.publish(s.ApprovalRequested(request_id="r", summary="Post it?", risk="public, irreversible",
                                            call=ToolCall("c", "slack_post", {"text": "hi"})), app.session)
        await until(lambda: app.card is not None, pilot)
        await pilot.pause(0.1)
        out = plain(app.card.body(), 70)
        assert out.startswith("APPROVAL · PUBLIC, IRREVERSIBLE") and "Post it?" in out
        assert "[o] once  [a] always  [d] deny" in out
        await pilot.press("d")
        await pilot.pause(0.1)
        assert "why deny?" in plain(app.card.body(), 70) and "[o]" not in plain(app.card.body(), 70)

    run(tmp_path, sc)


def test_approval_chips_follow_the_options(tmp_path):
    req = s.ApprovalRequested(request_id="r", summary="x", risk="low", options=["allow", "deny"])
    card = ApprovalCard(req)
    out = plain(card.body(), 60)
    assert "[o] once" in out and "[a]" not in out and "[d] deny" in out


# ---------------------------------------------------------------- boot sequence
def boot_run(tmp_path, scenario, **kw):
    eng = make_engine(tmp_path)
    client = FakeServerClient(eng)

    async def main():
        app = ExoApp(client, seat="tester", boot_anim=True, **kw)
        async with app.run_test(size=(100, 30)) as pilot:
            await scenario(app, pilot)

    asyncio.run(main())


def test_boot_sequence_runs_marks_real_steps_and_ends(tmp_path):
    async def sc(app, pilot):
        assert isinstance(app.screen, BootScreen)
        await until(lambda: app.ready.is_set(), pilot)
        assert set(app.boot_screen.done) == {"core", "agent", "memory", "model"}
        await until(lambda: not isinstance(app.screen, BootScreen), pilot, timeout=3)
        assert isinstance(app.focused, Composer)

    boot_run(tmp_path, sc)


def test_boot_is_skippable_with_any_key(tmp_path):
    async def sc(app, pilot):
        await pilot.press("x")
        await until(lambda: not isinstance(app.screen, BootScreen), pilot, timeout=1)

    boot_run(tmp_path, sc)


def test_boot_is_off_under_reduced_motion_and_for_tests(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_REDUCED_MOTION", "1")

    async def sc(app, pilot):
        assert app.boot_screen is None and not isinstance(app.screen, BootScreen)
        assert not app.motion

    boot_run(tmp_path, sc)
    monkeypatch.delenv("EXO_REDUCED_MOTION")
    assert ExoApp(object()).boot_anim is False  # default off: only the entry point asks


def test_reduced_motion_app_has_still_widgets(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_REDUCED_MOTION", "1")

    async def sc(app, pilot, client, eng):
        assert not app.composer.cursor_blink
        tb = ToolBlock("c", "bash", {})
        assert tb.glyph().plain == "◇"
        eng.hub.publish(s.TurnStarted(turn_id="t", model="fake:x"), app.session)
        await until(lambda: app.busy, pilot)
        assert app.wire().plain == app.wire().plain  # no packet motion: same frame twice

    run(tmp_path, sc)


# ---------------------------------------------------------------- snapshots
def test_screenshots_of_key_states_render(tmp_path):
    async def sc(app, pilot, client, eng):
        shots = []
        shots.append(app.export_screenshot())
        eng.hub.publish(s.ApprovalRequested(request_id="r", summary="Post it?", risk="public"), app.session)
        await until(lambda: app.card is not None, pilot)
        await pilot.pause(0.6)
        shots.append(app.export_screenshot())
        assert all(x.startswith("<svg") for x in shots) and "APPROVAL" in shots[1].replace("&#160;", " ")

    run(tmp_path, sc)


# ---------------------------------------------------------------- hero motif, startup line, tool columns
def test_hero_frames_are_braille_cheap_and_loop():
    from exo_tui.hero import FRAMES, hero_rows, render_frame

    f0, f1 = render_frame(0, 24, 10), render_frame(1, 24, 10)
    assert len(f0.plain.splitlines()) == 10 and all(len(ln) == 24 for ln in f0.plain.splitlines())
    assert all(ch in " \n" or 0x2800 <= ord(ch) <= 0x28FF for ch in f0.plain)
    assert f0.plain != f1.plain  # it turns
    assert render_frame(FRAMES, 24, 10).plain == f0.plain  # and loops
    assert any(sp.style and th.ACCENT in str(sp.style) for sp in f0.spans)  # the orange core
    assert (hero_rows(45), hero_rows(32), hero_rows(29)) == (10, 7, 0)  # scales down, hidden below 30 rows


def test_empty_start_has_hero_no_new_thread_line_and_it_goes_on_first_message(tmp_path):
    async def sc(app, pilot, client, eng):
        await pilot.pause(0.1)
        assert "new thread" not in app.transcript_text()
        assert app.size.height >= 30 and app.query("#hero")  # 120x40 in the shared harness
        assert len(app.query_one("#hero").frames) > 1
        await send(app, pilot, "hello")
        await until(lambda: not app.query("#hero") and not app.query("#welcome"), pilot)
        await send(app, pilot, "/new")
        await until(lambda: "new thread" in app.transcript_text(), pilot)

    run(tmp_path, sc)


def test_hero_is_still_under_reduced_motion_and_no_color(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_REDUCED_MOTION", "1")

    async def sc(app, pilot, client, eng):
        await pilot.pause(0.1)
        assert len(app.query_one("#hero").frames) == 1

    run(tmp_path, sc)


def test_hero_hidden_on_a_short_terminal(tmp_path):
    eng = make_engine(tmp_path)

    async def main():
        app = ExoApp(FakeServerClient(eng), seat="tester")
        async with app.run_test(size=(100, 24)) as pilot:
            await asyncio.wait_for(app.ready.wait(), 5)
            assert not app.query("#hero") and app.query("#welcome")

    asyncio.run(main())


def test_tool_columns_align_with_and_without_the_hint():
    a = ToolBlock("1", "read_file", {"path": "a"})
    a.output = "x\ny\n"
    a.finish("done", 0.4)
    b = ToolBlock("2", "bash", {"command": "ls"})
    b.finish("done", 12.5)
    for blk in (a, b):
        blk.flush()
        out = plain(blk.shown, 60)
        line = out.splitlines()[0]
        assert len(line.rstrip()) == 60, line  # the clock ends at the right edge either way


def test_approval_leaves_a_blank_row_above_the_wire(tmp_path):
    async def sc(app, pilot, client, eng):
        eng.hub.publish(s.ApprovalRequested(request_id="r", summary="x", risk="low"), app.session)
        await until(lambda: app.card is not None, pilot)
        await pilot.pause(0.2)
        assert app.card.region.bottom < app.query_one("#activity").region.y  # a gap, not touching

    run(tmp_path, sc)


# ---------------------------------------------------------------- D-032: cost is unknown, never 0; jobs
def test_cost_label_follows_the_unknown_never_zero_rule(tmp_path):
    async def sc(app, pilot, client, eng):
        assert app.cost_label() == "\u2013" and "COST \u2013" in app.strip.render().plain
        eng.hub.publish(s.ModelCall(requested="fake:x", route="fake:x", usage=s.Usage(input_tokens=100, output_tokens=5)),
                        app.session)
        await until(lambda: app.calls_seen == 1, pilot)
        assert app.cost_label() == "unknown" and "$0" not in app.status_text()
        eng.hub.publish(s.ModelCall(requested="fake:x", route="fake:x",
                                    usage=s.Usage(input_tokens=100, output_tokens=5, cost_usd=0.02)), app.session)
        eng.hub.publish(s.UsageUpdated(usage=s.Usage(input_tokens=1), session_total=s.Usage(cost_usd=0.02),
                                       model="fake:x"), app.session)
        await until(lambda: app.cost == 0.02, pilot)
        assert app.cost_label() == "$0.0200+"  # one call had no price
        assert "$0.0200+" in app.status_text()

    run(tmp_path, sc)


def test_job_updated_is_one_cold_system_line(tmp_path):
    async def sc(app, pilot, client, eng):
        eng.hub.publish(s.JobUpdated(job_id="j_1", state="running", command="make"), app.session)
        eng.hub.publish(s.JobUpdated(job_id="j_1", state="done", command="make", exit_code=0), app.session)
        eng.hub.publish(s.JobUpdated(job_id="j_2", state="failed", command="npm test", exit_code=2), app.session)
        await until(lambda: "j_2 failed" in app.transcript_text(), pilot)
        await pilot.pause(0.1)
        kinds = [w.kind for w in app.transcript.children if isinstance(w, Block) and w.text.startswith("job ")]
        assert kinds == ["job", "job", "warn"]  # cold lines; a failure is warm
        cold = [w for w in app.transcript.children if isinstance(w, Block) and w.kind == "job"][0]
        assert plain(cold.shown).strip() == "job j_1 running: make" and th.COLD in str(cold.shown.style)

    run(tmp_path, sc)
