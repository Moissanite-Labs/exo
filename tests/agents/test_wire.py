"""exo.wire: the SYM-2P line with Exo vocabulary, JSON fallback, repair, signing and English."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from exo import crypto
from exo.protocol import schema as s
from exo.protocols import sym2p
from exo.wire import (TASK_STATUSES, WIRE_FORMATS, Brief, Receipt, WireError, check_line, decode, decode_any, encode,
                      from_json, negotiate, render, repair, sign, to_json, verify)
from exo.wire.grammar import OPS, STATES, Str, dump, parse

UPSTREAM_BRIEF = (Path(__file__).parents[1] / "bus" / "sym2p_fixtures" / "v2" / "brief.sym2").read_text().strip()

BRIEF = Brief(id="k_3f9a", thread="s_8e7d", goal='Find callers of "load_config" | list file:line', role="scout",
              context="Python repo.\nTwo lines.", tools=["glob", "grep", "read_file"], max_turns=12,
              max_tokens=60000, timeout_s=300, depth=1, max_depth=2, cwd="/tmp/w", accept=["every caller listed"],
              base="s_8e7d:4", request="ack", tier="fast")
RECEIPT = Receipt(id="r_3f9a", thread="s_8e7d", brief="k_3f9a", status="failed", role="scout",
                  summary="Partial: 2 callers.", input_tokens=1200, output_tokens=80, cost_usd=0.0042,
                  duration_ms=3100, rounds=2, tool_calls=3, files=["a.py", "dir/b c.py"], error_code="timeout",
                  error="no result within 300 s", child_session="s_1b2c", confidence=40,
                  tier="standard", model="anthropic:claude-sonnet-5-5", escalated=1)


@pytest.mark.parametrize("packet", [BRIEF, RECEIPT, Brief(id="k_1", thread="t", goal="x"),
                                    Receipt(id="r_1", thread="t", brief="k_1")])
@pytest.mark.parametrize("rcp", [False, True])
def test_roundtrip_line_and_json(packet, rcp):
    line = encode(packet, rcp=rcp)
    assert line.startswith("2|") and "\n" not in line and check_line(line) == []
    assert decode(line) == packet
    assert from_json(to_json(packet)) == packet
    assert len(line.split("|")) == (14 if rcp else 13)  # pipes in text are escaped


def test_strings_escape_pipes_and_survive_any_text():
    text = 'a | b "q" \\ \n tab\t é 😀 (paren) )'
    line = encode(Receipt(id="r_1", thread="t", brief="k_1", summary=text))
    assert decode(line).summary == text
    assert parse(dump([["110", Str(text)]]))[0][1] == text


def test_upstream_lines_still_check_with_the_vendored_validator():
    assert check_line(UPSTREAM_BRIEF) == []
    assert sym2p.validate(UPSTREAM_BRIEF) == []
    with pytest.raises(WireError, match="not a brief or a receipt"):
        decode(UPSTREAM_BRIEF.replace("|1|1|1|88|", "|1|1|3|88|").replace("|1|mr1:v1|", "|0|mr1:v1|"))


def test_upstream_validator_rejects_exo_lines_loudly():
    errs = sym2p.validate(encode(BRIEF))
    assert errs and any("100" in e or "BODY" in e for e in errs)


@pytest.mark.parametrize("mutate, code", [
    (lambda ln: ln.replace("(101 ", "(101  "), "CANON"),
    (lambda ln: ln.replace("(100 1)", "(100 2)"), "VER"),
    (lambda ln: ln.replace("(105 1 2)", "(199 1 2)"), "BODY"),
    (lambda ln: ln.replace("(105 1 2)", "(105 x 2)"), "BODY"),
    (lambda ln: ln.replace("(105 1 2)", '(10 "s" (20 sec (3 5)))'), "BODY"),
    (lambda ln: ln.replace("|4|11|", "|9|11|"), "CODE"),
    (lambda ln: ln.replace("|4|11|", "|04|11|"), "TYPE"),
    (lambda ln: ln + " ", "CANON"),
    (lambda ln: ln.replace('"every caller listed"', '"unterminated'), "BODY"),
    (lambda ln: ln.split("|", 1)[1], "SHAPE"),
])
def test_strict_check_rejects(mutate, code):
    errs = check_line(mutate(encode(BRIEF)))
    assert errs and any(f"ERR:{code}" in e for e in errs), errs
    with pytest.raises(WireError):
        decode(mutate(encode(BRIEF)))


def test_checksum_detects_corruption_and_is_never_repaired():
    line = encode(RECEIPT, rcp=True)
    bad = line.replace("|1200 ", "|1201 ").replace("(111 1200 ", "(111 1201 ")
    assert any("ERR:RCP" in e for e in check_line(bad))
    assert repair(bad)[0] is None


def test_repair_fixes_only_safe_damage():
    line = encode(BRIEF)
    fixed, notes = repair(f"Here is the packet:\n```\n{line.replace('|4|11|', '|04|11|')}\n```")
    assert fixed == line and notes
    spaced = line.replace("(103 glob grep read_file)", "(103  glob grep  read_file )")
    assert repair(spaced) == (line, ["canonical body spacing"])
    assert repair("no packet here")[0] is None
    assert repair(line.replace("|4|11|", "|9|11|"))[0] is None  # unknown code: refuse to guess


def test_decode_any_reports_how_it_read():
    line = encode(RECEIPT)
    assert decode_any(line) == (RECEIPT, "line")
    assert decode_any("  " + line + "\n")[1] == "line"
    assert decode_any("```" + line + "```")[1] == "repaired"
    assert decode_any(to_json(RECEIPT)) == (RECEIPT, "json")
    assert decode_any("model says: " + to_json(RECEIPT) + " thanks")[1] == "json"
    with pytest.raises(WireError):
        decode_any("nothing useful")


@pytest.mark.parametrize("bad", [
    {"kind": "receipt", "id": "r_1", "thread": "t", "brief": "k_1", "colour": "red"},
    {"kind": "receipt", "id": "r_1", "thread": "t", "brief": "k_1", "rounds": "3"},
    {"kind": "receipt", "id": "r_1", "thread": "t", "brief": "k_1", "rounds": True},
    {"kind": "receipt", "id": "r_1", "thread": "t", "brief": "k_1", "status": "maybe"},
    {"kind": "brief", "id": "k 1", "thread": "t", "goal": "x"},
    {"kind": "brief", "id": "k_1", "thread": "t", "goal": "x", "tools": ["ok", 3]},
    {"kind": "note", "id": "k_1"},
    {"kind": "brief", "thread": "t", "goal": "x"},
])
def test_json_fallback_is_strict(bad):
    with pytest.raises(WireError):
        from_json(json.dumps(bad))


def test_negotiate_prefers_the_line_and_falls_back_to_json():
    assert negotiate(WIRE_FORMATS, ["json", "sym2p/2+exo1"]) == "sym2p/2+exo1"
    assert negotiate(WIRE_FORMATS, ["json"]) == "json"
    assert negotiate(WIRE_FORMATS, ["sym2p/9"]) == "json"


def test_detached_signature(signer):
    line = encode(RECEIPT)
    sig = sign(line, signer)
    assert sig["by"] == "tester" and verify(line, sig, signer.pub)
    assert not verify(line, sig)  # the key the packet claims is never trusted on its own
    assert not verify(line.replace("Partial", "Complete"), sig, signer.pub)
    other = crypto.Signer("other", crypto.keygen())
    assert not verify(line, sig, trusted_pub=other.pub)  # a trusted key overrides the key the packet claims


def test_english_render_is_deterministic_and_never_shows_codes():
    for p in (BRIEF, RECEIPT):
        text = render(p)
        assert text == render(encode(p)) == render(to_json(p))
        assert "|" not in text.replace(" | ", "") and "(10" not in text and "2|" not in text
    assert render(BRIEF).startswith('Task k_3f9a for a scout: Find callers of "load_config" | list file:line')
    assert "Limits: 12 tool rounds, 60,000 tokens, 300 seconds." in render(BRIEF)
    r = render(RECEIPT)
    assert r.startswith("Failed: task k_3f9a by a scout in 3.1 s, 3 tool calls over 2 rounds, 1,200 tokens in")
    assert "Reason: timeout: no result within 300 s." in r and "Files changed: a.py, dir/b c.py." in r


def test_every_status_and_opcode_is_covered():
    for st in TASK_STATUSES:
        p = Receipt(id="r_1", thread="t", brief="k_1", status=st)
        assert decode(encode(p)).status == st and render(p).split(":")[0].lower() == st
    used = {int(e[0]) for p in (BRIEF, RECEIPT) for e in parse(encode(p).split("|")[9])}
    assert used == set(OPS), set(OPS) - used
    assert {STATES[c] for c in (100, 101)} == {"failed", "cancelled"}


def test_task_statuses_match_the_protocol():
    assert "queued, running, done, failed, cancelled" in inspect.getsource(s.TaskUpdated)
    assert tuple(TASK_STATUSES) == ("queued", "running", "done", "failed", "cancelled")
    assert s.TaskUpdated(task_id="k_1").packet == ""  # additive field, default keeps old senders valid


def test_cli_renders_and_checks(capsys):
    from exo.cli import main

    assert main(["wire", "render", encode(RECEIPT)]) == 0
    assert capsys.readouterr().out.startswith("Failed: task k_3f9a by a scout")
    assert main(["wire", "check", to_json(BRIEF)]) == 0
    assert "ok after json" in capsys.readouterr().out
    assert main(["wire", "check", "2|broken"]) == 1
    assert "invalid: ERR:SHAPE" in capsys.readouterr().out


def test_exo_lines_travel_as_bus_bodies():
    from exo.protocols.sym2p import bus

    line = encode(RECEIPT, rcp=True)
    body = bus.wrap(line)
    assert bus.unwrap(body) == line
    assert bus.render_body(body) == render(RECEIPT)
    assert bus.render_body(bus.wrap(UPSTREAM_BRIEF)).startswith("assign b441")  # upstream path unchanged
    with pytest.raises(sym2p.Sym2pError):
        bus.wrap(line.replace("(100 1)", "(100 1)(199 2)"))
    broken = json.dumps({"data": line.replace("|100|40|", "|99|40|"), "fmt": "sym2p"}, separators=(",", ":"))
    assert bus.render_body(broken).startswith("[unreadable SYM-2P packet")
