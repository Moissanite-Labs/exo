from exo.core.tools.builtin.fuzzy import fuzzy_find_and_replace


def test_read_numbered_paging_and_errors(run, tmp_path):
    (tmp_path / "a.txt").write_text("".join(f"line{i}\n" for i in range(1, 11)))
    res = run("read_file", path="a.txt", offset=3, limit=2)
    assert res.text().splitlines()[:2] == ["     3|line3", "     4|line4"]
    assert "offset=5" in res.text()
    assert run("read_file", path="missing.txt").is_error
    (tmp_path / "b.bin").write_bytes(b"\x00\x01\x02")
    assert "binary" in run("read_file", path="b.bin").text()


def test_write_creates_parents_and_overwrites(run, tmp_path):
    assert run("write_file", path="d/e/f.txt", content="hi").text().startswith("Created")
    assert run("write_file", path="d/e/f.txt", content="yo").text().startswith("Overwrote")
    assert (tmp_path / "d/e/f.txt").read_text() == "yo"
    assert not list((tmp_path / "d/e").glob(".*tmp"))


def test_edit_exact_and_ambiguous(run, tmp_path):
    f = tmp_path / "x.py"
    f.write_text("a = 1\nb = 1\n")
    res = run("edit_file", path="x.py", old_string="= 1", new_string="= 2")
    assert res.is_error and "Found 2 matches" in res.text() and "L1" in res.text()
    assert f.read_text() == "a = 1\nb = 1\n"
    assert not run("edit_file", path="x.py", old_string="= 1", new_string="= 2", replace_all=True).is_error
    assert f.read_text() == "a = 2\nb = 2\n"


def test_edit_no_match_gives_hint(run, tmp_path):
    (tmp_path / "x.py").write_text("def hello():\n    return 1\n")
    res = run("edit_file", path="x.py", old_string="def helo_world():", new_string="x")
    assert res.is_error and "Did you mean" in res.text() and "def hello" in res.text()


def test_edit_preserves_crlf(run, tmp_path):
    f = tmp_path / "w.txt"
    f.write_bytes(b"one\r\ntwo\r\n")
    assert not run("edit_file", path="w.txt", old_string="two", new_string="three").is_error
    assert f.read_bytes() == b"one\r\nthree\r\n"


def test_fuzzy_indentation_drift_reindents():
    content = "class A:\n    def f(self):\n        return 1\n"
    new, n, strategy, err = fuzzy_find_and_replace(
        content, "def f(self):\n    return 1", "def f(self):\n    return 2")
    assert err is None and n == 1 and strategy != "exact"
    assert new == "class A:\n    def f(self):\n        return 2\n"


def test_fuzzy_unicode_keeps_file_typography():
    content = "say \u201chello\u201d \u2014 then go\n"
    new, n, strategy, err = fuzzy_find_and_replace(content, 'say "hello" -- then go', 'say "hello" -- then stop')
    assert err is None and strategy == "unicode_normalized"
    assert new == "say \u201chello\u201d \u2014 then stop\n"


def test_fuzzy_rejects_empty_identical_and_escape_drift():
    assert "empty" in fuzzy_find_and_replace("x", "", "y")[3]
    assert "whitespace" in fuzzy_find_and_replace("x  y", "  ", "y")[3]
    assert "identical" in fuzzy_find_and_replace("abc", "abc", "abc")[3]
    _new, n, _s, err = fuzzy_find_and_replace("a\nb\n", "a\\nb", "a\\nc")
    assert n == 0 and "Escape-drift" in err


def test_fuzzy_replace_all_refuses_similarity_matches():
    content = "start\nalpha one\nend\nstart\nalpha two\nend\n"
    _new, n, _s, err = fuzzy_find_and_replace(content, "start\nalpha 1\nend", "X", replace_all=True)
    assert n == 0 and "approximate" in err
