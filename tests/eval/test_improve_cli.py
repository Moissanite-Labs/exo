"""Real CLI path, trusted fixture computations, held-out acceptance and export policy."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from exo import cli
from exo.improvement import offline as O

ROOT = Path(__file__).resolve().parents[2]


def fixture(adapter="text-normalize-v1"):
    name = "text.json" if adapter == "text-normalize-v1" else "ledger.json"
    return json.loads((ROOT / "examples" / "improvement" / name).read_text())


def invoke(tmp_path, monkeypatch, capsys, data=None, adapter="text-normalize-v1", extra=()):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "public.json").write_text(json.dumps(data if data is not None else fixture(adapter)))
    result = cli.main(["improve", "--offline", "--fixture", "public.json", "--adapter", adapter, *extra])
    captured = capsys.readouterr()
    return result, json.loads(captured.out) if captured.out else None, captured.err


@pytest.mark.parametrize("adapter", O.ADAPTERS)
def test_real_cli_round_trip_disjoint_gate_and_typed_export(tmp_path, monkeypatch, capsys, adapter):
    code, report, err = invoke(tmp_path, monkeypatch, capsys, adapter=adapter, extra=("--output", "trial-one"))
    assert code == 0 and not err
    assert report["status"] == "review_ready" and report["calls"] == 15 and report["activated"] is False
    assert report["held_out"]["comparison"]["delta"] == 1
    proposal = report["proposal"]
    assert proposal["status"] == "review_only" and proposal["kind"] == "harness_notes"
    assert proposal["development_evidence"] == report["development"]["artifact_digest"]
    assert proposal["held_out_evidence"] == report["held_out"]["evidence_digest"]
    exported = tmp_path / "improvement-runs" / "trial-one" / "artifact.json"
    assert json.loads(exported.read_text()) == report
    assert exported.stat().st_mode & 0o777 == 0o600
    assert exported.parent.stat().st_mode & 0o777 == 0o700
    assert O._digest({k: v for k, v in report.items() if k != "artifact_digest"}) == report["artifact_digest"]
    assert not (tmp_path / "exo-home").exists()


def test_subprocess_command_needs_no_active_principal_or_provider(tmp_path):
    (tmp_path / "public.json").write_text(json.dumps(fixture()))
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "EXO_HOME": str(tmp_path / "never-open")}
    result = subprocess.run([sys.executable, "-m", "exo.cli", "improve", "--offline", "--adapter",
                             "text-normalize-v1", "--fixture", "public.json"], cwd=tmp_path,
                            env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "review_ready"
    assert not (tmp_path / "improvement-runs").exists()
    assert not (tmp_path / "never-open").exists()


def test_no_store_or_config_authority_is_loaded(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("private store access")
    monkeypatch.setattr(cli, "resolve_store", forbidden)
    monkeypatch.setattr("exo.config.Config.load", forbidden)
    monkeypatch.setattr("exo.store.explicit_roots", forbidden)
    assert invoke(tmp_path, monkeypatch, capsys)[0] == 0


@pytest.mark.parametrize("mutation", [
    lambda f: f.update(callback="os:system"),
    lambda f: f.update(classification="private"),
    lambda f: f.update(namespace="../principal"),
    lambda f: f.update(schema=True),
    lambda f: f["baseline"].update(permissions="all"),
    lambda f: f["baseline"].update(notes="__import__('os').system('touch sentinel')"),
    lambda f: f.update(development=[]),
    lambda f: f.update(held_out=[]),
    lambda f: f["development"][0].update(domain="principal"),
    lambda f: f["development"][0].update(id="fixture:fake:ledger"),
    lambda f: f["development"][0].update(input="x" * 8001),
    lambda f: f["development"][0].update(script="run me"),
    lambda f: f["references"].pop(next(iter(f["references"]))),
])
def test_malformed_or_authority_fields_rejected(tmp_path, monkeypatch, capsys, mutation):
    data = fixture()
    mutation(data)
    code, report, err = invoke(tmp_path, monkeypatch, capsys, data, extra=("--output", "rejected"))
    assert code == 2 and report is None and "invalid public fixture" in err
    assert not (tmp_path / "sentinel").exists()
    assert not (tmp_path / "improvement-runs").exists()


@pytest.mark.parametrize("overlap", ["id", "input", "normalized"])
def test_split_overlap_rejected(tmp_path, monkeypatch, capsys, overlap):
    data = fixture()
    if overlap == "id":
        data["held_out"][0]["id"] = data["development"][0]["id"]
    else:
        data["held_out"][0]["input"] = data["development"][0]["input"]
        if overlap == "normalized":
            data["held_out"][0]["input"] = data["held_out"][0]["input"].upper().strip()
    assert invoke(tmp_path, monkeypatch, capsys, data)[0] == 2


def test_numeric_permutation_and_equivalent_decimal_overlap_rejected(tmp_path, monkeypatch, capsys):
    data = fixture("signed-decimal-v1")
    data["held_out"][0]["input"] = "5,-20.100,100.250"
    assert invoke(tmp_path, monkeypatch, capsys, data, adapter="signed-decimal-v1")[0] == 2


@pytest.mark.parametrize("bad", ["NaN", '{"schema":1,"schema":1}', "[", '"string"'])
def test_bad_json(tmp_path, monkeypatch, capsys, bad):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "public.json").write_text(bad)
    assert cli.main(["improve", "--offline", "--adapter", O.ADAPTERS[0], "--fixture", "public.json"]) == 2
    assert not capsys.readouterr().out


@pytest.mark.parametrize("name", ["../outside", "/tmp/run", ".exo", "bad.json", "a/b", "UPPER"])
def test_output_name_cannot_escape_or_name_state(tmp_path, monkeypatch, capsys, name):
    assert invoke(tmp_path, monkeypatch, capsys, extra=("--output", name))[0] == 2
    assert not (tmp_path / "improvement-runs").exists()


def test_no_overwrite_or_linked_output(tmp_path, monkeypatch, capsys):
    assert invoke(tmp_path, monkeypatch, capsys, extra=("--output", "one"))[0] == 0
    original = (tmp_path / "improvement-runs" / "one" / "artifact.json").read_bytes()
    assert invoke(tmp_path, monkeypatch, capsys, extra=("--output", "one"))[0] == 2
    assert (tmp_path / "improvement-runs" / "one" / "artifact.json").read_bytes() == original
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "improvement-runs" / "linked").symlink_to(outside, target_is_directory=True)
    assert invoke(tmp_path, monkeypatch, capsys, extra=("--output", "linked"))[0] == 2
    assert not list(outside.iterdir())


def test_linked_output_parent_rejected(tmp_path, monkeypatch, capsys):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "improvement-runs").symlink_to(outside, target_is_directory=True)
    assert invoke(tmp_path, monkeypatch, capsys, extra=("--output", "one"))[0] == 2
    assert not list(outside.iterdir())


@pytest.mark.parametrize("kind", ["symlink", "fifo", "oversize"])
def test_fixture_must_be_local_bounded_regular_file(tmp_path, monkeypatch, capsys, kind):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "public.json"
    if kind == "symlink":
        other = tmp_path / "other.json"
        other.write_text(json.dumps(fixture()))
        p.symlink_to(other)
    elif kind == "fifo":
        os.mkfifo(p)
    else:
        p.write_text("x" * (O.FILE_CAP + 1))
    assert cli.main(["improve", "--offline", "--adapter", O.ADAPTERS[0], "--fixture", p.name]) == 2
    assert not capsys.readouterr().out


@pytest.mark.parametrize("cap", range(1, 16))
def test_one_cap_includes_development_and_held_out(tmp_path, monkeypatch, capsys, cap):
    code, report, _ = invoke(tmp_path, monkeypatch, capsys, extra=("--max-calls", str(cap)))
    assert report["calls"] <= cap
    assert code == (0 if cap == 15 else 1)
    if cap < 15:
        assert "proposal" not in report and report["status"] == "budget_exhausted"


@pytest.mark.parametrize("extra", [("--max-tasks", "3"), ("--max-calls", "0"),
                                   ("--iterations", "9"), ("--margin", "nan")])
def test_invalid_limits(tmp_path, monkeypatch, capsys, extra):
    assert invoke(tmp_path, monkeypatch, capsys, extra=extra)[0] == 2


def test_held_out_rejection_does_not_export_review_proposal(tmp_path, monkeypatch, capsys):
    data = fixture()
    for task in data["held_out"]:
        data["references"][task["id"]] = task["input"]  # baseline wins on these tasks
    code, report, _ = invoke(tmp_path, monkeypatch, capsys, data)
    assert code == 1 and report["status"] == "held_out_rejected" and "proposal" not in report
    assert report["development"]["status"] == "proposal_ready"


def test_held_out_data_and_references_do_not_reach_improvers(tmp_path, monkeypatch, capsys):
    original = O.callbacks
    histories = []
    def wrapped(adapter, references):
        propose, compose, probe = original(adapter, references)
        history = []
        histories.append(history)
        def editor(c):
            history.append(copy.deepcopy(c))
            return propose(c)
        def composer(c):
            history.append(copy.deepcopy(c))
            return compose(c)
        return editor, composer, probe
    monkeypatch.setattr(O, "callbacks", wrapped)
    first = fixture()
    assert invoke(tmp_path, monkeypatch, capsys, first)[0] == 0
    second = fixture()
    second["references"]["dev-bob"] = "deliberately different hidden reference"
    second["held_out"][0]["input"] = " ERIN Green "
    second["references"]["held-carol"] = "erin green"
    assert invoke(tmp_path, monkeypatch, capsys, second)[0] == 0
    assert histories[0] == histories[1]
    assert "fixture:normalize-names-v1:held:" not in json.dumps(histories)
    assert "references" not in json.dumps(histories)


@pytest.mark.parametrize("bad", [None, {"observation": "x", "score": float("nan"), "rule_ok": True}])
def test_malformed_held_out_evidence_fails_closed(tmp_path, monkeypatch, capsys, bad):
    original = O.callbacks
    def wrapped(adapter, references):
        propose, compose, probe = original(adapter, references)
        def bad_probe(task, harness):
            return bad if ":held:" in task["id"] else probe(task, harness)
        return propose, compose, bad_probe
    monkeypatch.setattr(O, "callbacks", wrapped)
    code, report, _ = invoke(tmp_path, monkeypatch, capsys)
    assert code == 1 and report["status"] == "failed" and "proposal" not in report
    assert report["calls"] <= 15


def test_refuses_principal_workspace(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EXO_HOME", str(tmp_path))
    assert invoke(tmp_path, monkeypatch, capsys)[0] == 2


def test_no_new_gate_allowance():
    from exo.gate import classify
    result = classify({"tool": "bash", "args": {"command": "exo improve --offline --fixture public.json --adapter text-normalize-v1"}})
    assert result.category == "opaque_exec"


def test_cli_rejects_missing_offline_or_callback_options(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for args in (["--adapter", O.ADAPTERS[0], "--fixture", "public.json"],
                 ["--offline", "--adapter", "os:system", "--fixture", "public.json"],
                 ["--offline", "--adapter", O.ADAPTERS[0], "--fixture", "public.json", "--callback", "os:system"],
                 ["--offline", "--adapter", O.ADAPTERS[0], "--fixture", "public.json", "--root", "private"]):
        with pytest.raises(SystemExit) as exc:
            cli.main(["improve", *args])
        assert exc.value.code == 2
    assert not capsys.readouterr().out
    assert not (tmp_path / "private").exists()


def test_fixture_path_and_protected_names_rejected(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for name in ("../public.json", str(tmp_path / "public.json"), "credentials.json"):
        assert cli.main(["improve", "--offline", "--adapter", O.ADAPTERS[0], "--fixture", name]) == 2
    assert not capsys.readouterr().out


def test_export_failure_has_no_partial_report(tmp_path, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise OSError("write failed")
    monkeypatch.setattr("exo.core.private.atomic_write", fail)
    code, report, err = invoke(tmp_path, monkeypatch, capsys, extra=("--output", "failure"))
    assert code == 2 and report is None
    assert not (tmp_path / "improvement-runs" / "failure" / "artifact.json").exists()


def test_deterministic_output_and_no_improvement(tmp_path, monkeypatch, capsys):
    first = invoke(tmp_path, monkeypatch, capsys)[1]
    assert invoke(tmp_path, monkeypatch, capsys)[1] == first
    data = fixture()
    data["baseline"] = {"notes": "trim lower"}
    code, report, _ = invoke(tmp_path, monkeypatch, capsys, data)
    assert code == 1 and report["status"] == "no_improvement" and "proposal" not in report
    assert "held_out" not in report
