"""exo.protocols.sym2p: vendored validator vectors, encode/decode, bus body helpers.

Fixtures in sym2p_fixtures/ are copied from aska-digital/protean-sym2p at
2d93ed603e94ddd3e4ddcab4b1d5fc4227832a1b (tests/fixtures, examples, templates).
"""
import json
from pathlib import Path

import pytest

from exo import privacy
from exo.protocols import sym2p
from exo.protocols.sym2p import Sym2pError, bus, v1, v2

DENY_WORDS = privacy.load_denylist()  # untracked file, read at collection time
FIX = Path(__file__).parent / "sym2p_fixtures"
STATE = json.loads((FIX / "state" / "receiver-state.json").read_text())
STATE = {k: v["v"] for k, v in STATE.items()}
BRIEF_V2 = (FIX / "v2" / "brief.sym2").read_text().strip()


def codes(findings):
    return {f["code"] for f in findings}


def docs(path):
    """Mirror the source CLI split: .jsonl is one doc per line, else whole file first."""
    text = path.read_text(encoding="utf-8")
    if path.suffix != ".jsonl":
        try:
            if isinstance(json.loads(text), dict):
                return [text.strip()]
        except ValueError:
            pass
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def run(path, **opts):
    """Validate every document in a fixture with one run state. Returns (findings, warnings)."""
    allow = opts.pop("allow_duplicates", False)
    rs = v1.RunState(allow_duplicates=allow)
    findings, warnings = [], []
    for i, text in enumerate(docs(path)):
        f, w = v1.validate_text(text, run_state=rs, where=f"{path.name}:{i + 1}", **opts)
        findings += f
        warnings += w
    return findings, warnings


# --- ported validator cases (source tests/run-tests.py CASES) ----------------

VALID = [
    ("brief-packet.json", {}),
    ("receipt-packet.json", {}),
    ("review-fix-verify-merge.jsonl", {}),
    ("review-fix-verify-merge.jsonl", {"strict_canonical": True}),
    ("review-fix-verify-merge.jsonl", {"verify_hashes": True}),
    ("valid/packet-assert.json", {}),
    ("valid/packet-error-ver.json", {}),
    ("valid/object-dispute-open.json", {}),
    ("valid/object-dispute-open.json", {"verify_hashes": True}),
    ("valid/packet-update-current.json", {"state": STATE}),
    ("invalid/object-hash-mismatch.json", {}),  # hash check is opt-in
]

INVALID = [
    ("invalid/syntax-truncated.jsonl", {}, "ERR:SYN"),
    ("invalid/duplicate-keys.json", {}, "ERR:SYN"),
    ("invalid/bad-id-grammar.json", {}, "ERR:SYN"),
    ("invalid/float-score.json", {}, "ERR:SYN"),
    ("invalid/unknown-act.json", {}, "ERR:SYN"),
    ("invalid/extra-property.json", {}, "ERR:SYN"),
    ("invalid/reordered-keys.json", {}, "ERR:SYN"),
    ("invalid/multi-recipient.json", {}, "ERR:POL"),
    ("invalid/wrong-major-version.json", {}, "ERR:SYN"),
    ("invalid/missing-auth-exec.json", {}, "ERR:AUTH"),
    ("invalid/challenge-without-dissent.json", {}, "ERR:SEM"),
    ("invalid/verify-without-evidence.json", {}, "ERR:SEM"),
    ("invalid/update-without-base.json", {}, "ERR:SYN"),
    ("invalid/protected-dispute-omission.json", {}, "ERR:SYN"),
    ("invalid/drop-losing-position.json", {}, "ERR:SEM"),
    ("invalid/expired-authorization.json", {"now": "2026-09-17T03:00:00Z"}, "ERR:EXP"),
    ("invalid/stale-base.jsonl", {"state": STATE}, "ERR:VER"),
    ("invalid/duplicate-packet.jsonl", {}, "ERR:DUP"),
    ("invalid/noncanonical-whitespace.jsonl", {"strict_canonical": True}, "ERR:SYN"),
    ("invalid/object-hash-mismatch.json", {"verify_hashes": True}, "ERR:SYN"),
]


@pytest.mark.parametrize("name,opts", VALID, ids=[f"{n}-{sorted(o)}" for n, o in VALID])
def test_valid_vectors(name, opts):
    findings, _ = run(FIX / name, **opts)
    assert findings == []


@pytest.mark.parametrize("name,opts,code", INVALID, ids=[n for n, _, _ in INVALID])
def test_invalid_vectors(name, opts, code):
    findings, _ = run(FIX / name, **opts)
    assert code in codes(findings), findings


def test_duplicates_downgrade_to_warning_when_allowed():
    findings, warnings = run(FIX / "invalid/duplicate-packet.jsonl", allow_duplicates=True)
    assert findings == [] and "ERR:DUP" in codes(warnings)


def test_every_fixture_file_is_covered():
    covered = {n for n, _ in VALID} | {n for n, _, _ in INVALID}
    for p in (FIX / "valid").iterdir():
        assert f"valid/{p.name}" in covered
    for p in (FIX / "invalid").iterdir():
        assert f"invalid/{p.name}" in covered


# --- ported v2 cases (source tests/test_sym2.py) ------------------------------

def test_v2_templates_validate_and_render_as_published():
    for p in sorted((FIX / "v2").glob("*.sym2")):
        line = p.read_text().strip()
        assert sym2p.validate(line) == [], p.name
        expected = p.with_suffix(".rendered.txt").read_text().strip()
        assert sym2p.render(line) == expected, p.name


@pytest.mark.parametrize("mutate,needle", [
    (lambda s: s.replace("|1|2|1|", "|1|2|99|", 1), "ERR:CODE"),
    (lambda s: s.replace("(3 5))", "(3 5)"), "unbalanced"),
    (lambda s: s.replace("(10 sym-2p/1.0", "(99 sym-2p/1.0"), "unknown opcode 99"),
    (lambda s: s.replace("(20 sec (3 5))", "(20 sec (3 5 7))"), "range wants"),
    (lambda s: s.replace("|88|70|", "|101|70|", 1), "ERR:RANGE"),
    (lambda s: "2|b441|mr1", "ERR:SHAPE"),
    (lambda s: s.replace("(10 sym-2p/1.0", "(10  sym-2p/1.0"), "ERR:CANON"),
    (lambda s: s + "|0000000000000000", "ERR:RCP"),
    (lambda s: s.replace("(12 (22 validator 0 1))", "(31 (34 $cov 70) (20 sec 6) ())"), "ERR:SCOPE"),
])
def test_v2_rejections(mutate, needle):
    errs = sym2p.validate(mutate(BRIEF_V2))
    assert any(needle in e for e in errs), errs


def test_v2_single_atom_range_ok():
    assert sym2p.validate(BRIEF_V2.replace("(20 sec (3 5))", "(20 sec 5)")) == []


def test_migrate_v1_brief_to_v2_line():
    packet = json.loads((FIX / "brief-packet.json").read_text())
    line, err = v2.migrate(packet)
    assert err is None
    assert line == ("2|b441|mr1|1|2|1|1|88|70|(10 sym-2p/1.0 (20 sec (3 5)))(11 (21 *))"
                    "(12 (22 validator 0 1))|1|mr1:v1|40")
    line_rcp, _ = v2.migrate(packet, rcp=True)
    assert line_rcp == (FIX / "v2" / "brief-rcp.sym2").read_text().strip()
    custom = dict(packet, f="lead")
    assert v2.migrate(custom)[0] is None
    assert v2.migrate(custom, roles={"lead": 1})[0] == line


# --- encode_brief / decode / render -------------------------------------------

def test_encode_brief_matches_published_template_and_round_trips():
    line = sym2p.encode_brief(id="b441", tk="mr1", f="coordinator", t="builder", a="assign",
                              st="proposed", cf=88, cv=70, scope=("sym-2p/1.0", 3, 5),
                              nomod="*", accept=("validator", 0, 1), rq="ack",
                              base="mr1:v1", exp=40)
    assert line == BRIEF_V2
    d = sym2p.decode(line)
    assert d["profile"] == "2.0" and d["id"] == "b441" and d["f"] == 1 and d["t"] == 2
    assert d["a_name"] == "assign" and d["rq_name"] == "acknowledgement required"
    assert d["exprs"][0] == ["10", "sym-2p/1.0", ["20", "sec", ["3", "5"]]]
    assert d["sum"] is None
    again = sym2p.encode_brief(id=d["id"], tk=d["tk"], f=d["f"], t=d["t"], a=d["a"], st=d["st"],
                               cf=d["cf"], cv=d["cv"], body=d["body"], rq=d["rq"],
                               base=d["base"], exp=d["exp"])
    assert again == line


def test_encode_brief_rcp_and_codes():
    line = sym2p.encode_brief(id="x1", tk="t1", f=1, t=7, a=1, body="(32 s (33 3 4 5) (20 sec s))",
                              rcp=True)
    assert len(line.split("|")) == 14 and sym2p.decode(line)["sum"] == line.split("|")[-1]
    assert "For each s in [3, 4, 5]" in sym2p.render(line)


def test_encode_brief_rejects_bad_input():
    with pytest.raises(Sym2pError):
        sym2p.encode_brief(id="b1", tk="t", a="shout")
    with pytest.raises(Sym2pError) as e:
        sym2p.encode_brief(id="b1", tk="t", cf=150)
    assert any("ERR:RANGE" in x for x in e.value.errors)
    with pytest.raises(Sym2pError):
        sym2p.encode_brief(id="b1", tk="t", body="(99 nope)")


def test_decode_v1_and_invalid():
    text = (FIX / "valid" / "packet-assert.json").read_text().strip()
    assert sym2p.decode(text)["packet"]["a"] == json.loads(text)["a"]
    with pytest.raises(Sym2pError):
        sym2p.decode((FIX / "invalid" / "unknown-act.json").read_text())
    with pytest.raises(Sym2pError):
        sym2p.decode("hello world")


def test_render_v1_plain_english_with_party_names():
    packet = json.loads((FIX / "receipt-packet.json").read_text())
    text = sym2p.render(packet, roles={"builder": "Builder seat", "cos": "Coordinator"})
    assert text.startswith("done r512 [Builder seat -> Coordinator] (verified)")
    assert "Evidence: e21:v1, e23:v1." in text and "Requires: verification." in text
    assert "{" not in text


def test_render_v2_party_names():
    text = sym2p.render(BRIEF_V2, roles={1: "Lead", 2: "Maker"})
    assert text.splitlines()[0] == "assign b441 [Lead -> Maker] (proposed) confidence 88, coverage 70"


def test_validate_accepts_dict_and_unknown_text():
    assert sym2p.validate(json.loads((FIX / "brief-packet.json").read_text())) == []
    assert sym2p.validate("just words")[0].startswith("ERR:SYN")
    assert sym2p.validate(" " + BRIEF_V2)[0].startswith("ERR:CANON")


# --- bus body helpers ---------------------------------------------------------

def test_wrap_is_canonical_json_and_unwraps():
    body = bus.wrap(BRIEF_V2)
    assert body == json.dumps({"data": BRIEF_V2, "fmt": "sym2p"}, sort_keys=True,
                              separators=(",", ":"), ensure_ascii=False)
    assert body.startswith('{"data":') and bus.BODY_FMT == "sym2p"
    assert bus.unwrap(body) == BRIEF_V2
    v1_line = (FIX / "valid" / "packet-assert.json").read_text().strip()
    assert bus.unwrap(bus.wrap(v1_line)) == v1_line


def test_wrap_rejects_invalid_packets():
    with pytest.raises(Sym2pError):
        bus.wrap(BRIEF_V2.replace("|1|2|1|", "|1|2|99|", 1))
    with pytest.raises(Sym2pError):
        bus.wrap("hello")
    with pytest.raises(Sym2pError):
        bus.wrap({"v": 2})


@pytest.mark.parametrize("body", ["plain text", "", "{not json", '{"fmt":"sym2p"}',
                                  '{"data":"x","fmt":"other"}', '{"data":1,"fmt":"sym2p"}',
                                  '{"data":"x","fmt":"sym2p","extra":1}', "[1,2]"])
def test_unwrap_ignores_other_bodies(body):
    assert bus.unwrap(body) is None
    assert bus.render_body(body) == body


def test_render_body_plain_english():
    text = bus.render_body(bus.wrap(BRIEF_V2))
    assert text.startswith("assign b441 [coordinator -> builder]") and "|" not in text
    bad = json.dumps({"data": "2|bad", "fmt": "sym2p"}, sort_keys=True, separators=(",", ":"))
    assert bus.render_body(bad).startswith("[unreadable SYM-2P packet")


def test_no_principal_names_in_library():
    deny = DENY_WORDS
    if not deny:
        pytest.skip("no privacy deny-list (EXO_PRIVACY_DENYLIST)")
    pkg = Path(sym2p.__file__).parent
    for p in pkg.iterdir():
        if p.suffix == ".py":
            low = p.read_text().lower()
            assert not [w for w in deny if w in low], p.name
