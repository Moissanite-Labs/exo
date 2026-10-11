from conftest import RULING
from exo_context import commands
from exo_context.engine import ExoContextEngine


def live(tmp_path, convo):
    eng = ExoContextEngine(handoff_dir=tmp_path / "handoffs")
    eng.on_session_start("abc123")
    eng.on_turn_complete(convo)
    return eng


def test_usage_and_unknown():
    assert commands.handle("").startswith("Usage:")
    assert "Unknown subcommand" in commands.handle("frobnicate")


def test_branch_writes_handoff_and_launch_line(tmp_path, convo):
    eng = live(tmp_path, convo)
    out = commands.handle("branch billing importer", eng)
    assert "Handoff written:" in out
    assert 'hermes chat -q "/exo resume billing-importer"' in out
    assert "1 ruling(s), 1 correction(s)" in out
    assert RULING in eng.last_handoff.read_text()
    assert commands.handle("branch", eng).startswith("Give the branch a topic")


def test_branch_uses_launch_template(tmp_path, convo, monkeypatch):
    monkeypatch.setenv("EXO_LAUNCH_TEMPLATE", "agent --handoff {path}")
    out = commands.handle("branch x", live(tmp_path, convo))
    assert "agent --handoff " in out and "x-" in out


def test_handoff_default_topic(tmp_path, convo):
    eng = live(tmp_path, convo)
    out = commands.handle("handoff", eng)
    assert "session-abc123" in out


def test_resume_injects(tmp_path, convo):
    eng = live(tmp_path, convo)
    commands.handle("branch billing", eng)
    fresh = ExoContextEngine(handoff_dir=tmp_path / "handoffs")
    out = commands.handle("resume billing", fresh)
    assert out.startswith('Resumed "billing"')
    assert RULING in out and RULING in fresh.resume_block
    assert fresh.topic == "billing"
    assert commands.handle("resume nothing-here", fresh).startswith('No handoff found for "nothing-here"')


def test_no_live_session(monkeypatch):
    monkeypatch.setattr(commands.eng, "live_engine", lambda: None)
    assert commands.handle("handoff").startswith("Nothing to hand off yet")
