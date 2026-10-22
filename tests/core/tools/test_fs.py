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


STALE_ONE_LINERS = [  # (file, old_string, new_string): the file's line changed since old_string was read
    ("A = 1\nTIMEOUT_SECONDS = 45\n", "TIMEOUT_SECONDS = 30", "TIMEOUT_SECONDS = 60"),
    ("def f(uid):\n    return db.get_user(uid)\n", "    return db.get_users(uid)", "    return db.get_users(uid, 1)"),
    ("x = compute(a, b, c)\n", "x = compute(a, b)", "x = compute(a, b, d)"),
    ("if user.is_admin:\n    go()\n", "if not user.is_admin:", "if not user.is_admin and ok:"),
    # One line padded with blank lines (a copied line's newline, a blank context line) is still one line.
    ("A = 1\n\nTIMEOUT_SECONDS = 45\n\nB = 2\n", "TIMEOUT_SECONDS = 30\n", "TIMEOUT_SECONDS = 60\n"),
    ("A = 1\n\nTIMEOUT_SECONDS = 45\n\nB = 2\n", "\nTIMEOUT_SECONDS = 30", "\nTIMEOUT_SECONDS = 60"),
    ("A = 1\n\nTIMEOUT_SECONDS = 45\n\nB = 2\n", "\nTIMEOUT_SECONDS = 30\n", "\nTIMEOUT_SECONDS = 60\n"),
]


def test_edit_refuses_stale_one_line_old_string(run, tmp_path):
    # A one-line old_string whose text differs from the file's line (not just its whitespace) used to match via
    # context_aware or block_anchor and overwrite the newer line. It fails now, and the hint shows the current line.
    f = tmp_path / "cfg.py"
    for content, old, new in STALE_ONE_LINERS:
        f.write_text(content)
        for replace_all in (False, True):
            res = run("edit_file", path="cfg.py", old_string=old, new_string=new, replace_all=replace_all)
            assert res.is_error and "Could not find a match" in res.text(), (old, res.text())
            assert "Did you mean" in res.text() and old.strip().split()[0] in res.text()
            assert f.read_text() == content
    f.write_text("A = 1\nTIMEOUT_SECONDS = 45\n")
    assert "TIMEOUT_SECONDS = 45" in run("edit_file", path="cfg.py", old_string="TIMEOUT_SECONDS = 30",
                                         new_string="TIMEOUT_SECONDS = 60").text()


def test_fuzzy_one_line_matches_only_up_to_drift():
    for content, old, new in STALE_ONE_LINERS:
        assert fuzzy_find_and_replace(content, old, new) == (
            content, 0, None, "Could not find a match for old_string in the file"), old
    # Whitespace, indentation and Unicode drift on one line still land.
    content = "def f():\n    TIMEOUT_SECONDS  =  45\n    say(\u201chi\u201d)\n"
    new, n, strategy, err = fuzzy_find_and_replace(content, "TIMEOUT_SECONDS = 45\n", "TIMEOUT_SECONDS = 60\n")
    assert err is None and n == 1 and strategy == "whitespace_normalized"
    assert new == "def f():\n    TIMEOUT_SECONDS = 60\n    say(\u201chi\u201d)\n"
    new, n, strategy, err = fuzzy_find_and_replace(content, 'say("hi")', 'say("bye")')
    assert err is None and strategy == "unicode_normalized" and new.endswith("say(\u201cbye\u201d)\n")


def test_fuzzy_multi_line_similarity_still_matches_one_place():
    # Approximate matching stays for blocks of two or more non-blank lines (the upstream design).
    content = "def compute_total(a):\n\n    return a + 1\n"
    new, n, strategy, err = fuzzy_find_and_replace(
        content, "def compute_totals(a):\n\n    return a + 1", "def compute_total(a):\n\n    return a + 2")
    assert err is None and n == 1 and strategy == "context_aware"
    assert new == "def compute_total(a):\n\n    return a + 2\n"
    new, n, strategy, err = fuzzy_find_and_replace("start\nalpha one\nend\n", "start\nalpha 1\nend", "X")
    assert err is None and n == 1 and strategy == "block_anchor" and new == "X\n"
    # ...but blank lines are no anchor for a single changed line.
    content = "a = 1\n\nTIMEOUT = 45\n\nb = 2\n"
    assert fuzzy_find_and_replace(content, "\nTIMEOUT = 30\n", "\nTIMEOUT = 60\n")[1] == 0


def test_edit_one_trivial_extra_line_still_matches_a_stale_line(run, tmp_path):
    # docs/TOOLS.md "Edit matching": the rule counts lines, not exact lines. One stale line plus a lone brace or the
    # def line matches by similarity and replaces the newer line, and the result names the strategy.
    f = tmp_path / "cfg.py"
    for old, strategy in [("    TIMEOUT = 30\n}", "context_aware"), ("def f():\n    TIMEOUT = 30", "context_aware"),
                          ("def f():\n    TIMEOUT = 30\n}", "block_anchor")]:
        f.write_text("def f():\n    TIMEOUT = 45\n}\n")
        res = run("edit_file", path="cfg.py", old_string=old, new_string=old.replace("30", "60"))
        assert not res.is_error and res.text().endswith(f"(matched via {strategy})"), (old, res.text())
        assert f.read_text() == "def f():\n    TIMEOUT = 60\n}\n"
    f.write_text("def f():\n    TIMEOUT = 45\n}\n")
    assert run("edit_file", path="cfg.py", old_string="    TIMEOUT = 30", new_string="    TIMEOUT = 60").is_error


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


def test_fuzzy_whitespace_drift_keeps_indentation():
    # One stray double space in the file; the model's old_string has it collapsed, so only the
    # whitespace-normalized strategy matches. The match starts after the indentation, and the
    # replacement must keep the file's depth on every line.
    content = "def f(a):\n    if a:\n        return  1\n        y = 3\n    x = 2\n"
    new, n, strategy, err = fuzzy_find_and_replace(content, "if a:\n    return 1", "if a:\n    return 2")
    assert err is None and n == 1 and strategy == "whitespace_normalized"
    assert new == "def f(a):\n    if a:\n        return 2\n        y = 3\n    x = 2\n"
    # Mid-line matches are not widened to the line start.
    new, n, strategy, err = fuzzy_find_and_replace("x = foo(a,  b)\n", "foo(a, b)", "foo(a, c)")
    assert err is None and strategy == "whitespace_normalized" and new == "x = foo(a, c)\n"


def test_fuzzy_whitespace_spans_cover_the_pattern():
    # Every span the whitespace-normalized strategy returns must normalize to the pattern; before,
    # a span could start inside the blank run before the match.
    import random
    import re
    from exo.core.tools.builtin.fuzzy import _strategy_whitespace_normalized

    norm = lambda t: re.sub(r"[ \t]+", " ", t)
    rng = random.Random(7)
    words = ["foo", "bar", "x", "=", "(", ")", "if", "y"]
    checked = 0
    for _ in range(3000):
        lines = []
        for _ in range(rng.randint(2, 5)):
            toks = [rng.choice(words) for _ in range(rng.randint(1, 4))]
            lines.append(rng.choice(["", " ", "\t", "  "]) + rng.choice([" ", "  ", "\t"]).join(toks)
                         + rng.choice(["", " ", "\t"]))
        content = "\n".join(lines) + "\n"
        s = rng.randint(0, len(content) - 2)
        old = content[s:rng.randint(s + 1, len(content))]
        if not old.strip():
            continue
        drifted = re.sub(r"[ \t]+", lambda m: rng.choice([" ", "  ", "\t"]), old)
        for a, b in _strategy_whitespace_normalized(content, drifted):
            assert norm(content[a:b]) == norm(drifted), (content, drifted, (a, b))
            checked += 1
    assert checked > 500


def test_fuzzy_verbatim_form_keeps_absolute_indent():
    # old_string copied from read_file starting at the first token: its continuation lines already carry
    # the file's indent, so the replacement goes in as written (tabs, spaces, and a one-line old).
    content = "func f() {\n\tif a {\n\t\treturn  1\n\t}\n}"
    new, n, strategy, err = fuzzy_find_and_replace(content, "if a {\n\t\treturn 1\n\t}", "if a {\n\t\treturn 2\n\t}")
    assert err is None and strategy == "whitespace_normalized" and new == content.replace("return  1", "return 2")
    content = "def f():\n    if a:\n        return  1\n"
    new, _n, _s, err = fuzzy_find_and_replace(content, "if a:\n        return 1", "if a:\n        return 2")
    assert err is None and new == "def f():\n    if a:\n        return 2\n"
    new, _n, _s, err = fuzzy_find_and_replace("\tx  := g()", "x := g()", "x := g()\n\tlog(x)")
    assert err is None and new == "\tx := g()\n\tlog(x)"


def test_fuzzy_dedented_spaces_in_a_tab_indented_file():
    # A block dedented to column 0 with space indentation, matched in a tab-indented file: a tab counts to the
    # next multiple of 8 columns, so its lines sit shallower than the file's and get the file's indent back.
    content = "def f():\n\tif  x:\n\t\ty()\n\treturn 1\n"
    new, _n, strategy, err = fuzzy_find_and_replace(content, "if x:\n    y()", "if x:\n    y2()\n    z()")
    assert err is None and strategy == "whitespace_normalized"
    assert new == "def f():\n\tif x:\n\t    y2()\n\t    z()\n\treturn 1\n"
    compile(new, "<edit>", "exec")
    # The same block dedented with a tab is re-anchored as before.
    new, _n, _s, err = fuzzy_find_and_replace(content, "if x:\n\ty()", "if x:\n\ty2()")
    assert err is None and new == "def f():\n\tif x:\n\t\ty2()\n\treturn 1\n"


def test_fuzzy_indented_unicode_and_escape_matches_stay_in_place():
    # Unicode- and escape-normalized matches after a line's indent keep the file's typography and are never
    # re-anchored: their continuation lines land exactly where new_string puts them.
    new, _n, strategy, err = fuzzy_find_and_replace("    \u201chello\u201d\n", '"hello"', '"hello world"')
    assert err is None and strategy == "unicode_normalized" and new == "    \u201chello world\u201d\n"
    new, _n, _s, err = fuzzy_find_and_replace("    print(\"It\u2019s fine\")", "print(\"It's fine\")", "print(\"It's great\")")
    assert err is None and new == "    print(\"It\u2019s great\")"
    content = "  - It\u2019s \u201cdone\u201d \u2014 ship\u00a0it\n"
    new, _n, _s, err = fuzzy_find_and_replace(content, '- It\'s "done" -- ship it', '- It\'s "done" -- ship it now')
    assert err is None and new == "  - It\u2019s \u201cdone\u201d \u2014 ship\u00a0it now\n"
    content = "def h():\n    msg = \u201chi\u201d\n    return msg"
    new, _n, strategy, err = fuzzy_find_and_replace(content, 'msg = "hi"\n    return msg', 'msg = "bye"\n    return msg')
    assert err is None and strategy == "unicode_normalized" and new == content.replace("hi", "bye")
    for indent in ("    ", "\t"):
        content = f"def g():\n{indent}if x:\n{indent}{indent}return 1\n"
        old, new_string = f"if x:\\n{indent}{indent}return 1", f"if x:\n{indent}{indent}return 2"
        new, _n, strategy, err = fuzzy_find_and_replace(content, old, new_string)
        assert err is None and strategy == "escape_normalized" and new == content.replace("return 1", "return 2")


def test_fuzzy_empty_new_string_keeps_line_indent():
    # Deleting the head of an indented line leaves the indent; deleting a whole dedented block takes it along.
    assert fuzzy_find_and_replace("\ta  := 1; b := 2", "a := 1; ", "")[0] == "\tb := 2"
    assert fuzzy_find_and_replace("    a  = 1; b = 2", "a = 1; ", "")[0] == "    b = 2"
    assert fuzzy_find_and_replace("    s = \u201cx\u201d; t = 2", 's = "x"; ', "")[0] == "    t = 2"
    assert fuzzy_find_and_replace("    foo  x", "foo x", "")[0] == "    "
    content = "    if a:\n        return  1\n    x = 2\n"
    assert fuzzy_find_and_replace(content, "if a:\n    return 1", "")[0] == "\n    x = 2\n"


def test_fuzzy_replace_all_on_adjacent_indented_lines():
    # A match that swallows the next line's indent must not be widened back into the previous match.
    content = "    print(\u201chi\u201d)\n    print(\u201chi\u201d)\n    done()\n"
    new, n, _s, err = fuzzy_find_and_replace(content, 'print("hi")\n    ', "", replace_all=True)
    assert err is None and n == 2 and new == "    done()\n"
    new, n, _s, err = fuzzy_find_and_replace("    a = 1\n    a = 1\n    b = 2\n", "a = 1\\n    ", "", replace_all=True)
    assert err is None and n == 2 and new == "    b = 2\n"
    new, n, _s, err = fuzzy_find_and_replace("x  =\n    x  =\n    y = 1\n    z = 2\n", "x =\n ", "", replace_all=True)
    assert err is None and n == 2 and new == "y = 1\n    z = 2\n"
    content = "def f():\n    if a:\n        return  1\n    if a:\n        return  1\n"
    new, n, _s, err = fuzzy_find_and_replace(content, "if a:\n    return 1", "if a:\n    return 2", replace_all=True)
    assert err is None and n == 2 and new == content.replace("return  1", "return 2")
