import os
import stat
from pathlib import Path

import pytest

from exo import onboard as ob
from exo.config import Config
from exo.ledger import Ledger
from exo.store import PrincipalStore

ANSWERS = """answers:
  identity.name: Sam
  identity.role: "I run a small design studio and also advise two clients."
  identity.orgs-detail: Act for the studio only.
  work.projects: The studio website and a client rebrand.
  work.tools: "A laptop, a shared drive and a chat app."
  rights.keep: Spend and anything public.
  comm.style: Short and plain.
  comm.report: Outcome first, three lines.
  gates.public: Never without approval.
  privacy.never-read: My personal email.
  rhythm.hours: "Weekdays 9 to 6."
  quality.done: Tested and checked by me.
"""


def test_bank_is_generic_and_large_enough():
    bank = ob.load_bank()
    sections = {q.section for q in bank.questions}
    assert {"identity", "work", "decision-rights", "communication", "quality-bar", "gates", "rhythm",
            "privacy"} <= sections
    total = len(bank.questions) + sum(len(q.followups) for q in bank.questions)
    assert total >= 20                          # principle 13: draft 20+ candidates
    for f in ob.bank_dir().glob("*.yaml"):
        text = f.read_text().lower()
        assert "hafiz" not in text and "ahraz" not in text


def test_scoring_skips_known_and_spreads_sections():
    bank = ob.load_bank()
    q = next(x for x in bank.questions if x.id == "identity.name")
    assert ob.score(q, set()) > 0
    assert ob.score(q, {"identity.name"}) == 0
    order = ob.plan(bank, set(), 8)
    assert len({q.section for q, _ in order}) == 8      # first pass covers every section once
    assert [s for _, s in order] == sorted([s for _, s in order], reverse=True)
    assert ob.plan(bank, set(), 8) == order             # deterministic


def test_generator_seam_adds_questions():
    class Gen(ob.Generator):
        def draft(self, bank, known):
            return [ob.Question(id="model.q1", section="work", ask="A drafted question?", weight=5,
                                imagined=("a", "b", "c", "d"), rank=0)]

    order = ob.plan(ob.load_bank(), set(), 3, Gen())
    assert order[0][0].id == "model.q1"


def test_init_interview_non_interactive_passes_lint(tmp_path, capsys):
    answers = tmp_path / "answers.yaml"
    answers.write_text(ANSWERS)
    assert ob.main(["init", "sam", "--interview", "--answers", str(answers)]) == 0
    out = capsys.readouterr().out
    assert "12 answered" in out
    store = PrincipalStore.for_principal("sam")
    report = store.lint()
    assert report.errors == [], report.render()

    ident = store.get("person/identity")
    assert ident["kind"] == "a-said" and ident["confidence"] == "low"
    assert '"Sam"' in ident["_body"]
    assert store.get("relationship/reporting")["kind"] == "a-said"     # per-question node override

    led = Ledger(store.ledger_path)
    answers_ev = list(led.iter(kind="answer"))
    questions_ev = list(led.iter(kind="question"))
    assert led.verify()["ok"]
    led.close()
    assert len(answers_ev) == 12 and len(questions_ev) == 12
    assert all(a["author"] == "sam" and a["refs"] for a in answers_ev)
    assert {a["body"]["question_id"] for a in answers_ev} >= {"identity.name", "identity.orgs-detail"}
    assert Config.load().principal == "sam"
    assert oct(store.ledger_path.stat().st_mode & 0o777) == "0o600"

    from exo.ask import QuestionQueue
    queued = QuestionQueue(store.root).state()
    assert queued and all(k.startswith("onboard-") for k in queued)

    # a second interview skips what is known and does not break the store
    assert ob.main(["interview", "--principal", "sam", "--answers", str(answers), "--seed-queue", "0"]) == 0
    led = Ledger(store.ledger_path)
    assert len(list(led.iter(kind="answer"))) == 12
    led.close()


def test_secret_answer_stays_out_of_nodes(tmp_path):
    store = ob.create_store("kim")
    res = ob.run_interview(store, "kim", ob.file_asker({"work.tools": "laptop, api_key = abcdefghijklmnop1234"}),
                           max_questions=0, count_skips=False, seed=0)
    assert res["answered"] == 1 and res["lint"].ok
    assert "abcdefghijklmnop" not in (store.root / "nodes/systems/environment.md").read_text()


def test_tty_asker_skip_and_stop(tmp_path):
    store = ob.create_store("lee")
    replies = iter(["Lee", "", "q"])
    ask = ob.tty_asker(inp=lambda prompt: next(replies), out=open(os.devnull, "w"))
    res = ob.run_interview(store, "lee", ask, max_questions=12, seed=0)
    assert res["answered"] == 1 and res["asked"] == 2


def _hafiz_like(root: Path) -> Path:
    (root / "nodes/person").mkdir(parents=True)
    (root / "INDEX.md").write_text("# INDEX\n\n<!-- NODES:BEGIN -->\n<!-- NODES:END -->\n")
    (root / "PRINCIPAL.md").write_text("# PRINCIPAL: Test\n")
    (root / "nodes/person/identity.md").write_text(
        "---\nid: person/identity\ntitle: Who\ntype: fact\ndomain: person\nsummary: s\nload_when: w\n"
        "confidence: high\nstability: core\nvalid_from: 2026-01-01\nsources:\n  - {kind: observed, ref: x}\n"
        "links: []\n---\nBody.\n")
    (root / "tools").mkdir()
    (root / "tools/kb.py").write_text("print('kb')\n")
    (root / "corpus/private").mkdir(parents=True)
    (root / "corpus/private/dialogue.jsonl").write_text('{"secret": true}\n')
    (root / "corpus/sources").mkdir()
    (root / "corpus/sources/notes.md").write_text("notes\n")
    (root / ".git").mkdir()
    (root / ".git/HEAD").write_text("ref: refs/heads/main\n")
    (root / "agent.key").write_text("k")
    (root / "link.md").symlink_to(root / "corpus/private/dialogue.jsonl")
    return root


def test_import_store_copies_without_private(tmp_path, capsys):
    src = _hafiz_like(tmp_path / "kb")
    before = sorted(p.relative_to(src).as_posix() for p in src.rglob("*"))
    assert ob.main(["import-store", str(src), "--id", "pat"]) == 0
    dest = PrincipalStore.for_principal("pat").root
    assert (dest / "nodes/person/identity.md").is_file()
    assert (dest / "tools/kb.py").is_file() and (dest / "corpus/sources/notes.md").is_file()
    assert not (dest / "corpus/private/dialogue.jsonl").exists()
    assert (dest / "corpus/private").is_dir()
    assert stat.S_IMODE((dest / "corpus/private").stat().st_mode) == 0o700
    assert not (dest / ".git").exists() and not (dest / "agent.key").exists() and not (dest / "link.md").exists()
    assert (dest / "policies/gates.yaml").is_file()                       # filled from the template
    assert sorted(p.relative_to(src).as_posix() for p in src.rglob("*")) == before   # source untouched
    assert (src / "corpus/private/dialogue.jsonl").read_text() == '{"secret": true}\n'
    led = Ledger(dest / "ledger.sqlite")
    ev = list(led.iter(kind="ingest"))
    led.close()
    assert ev and ev[0]["body"]["adapter"] == "import-store" and "corpus/private" in ev[0]["body"]["skipped"]
    assert Config.load().principal == "pat"
    assert "corpus/private was not copied" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        ob.main(["import-store", str(src), "--id", "pat"])             # never overwrites


def test_import_store_include_private(tmp_path):
    src = _hafiz_like(tmp_path / "kb")
    st = ob.import_store(src, tmp_path / "out", include_private=True)
    assert (tmp_path / "out/corpus/private/dialogue.jsonl").read_text() == '{"secret": true}\n'
    assert st["lint"].errors == []


def test_import_store_rejects_non_store(tmp_path):
    (tmp_path / "x").mkdir()
    with pytest.raises(ValueError):
        ob.import_store(tmp_path / "x", tmp_path / "y")
