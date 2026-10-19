from exo.core.tools.builtin.patch import OperationType, parse_v4a_patch


def P(*body):
    return "*** Begin Patch\n" + "\n".join(body) + "\n*** End Patch\n"


def test_parse_all_ops_and_errors():
    ops, err = parse_v4a_patch(P("*** Add File: n.txt", "+hi", "*** Delete File: d.txt",
                                 "*** Move File: a.txt -> b.txt", "*** Update File: u.txt",
                                 "@@ def f @@", " ctx", "-old", "+new"))
    assert err is None
    assert [o.operation for o in ops] == [OperationType.ADD, OperationType.DELETE,
                                          OperationType.MOVE, OperationType.UPDATE]
    assert ops[2].new_path == "b.txt" and ops[3].hunks[0].context_hint == "def f"
    assert parse_v4a_patch(P("*** Update File: u.txt"))[1].startswith("Parse error")


def test_parse_ignores_marker_inside_content_and_crlf():
    ops, err = parse_v4a_patch(P("*** Add File: n.txt", "+*** End Patch", "+x").replace("\n", "\r\n"))
    assert err is None
    assert [ln.content for ln in ops[0].hunks[0].lines] == ["*** End Patch", "x"]


def test_update_add_delete_move(run, tmp_path):
    (tmp_path / "u.py").write_text("def f():\n    return 1\n\ndef g():\n    return 1\n")
    (tmp_path / "d.txt").write_text("bye\n")
    (tmp_path / "m.txt").write_text("move me\n")
    res = run("apply_patch", patch=P(
        "*** Update File: u.py", "@@ def g @@", " def g():", "-    return 1", "+    return 2",
        "*** Add File: sub/new.txt", "+hello", "+world",
        "*** Delete File: d.txt",
        "*** Move File: m.txt -> moved/m.txt"))
    assert not res.is_error, res.text()
    assert (tmp_path / "u.py").read_text() == "def f():\n    return 1\n\ndef g():\n    return 2\n"
    assert (tmp_path / "sub/new.txt").read_text() == "hello\nworld"
    assert not (tmp_path / "d.txt").exists() and not (tmp_path / "m.txt").exists()
    assert (tmp_path / "moved/m.txt").read_text() == "move me\n"
    assert "+    return 2" in res.text()


def test_failed_hunk_changes_nothing(run, tmp_path):
    (tmp_path / "a.txt").write_text("alpha\n")
    (tmp_path / "b.txt").write_text("beta\n")
    res = run("apply_patch", patch=P(
        "*** Update File: a.txt", "-alpha", "+ALPHA",
        "*** Update File: b.txt", "-gamma", "+GAMMA"))
    assert res.is_error and "no files changed" in res.text() and "b.txt" in res.text()
    assert (tmp_path / "a.txt").read_text() == "alpha\n"


def test_add_refuses_existing_and_move_refuses_overwrite(run, tmp_path):
    (tmp_path / "a.txt").write_text("keep\n")
    (tmp_path / "b.txt").write_text("also\n")
    assert "already exists" in run("apply_patch", patch=P("*** Add File: a.txt", "+x")).text()
    assert "already exists" in run("apply_patch", patch=P("*** Move File: a.txt -> b.txt")).text()
    assert (tmp_path / "a.txt").read_text() == "keep\n"


def test_delete_then_add_same_path_and_update_after_move(run, tmp_path):
    (tmp_path / "a.txt").write_text("old\n")
    res = run("apply_patch", patch=P(
        "*** Delete File: a.txt", "*** Add File: a.txt", "+fresh",
        "*** Move File: a.txt -> c.txt", "*** Update File: c.txt", "-fresh", "+final"))
    assert not res.is_error, res.text()
    assert (tmp_path / "c.txt").read_text() == "final" and not (tmp_path / "a.txt").exists()


def test_addition_only_hunk_uses_hint_and_already_applied_is_noop(run, tmp_path):
    f = tmp_path / "a.py"
    f.write_text("import os\n\ndef main():\n    pass\n")
    assert not run("apply_patch", patch=P("*** Update File: a.py", "@@ import os @@", "+import sys")).is_error
    assert f.read_text() == "import os\nimport sys\n\ndef main():\n    pass\n"
    again = P("*** Update File: a.py", "-    pass", "+    return run_everything()")
    assert not run("apply_patch", patch=again).is_error
    assert not run("apply_patch", patch=again).is_error  # re-sent: already applied
    assert f.read_text().count("run_everything") == 1


def test_context_only_patch_rejected(run, tmp_path):
    (tmp_path / "a.txt").write_text("x\n")
    res = run("apply_patch", patch=P("*** Update File: a.txt", " x"))
    assert res.is_error and "no changes" in res.text()


def test_stale_one_line_hunk_changes_nothing(run, tmp_path):
    # The file's line changed since the hunk was written: '-MAX_RETRIES = 3' must not rewrite 'MAX_RETRIES = 7',
    # with or without a blank context line, a context hint, or a second file in the patch.
    f, other = tmp_path / "r.py", tmp_path / "o.txt"
    other.write_text("keep\n")
    for body in (["-MAX_RETRIES = 3", "+MAX_RETRIES = 5"],
                 [" ", "-MAX_RETRIES = 3", "+MAX_RETRIES = 5"],
                 ["@@ import os @@", "-MAX_RETRIES = 3", "+MAX_RETRIES = 5"]):
        f.write_text("import os\n\nMAX_RETRIES = 7\n")
        res = run("apply_patch", patch=P("*** Update File: o.txt", "-keep", "+changed", "*** Update File: r.py", *body))
        assert res.is_error and "not found" in res.text() and "no files changed" in res.text(), res.text()
        assert "MAX_RETRIES = 7" in res.text()
        assert f.read_text() == "import os\n\nMAX_RETRIES = 7\n" and other.read_text() == "keep\n"
