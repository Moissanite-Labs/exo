def _tree(tmp_path):
    (tmp_path / "src/pkg").mkdir(parents=True)
    (tmp_path / "src/pkg/a.py").write_text("import os\nTODO = 1\n")
    (tmp_path / "src/b.py").write_text("print('todo later')\n")
    (tmp_path / "README.md").write_text("TODO: docs\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/x.py").write_text("TODO\n")
    (tmp_path / "blob.bin").write_bytes(b"TODO\x00\x00")


def test_glob(run, tmp_path):
    _tree(tmp_path)
    assert sorted(run("glob", pattern="**/*.py").text().splitlines()) == ["src/b.py", "src/pkg/a.py"]
    assert run("glob", pattern="*.py", path="src/pkg").text() == "a.py"
    assert run("glob", pattern="src/*.py").text() == "src/b.py"
    assert run("glob", pattern="*.rs").text() == "No files matched."
    assert run("glob", pattern="*", path="missing").is_error


def test_grep(run, tmp_path):
    _tree(tmp_path)
    rows = run("grep", pattern="TODO").text().splitlines()
    assert sorted(rows) == ["README.md:1:TODO: docs", "src/pkg/a.py:2:TODO = 1"]
    rows = run("grep", pattern="todo", ignore_case=True, glob="*.py").text().splitlines()
    assert sorted(rows) == ["src/b.py:1:print('todo later')", "src/pkg/a.py:2:TODO = 1"]
    assert run("grep", pattern="TODO", files_only=True, path="src").text() == "pkg/a.py"
    assert "more matches" in run("grep", pattern="o", limit=1).text()
    assert run("grep", pattern="(").is_error
