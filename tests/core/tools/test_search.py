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


# --- #255 W1-4: streamed grep lines equal decode + splitlines on the whole file ---

import random  # noqa: E402

import pytest  # noqa: E402

from exo.core.tools.builtin import search  # noqa: E402

_SEAM_BYTES = [b"a", b"TODO", b" ", b"\n", b"\r", b"\r\n", b"\v", b"\f", b"\x1c", b"\x1e",
               "\u2028".encode(), "\u2029".encode(), "\x85".encode(), "\u00e9".encode(),
               b"\xff", b"\xc3", b"\xe2\x80", b""]


@pytest.mark.parametrize("raw", [
    b"", b"\x1dTODO\x1d", b"\xef\xbb\xbfTODO\n", b"\xc0\xaf TODO\n", b"\xed\xa0\x80 TODO\n",
    b"a" * 65_535 + "é".encode() + b"\r\nTODO\n",  # a multibyte char and CRLF across the 64 KiB seam
    b"a" * 65_536 + b"\rTODO",  # a lone CR as the first byte past the seam
])
def test_streamed_lines_explicit_cases(tmp_path, raw):
    p = tmp_path / "case.txt"
    p.write_bytes(raw)
    assert list(search._lines(p)) == raw.decode("utf-8", errors="replace").splitlines()


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 65_536])
def test_streamed_lines_equal_splitlines(tmp_path, monkeypatch, chunk):
    rng = random.Random(20261020)
    monkeypatch.setattr(search, "_CHUNK", chunk)
    for case in range(60):
        raw = b"".join(rng.choice(_SEAM_BYTES) for _ in range(rng.randrange(120)))
        p = tmp_path / f"f{case}.txt"
        p.write_bytes(raw)
        try:
            got = list(search._lines(p))
        except Exception:
            got = None  # a NUL-free alphabet never refuses; any raise here is a bug the assert catches
        assert got == raw.decode("utf-8", errors="replace").splitlines(), (case, chunk, raw[:80])


def test_grep_skips_binary_and_oversize_files_wholly(run, tmp_path):
    (tmp_path / "bin.txt").write_bytes(b"TODO here\x00 and there\nTODO again\n")
    (tmp_path / "big.txt").write_bytes(b"TODO first line\n" + b"x" * search.MAX_FILE_BYTES)
    (tmp_path / "ok.txt").write_text("TODO fine\n")
    assert run("grep", pattern="TODO").text() == "ok.txt:1:TODO fine"


def test_grep_line_numbers_across_a_chunk_seam(run, tmp_path, monkeypatch):
    monkeypatch.setattr(search, "_CHUNK", 4)
    (tmp_path / "a.txt").write_bytes(b"one\r\ntwo\rthree\nTODO four")
    assert run("grep", pattern="TODO|two").text() == "a.txt:2:two\na.txt:4:TODO four"
