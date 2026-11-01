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
WORKSPACE_REFUSED = "exo improve: run only in an explicit public workspace\n"
NEW_RUN_REFUSED = "exo improve: output must be a new run; links and overwrites are refused\n"
RUN_ID_REFUSED = ("exo improve: output must be a run ID: up to 48 lowercase letters, digits and hyphens, "
                  "starting with a letter\n")


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


def test_only_recorded_roots_are_read_without_store_or_runtime_config(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("private store access")
    monkeypatch.setattr(cli, "resolve_store", forbidden)
    monkeypatch.setattr("exo.config.Config.load", forbidden)
    import exo.store as store
    roots = store.explicit_roots
    calls = []
    def recorded_roots(home):
        calls.append(home)
        return roots(home)
    monkeypatch.setattr(store, "explicit_roots", recorded_roots)
    monkeypatch.setattr(store.PrincipalStore, "__init__", forbidden)
    assert invoke(tmp_path, monkeypatch, capsys)[0] == 0
    assert calls == [tmp_path / "exo-home"]


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


@pytest.mark.parametrize("name", ["../outside", "/tmp/run", ".exo", "bad.json", "a/b", "UPPER", "a" * 49])
def test_output_name_cannot_escape_or_name_state(tmp_path, monkeypatch, capsys, name):
    assert invoke(tmp_path, monkeypatch, capsys, extra=("--output", name)) == (2, None, RUN_ID_REFUSED)
    assert not (tmp_path / "improvement-runs").exists()


def test_no_overwrite_or_linked_output(tmp_path, monkeypatch, capsys):
    assert invoke(tmp_path, monkeypatch, capsys, extra=("--output", "one"))[0] == 0
    original = (tmp_path / "improvement-runs" / "one" / "artifact.json").read_bytes()
    assert invoke(tmp_path, monkeypatch, capsys, extra=("--output", "one")) == (2, None, NEW_RUN_REFUSED)
    assert (tmp_path / "improvement-runs" / "one" / "artifact.json").read_bytes() == original
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "improvement-runs" / "linked").symlink_to(outside, target_is_directory=True)
    assert invoke(tmp_path, monkeypatch, capsys, extra=("--output", "linked")) == (2, None, NEW_RUN_REFUSED)
    assert not list(outside.iterdir())


def test_linked_output_parent_rejected(tmp_path, monkeypatch, capsys):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "improvement-runs").symlink_to(outside, target_is_directory=True)
    assert invoke(tmp_path, monkeypatch, capsys, extra=("--output", "one")) == (2, None, NEW_RUN_REFUSED)
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
    assert invoke(tmp_path, monkeypatch, capsys) == (2, None, WORKSPACE_REFUSED)


def test_protected_workspace_is_a_boundary_refusal(tmp_path, monkeypatch, capsys):
    workspace = tmp_path / "credentials"
    workspace.mkdir()
    assert invoke(workspace, monkeypatch, capsys) == (2, None, "exo improve: workspace is a protected location\n")


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


@pytest.mark.parametrize("adapter,baseline,expected", [
    ("text-normalize-v1", "trim", "trim lower"),
    ("text-normalize-v1", "lower", "trim lower"),
    ("signed-decimal-v1", "signed", "signed decimal"),
    ("signed-decimal-v1", "decimal", "signed decimal"),
])
def test_partial_baseline_capabilities_survive_propose_and_compose(tmp_path, monkeypatch, capsys, adapter, baseline, expected):
    data = fixture(adapter)
    data["baseline"] = {"notes": baseline}
    code, report, _ = invoke(tmp_path, monkeypatch, capsys, data, adapter=adapter)
    assert code == 0 and report["proposal"]["notes"] == expected
    for stage in report["development"]["stages"]:
        assert set(baseline.split()) <= set(stage["proposal"]["notes"].split())
    assert report["held_out"]["comparison"]["delta"] > 0


def negative_fixture():
    data = fixture("signed-decimal-v1")
    for task, amounts, total in zip(data["development"] + data["held_out"],
                                  ["1.25,-3.50", "2.25,-5.50", "4.25,-9.50", "7.25,-15.50"],
                                  ["-2.25", "-3.25", "-5.25", "-8.25"]):
        task["input"] = amounts
        data["references"][task["id"]] = "Total: " + total
    return data


@pytest.mark.parametrize("wrong_split", ["development", "held_out"])
def test_wrong_sign_arithmetic_never_passes_gate(tmp_path, monkeypatch, capsys, wrong_split):
    data = negative_fixture()
    for task in data[wrong_split]:
        key = task["id"]
        data["references"][key] = data["references"][key].replace("-", "")
    code, report, _ = invoke(tmp_path, monkeypatch, capsys, data, adapter="signed-decimal-v1")
    assert code == 1 and "proposal" not in report
    assert report["status"] == ("no_improvement" if wrong_split == "development" else "held_out_rejected")
    if wrong_split == "held_out":
        assert all(r["observation"].startswith("Total: -") and r["score"] == 0 for r in report["held_out"]["candidate"]["rows"])


def test_correct_negative_totals_pass(tmp_path, monkeypatch, capsys):
    code, report, _ = invoke(tmp_path, monkeypatch, capsys, negative_fixture(), adapter="signed-decimal-v1")
    assert code == 0 and report["status"] == "review_ready"
    assert all(r["score"] == 1 for r in report["held_out"]["candidate"]["rows"])


@pytest.mark.parametrize("reference", ["Total: 2.25", "Total: -2.250", "Total: -2.2", "Total -2.25", "total: -2.25"])
def test_arithmetic_grader_preserves_sign_and_format(reference):
    probe = O.callbacks("signed-decimal-v1", {"x": reference})[2]
    result = probe({"id": "x", "input": "1.25,-3.50"}, {"notes": "signed decimal"})
    assert result["observation"] == "Total: -2.25" and result["score"] == 0


def test_failed_export_releases_empty_reservation_and_can_retry(tmp_path, monkeypatch, capsys):
    import exo.core.private as private
    writer = private.atomic_write
    def fail(*args, **kwargs):
        raise OSError("injected write failure")
    monkeypatch.setattr(private, "atomic_write", fail)
    assert invoke(tmp_path, monkeypatch, capsys, extra=("--output", "retry"))[0] == 2
    assert not (tmp_path / "improvement-runs" / "retry").exists()
    monkeypatch.setattr(private, "atomic_write", writer)
    assert invoke(tmp_path, monkeypatch, capsys, extra=("--output", "retry"))[0] == 0


def test_failed_export_preserves_nonempty_reservation(tmp_path, monkeypatch, capsys):
    def fail(path, *args, **kwargs):
        (path.parent / "keep.txt").write_text("preserve unexpected contents")
        raise OSError("injected write failure")
    monkeypatch.setattr("exo.core.private.atomic_write", fail)
    assert invoke(tmp_path, monkeypatch, capsys, extra=("--output", "nonempty"))[0] == 2
    assert (tmp_path / "improvement-runs" / "nonempty" / "keep.txt").read_text() == "preserve unexpected contents"
    assert invoke(tmp_path, monkeypatch, capsys, extra=("--output", "nonempty")) == (2, None, NEW_RUN_REFUSED)


@pytest.mark.parametrize("nested", [False, True])
def test_refuses_recorded_explicit_principal_root_before_fixture_read(tmp_path, monkeypatch, capsys, nested):
    home = tmp_path / "exo-home"
    home.mkdir()
    store = tmp_path / "stores" / "alice"
    cwd = store / "reports" if nested else store
    cwd.mkdir(parents=True)
    (home / "config.json").write_text(json.dumps({"principals": {"alice": str(store)}}))
    monkeypatch.chdir(cwd)
    def forbidden(*args, **kwargs):
        raise AssertionError("fixture or store content must not be read")
    monkeypatch.setattr(O, "load_fixture", forbidden)
    monkeypatch.setattr("exo.store.PrincipalStore.__init__", forbidden)
    code = cli.main(["improve", "--offline", "--adapter", "text-normalize-v1",
                     "--fixture", "public.json", "--output", "rejected"])
    captured = capsys.readouterr()
    assert code == 2 and not captured.out and captured.err == WORKSPACE_REFUSED
    assert not (cwd / "improvement-runs").exists()


@pytest.mark.parametrize("variant", ["root", "descendant", "home", "unicode"])
def test_case_variant_spelling_of_a_protected_location_is_refused(tmp_path, monkeypatch, capsys, variant):
    # A case-insensitive volume (default APFS) opens one folder under both spellings, and path
    # resolution keeps the spelling as written. On a case-sensitive volume the variant is made as
    # its own folder here, and the check refuses that too: the fail-closed reading, as in D-087.
    home = tmp_path / "exo-home"
    home.mkdir()
    spelled, recorded = ("caf\u00e9", "CAFE\u0301") if variant == "unicode" else ("alice", "ALICE")
    store = tmp_path / "stores" / spelled
    cwd = store / "reports" if variant == "descendant" else store
    cwd.mkdir(parents=True)
    alias = tmp_path / "Stores" / recorded
    alias.mkdir(parents=True, exist_ok=True)
    if variant == "home":
        monkeypatch.setenv("HOME", str(alias))
    else:
        (home / "config.json").write_text(json.dumps({"principals": {"alice": str(alias)}}))
    monkeypatch.chdir(cwd)
    def forbidden(*args, **kwargs):
        raise AssertionError("fixture must not be read")
    monkeypatch.setattr(O, "load_fixture", forbidden)
    code = cli.main(["improve", "--offline", "--adapter", "text-normalize-v1",
                     "--fixture", "public.json", "--output", "rejected"])
    captured = capsys.readouterr()
    assert code == 2 and not captured.out and captured.err == WORKSPACE_REFUSED
    assert not (cwd / "improvement-runs").exists()


def test_recorded_store_symlink_is_resolved(tmp_path, monkeypatch, capsys):
    home = tmp_path / "exo-home"
    home.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    alias = tmp_path / "registered"
    alias.symlink_to(store, target_is_directory=True)
    (home / "config.json").write_text(json.dumps({"principals": {"alice": str(alias)}}))
    assert invoke(store, monkeypatch, capsys) == (2, None, WORKSPACE_REFUSED)


def test_positive_held_out_mean_cannot_hide_one_individual_regression(tmp_path, monkeypatch, capsys):
    data = fixture()
    # Baseline wins Carol, composition wins Dave and Eve: mean delta +1/3.
    carol = data["held_out"][0]
    data["references"][carol["id"]] = carol["input"]
    data["held_out"].append({"id": "held-eve", "domain": "text", "input": " EVE Black "})
    data["references"]["held-eve"] = "eve black"
    code, report, _ = invoke(tmp_path, monkeypatch, capsys, data)
    held = report["held_out"]
    assert held["comparison"]["better"] and held["comparison"]["delta"] == .3333
    assert held["gate"]["pass"]  # This generic mean/domain gate alone is insufficient.
    assert held["comparison"]["worse"] == ["fixture:normalize-names-v1:held:held-carol"]
    assert code == 1 and report["status"] == "held_out_rejected" and "proposal" not in report


def test_iteration_help_explains_saturation_rollback(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["improve", "--help"])
    assert exc.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "later non-improvement discards earlier proposals" in help_text
    assert "built-in fixtures saturate after one round" in help_text


@pytest.mark.parametrize("kind", ["cycle", "missing"])
def test_unresolvable_recorded_root_is_cli_path_refusal(tmp_path, kind):
    home = tmp_path / "exo-home"
    home.mkdir()
    root = tmp_path / "unresolvable"
    if kind == "cycle":
        root.symlink_to(root)
    (home / "config.json").write_text(json.dumps({"principals": {"alice": str(root)}}))
    (tmp_path / "public.json").write_text(json.dumps(fixture()))
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "EXO_HOME": str(home)}
    result = subprocess.run([sys.executable, "-m", "exo.cli", "improve", "--offline",
                             "--adapter", "text-normalize-v1", "--fixture", "public.json",
                             "--output", "refused"], cwd=tmp_path, env=env,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 2 and not result.stdout
    assert "cannot resolve principal root 'alice'" in result.stderr
    assert "repair or remove principals.alice in EXO_HOME/config.json" in result.stderr
    assert str(root) not in result.stderr and str(home) not in result.stderr
    assert "fixture" not in result.stderr and "Traceback" not in result.stderr
    assert not (tmp_path / "improvement-runs").exists()


def test_unrelated_root_list_runtime_error_is_not_swallowed(tmp_path, monkeypatch, capsys):
    def defect(*args, **kwargs):
        raise RuntimeError("unrelated implementation error")
    monkeypatch.setattr("exo.store.explicit_roots", defect)
    with pytest.raises(RuntimeError, match="unrelated implementation error"):
        invoke(tmp_path, monkeypatch, capsys)


@pytest.mark.parametrize("recorded", ["parent", "parent-alias", "ancestor", "case-variant", "env-store", "env-home"])
def test_output_parent_cannot_be_a_protected_store(tmp_path, monkeypatch, capsys, recorded):
    home = tmp_path / "exo-home"
    home.mkdir()
    workspace = tmp_path / "public"
    workspace.mkdir()
    output_parent = workspace / "improvement-runs"
    output_parent.mkdir()
    root = output_parent
    if recorded == "parent-alias":
        root = tmp_path / "registered-alias"
        root.symlink_to(output_parent, target_is_directory=True)
    elif recorded == "ancestor":
        # Canonicalize a root whose spelling includes an existing child/.. .
        child = output_parent / "existing"
        child.mkdir()
        root = child / ".."
    elif recorded == "case-variant":
        # One folder on a case-insensitive volume, a second folder on a case-sensitive one.
        root = workspace / "Improvement-Runs"
        root.mkdir(exist_ok=True)
    if recorded == "env-store":
        monkeypatch.setenv("EXO_PRINCIPAL_STORE", str(root))
    elif recorded == "env-home":
        monkeypatch.setenv("EXO_HOME", str(root))
    else:
        (home / "config.json").write_text(json.dumps({"principals": {"alice": str(root)}}))
    before = set(output_parent.iterdir())
    monkeypatch.chdir(workspace)
    def forbidden(*args, **kwargs):
        raise AssertionError("protected output must be rejected before fixture reads or writes")
    monkeypatch.setattr(O, "load_fixture", forbidden)
    monkeypatch.setattr("exo.store.PrincipalStore.__init__", forbidden)
    monkeypatch.setattr("exo.core.private.atomic_write", forbidden)
    code = cli.main(["improve", "--offline", "--adapter", "text-normalize-v1",
                     "--fixture", "public.json", "--output", "trial"])
    captured = capsys.readouterr()
    assert code == 2 and not captured.out
    assert "output is inside a protected principal location" in captured.err
    assert str(root) not in captured.err
    assert set(output_parent.iterdir()) == before


@pytest.mark.parametrize("broken", ["home-loop", "recorded-root"])
def test_default_exo_home_diagnostics_name_the_default_home(tmp_path, monkeypatch, capsys, broken):
    home = tmp_path / "home"
    exo = home / ".exo"
    if broken == "home-loop":
        home.mkdir()
        exo.symlink_to(".exo")
        expected = "cannot resolve the default Exo home ~/.exo; repair that path"
    else:
        exo.mkdir(parents=True)
        (exo / "config.json").write_text(json.dumps({"principals": {"alice": str(tmp_path / "unmounted" / "alice")}}))
        expected = "cannot resolve principal root 'alice'; repair or remove principals.alice in ~/.exo/config.json"
    monkeypatch.delenv("EXO_HOME")
    monkeypatch.setenv("HOME", str(home))
    workspace = tmp_path / "public"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    def forbidden(*args, **kwargs):
        raise AssertionError("fixture must not be read")
    monkeypatch.setattr(O, "load_fixture", forbidden)
    code = cli.main(["improve", "--offline", "--adapter", "text-normalize-v1",
                     "--fixture", "public.json", "--output", "refused"])
    captured = capsys.readouterr()
    assert code == 2 and not captured.out and captured.err == f"exo improve: {expected}\n"
    assert "EXO_HOME" not in captured.err and str(tmp_path) not in captured.err
    assert not (workspace / "improvement-runs").exists()


@pytest.mark.parametrize("variable", ["EXO_HOME", "EXO_PRINCIPAL_STORE"])
@pytest.mark.parametrize("masked", [False, True])
def test_environment_root_cycles_are_cli_path_refusals(tmp_path, variable, masked):
    loop = tmp_path / "private-loop"
    loop.symlink_to(loop)
    (tmp_path / "public.json").write_text(json.dumps(fixture()))
    value = tmp_path / "missing" / ".." / "private-loop" if masked else loop
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), variable: str(value)}
    result = subprocess.run([sys.executable, "-m", "exo.cli", "improve", "--offline",
                             "--adapter", "text-normalize-v1", "--fixture", "public.json",
                             "--output", "refused"], cwd=tmp_path, env=env,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 2 and not result.stdout
    assert f"cannot resolve {variable}" in result.stderr
    assert "repair its path or unset that environment variable" in result.stderr
    assert str(loop) not in result.stderr and str(value) not in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "improvement-runs").exists()


def test_future_environment_roots_do_not_require_initialization(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "not-created-home"))
    monkeypatch.setenv("EXO_PRINCIPAL_STORE", str(tmp_path / "not-created-store"))
    code, report, _ = invoke(tmp_path, monkeypatch, capsys)
    assert code == 0 and report["status"] == "review_ready"
    assert not (tmp_path / "not-created-home").exists()
    assert not (tmp_path / "not-created-store").exists()


@pytest.mark.parametrize("variable", ["EXO_HOME", "EXO_PRINCIPAL_STORE"])
@pytest.mark.parametrize("legacy", [False, True])
def test_missing_path_resolution_refuses_masked_cycle_before_fixture_read(tmp_path, monkeypatch, capsys, variable, legacy):
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    if legacy:
        monkeypatch.setattr(O, "_ALLOW_MISSING", None)
    value = tmp_path / "missing" / ".." / "loop"
    monkeypatch.setenv(variable, str(value))
    def forbidden(*args, **kwargs):
        raise AssertionError("masked cycle must be refused before fixture reads")
    monkeypatch.setattr(O, "load_fixture", forbidden)
    monkeypatch.chdir(tmp_path)
    code = cli.main(["improve", "--offline", "--adapter", "text-normalize-v1",
                     "--fixture", "public.json", "--output", "refused"])
    result = capsys.readouterr()
    assert code == 2 and not result.out and f"cannot resolve {variable}" in result.err
    assert str(loop) not in result.err and str(value) not in result.err
    assert "Traceback" not in result.err
    assert not (tmp_path / "improvement-runs").exists()


@pytest.mark.parametrize("legacy", [False, True])
def test_missing_path_resolution_preserves_absent_environment_roots(tmp_path, monkeypatch, capsys, legacy):
    if legacy:
        monkeypatch.setattr(O, "_ALLOW_MISSING", None)
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "missing" / ".." / "missing-home"))
    monkeypatch.setenv("EXO_PRINCIPAL_STORE", str(tmp_path / "missing" / ".." / "missing-store"))
    assert invoke(tmp_path, monkeypatch, capsys)[0] == 0
    assert not (tmp_path / "missing-home").exists()
    assert not (tmp_path / "missing-store").exists()
