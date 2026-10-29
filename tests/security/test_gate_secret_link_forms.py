"""#312: a link into a store still asks when it is reached through a folder the line enters, $PWD or a glob.

Fake files only, in a temporary HOME. Every check classifies; nothing is run."""

import time

import pytest

from exo import gate

POL = gate.DEFAULT_POLICIES


def outcome(tool, args, cwd=None):
    c = gate.classify({"tool": tool, "args": args}, cwd=cwd) if cwd else gate.classify({"tool": tool, "args": args})
    return c.category, gate.decide(c, POL).outcome


@pytest.fixture
def tree(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for d in (".ssh", ".kube"):
        (home / d).mkdir(parents=True)
    (home / ".ssh" / "id_rsa").write_text("fake\n")
    (home / ".kube" / "config").write_text("fake\n")
    (home / ".kube" / "other.txt").write_text("plain\n")
    work = tmp_path / "work"
    (work / "docs").mkdir(parents=True)
    (work / "plain.md").write_text("hi\n")
    (work / "docs" / "a.md").write_text("hi\n")
    (work / "notes.txt").symlink_to(home / ".ssh" / "id_rsa")
    (work / "kdir").symlink_to(home / ".kube")
    (work / "latest.md").symlink_to(work / "plain.md")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("EXO_HOME", raising=False)
    monkeypatch.delenv("EXO_KEYS_FILE", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.chdir(elsewhere)              # the gate's own folder is not the tool's
    return work


def test_a_link_reads_from_the_folder_the_line_enters(tree):
    """The issue's cd row: the session sits elsewhere, the line cds into the work folder first."""
    w = str(tree)
    assert outcome("bash", {"command": "cat notes.txt"}, cwd=w) == ("credentials", "ask")     # the control
    assert outcome("bash", {"command": f"cd {tree} && cat notes.txt"}, cwd=str(tree.parent / "elsewhere")) == \
        ("credentials", "ask")
    # A relative target, and a target reached from an earlier one on the same line.
    assert outcome("bash", {"command": "cd work && cat notes.txt"}, cwd=str(tree.parent)) == \
        ("credentials", "ask")
    assert outcome("bash", {"command": "cd work && cd . && cat notes.txt"}, cwd=str(tree.parent)) == \
        ("credentials", "ask")
    # The same names over plain files stay reads.
    assert outcome("bash", {"command": "cd work && cat plain.md"}, cwd=str(tree.parent)) == ("read", "allow")
    assert outcome("bash", {"command": "cd docs && cat a.md"}, cwd=w) == ("read", "allow")


@pytest.mark.parametrize("form", ["$PWD/notes.txt", "${PWD}/notes.txt", "$(pwd)/notes.txt", "`pwd`/notes.txt",
                                  "${PWD%/*}/work/notes.txt"])
def test_the_current_folder_spelled_out_is_the_tools_folder(tree, form):
    w = str(tree)
    assert outcome("bash", {"command": f"cat {form}"}, cwd=w) == ("credentials", "ask")
    assert outcome("read_file", {"path": form}, cwd=w) == ("credentials", "ask")
    plain = form.replace("notes.txt", "plain.md")
    assert outcome("bash", {"command": f"cat {plain}"}, cwd=w) == ("read", "allow")


@pytest.mark.parametrize("form", ["note?.txt", "note*.txt", "*.txt", "k*/config"])
def test_a_glob_is_judged_by_where_its_matches_lead(tree, form):
    assert outcome("bash", {"command": f"cat {form}"}, cwd=str(tree)) == ("credentials", "ask")


def test_a_glob_over_plain_files_stays_a_read(tree):
    w = str(tree)
    for form in ("*.md", "docs/*.md", "plain.*", "latest.*"):
        assert outcome("bash", {"command": f"cat {form}"}, cwd=w) == ("read", "allow")
    # A glob in a folder the line enters is expanded there too.
    assert outcome("bash", {"command": "cd work && cat note?.txt"}, cwd=str(tree.parent)) == \
        ("credentials", "ask")
    assert outcome("bash", {"command": "cd docs && cat *.md"}, cwd=w) == ("read", "allow")


def test_a_cd_into_a_link_to_a_store_enters_the_store(tree):
    w = str(tree)
    assert outcome("bash", {"command": "cd kdir && cat config"}, cwd=w) == ("credentials", "ask")
    # Parity with the lexical spelling: a plain name in the store folder is not a store file.
    assert outcome("bash", {"command": "cd kdir && cat other.txt"}, cwd=w) == ("read", "allow")
    assert outcome("bash", {"command": "cd ~/.kube && cat other.txt"}, cwd=w) == ("read", "allow")
    assert outcome("bash", {"command": "cd ~/.kube && cat config"}, cwd=w) == ("credentials", "ask")


def test_a_glob_cd_target_enters_the_folder_it_lands_in(tree):
    """A one-character respelling of a glob cd target used to escape: `cd kd*` read without asking."""
    w = str(tree)
    for cd in ("cd kd*", "cd k?ir", "pushd kd*", "env -C kd*"):
        cmd = f"{cd} cat config" if cd.startswith("env") else f"{cd} && cat config"
        assert outcome("bash", {"command": cmd}, cwd=w) == ("credentials", "ask")


def test_a_glob_in_a_folder_part_is_expanded_one_level(tree):
    w = str(tree)
    assert outcome("bash", {"command": "cat kd*/config"}, cwd=w) == ("credentials", "ask")
    assert outcome("bash", {"command": "cat k*/c*"}, cwd=w) == ("credentials", "ask")
    # A plain file reached the same way stays a read.
    assert outcome("bash", {"command": "cat k*/other.txt"}, cwd=w) == ("read", "allow")


def test_a_link_target_reached_from_an_earlier_target_or_spelled_out(tree):
    w = str(tree)
    assert outcome("bash", {"command": "cd work && cd kdir && cat config"}, cwd=str(tree.parent)) == \
        ("credentials", "ask")
    assert outcome("bash", {"command": "cd $PWD/kdir && cat config"}, cwd=w) == ("credentials", "ask")
    assert outcome("bash", {"command": "cd ~+/kdir && cat config"}, cwd=w) == ("credentials", "ask")


def test_a_quoted_or_escaped_glob_is_not_expanded(tree):
    """Bash does not expand a quoted glob, so it must not ask on the files it would have matched."""
    w = str(tree)
    (tree / "fixtures").mkdir()
    (tree / "fixtures" / "id_rsa").write_text("fake\n")
    assert outcome("bash", {"command": "echo '*'"}, cwd=w) == ("read", "allow")
    assert outcome("bash", {"command": "git commit -m 'update *'"}, cwd=w)[1] == "allow"
    assert outcome("bash", {"command": "grep 'no*' plain.md"}, cwd=w) == ("read", "allow")
    assert outcome("bash", {"command": r"echo \*"}, cwd=w) == ("read", "allow")
    assert outcome("bash", {"command": "echo 'fixtures/*'"}, cwd=w) == ("read", "allow")
    # The same glob unquoted does ask: the contrast pins the quoting, not the folder.
    assert outcome("bash", {"command": "echo fixtures/*"}, cwd=w) == ("credentials", "ask")


def test_a_glob_inside_a_substitution_is_expanded(tree):
    assert outcome("bash", {"command": "echo $(cat no*)"}, cwd=str(tree)) == ("credentials", "ask")


def test_a_link_past_the_old_match_cap_still_asks(tree):
    """The old code kept the first 256 matches per folder; a link past them read without asking."""
    big = tree / "big"
    big.mkdir()
    for i in range(300):
        (big / f"f{i:03d}.txt").write_text("plain\n")
    (big / "zzkey.txt").symlink_to(tree.parent / "home" / ".ssh" / "id_rsa")
    assert outcome("bash", {"command": "cat *"}, cwd=str(big)) == ("credentials", "ask")
    assert outcome("bash", {"command": "cat *.txt"}, cwd=str(big)) == ("credentials", "ask")


def test_many_filler_folders_do_not_push_the_store_out(tree):
    """The old code kept the first 64 entered folders; a store entered past them read without asking."""
    parent = tree.parent
    fillers = []
    for i in range(65):
        d = parent / f"f{i:02d}"
        d.mkdir()
        fillers.append(str(d))
    line = " && ".join(f"cd {f}" for f in fillers) + " && cd work && cat notes.txt"
    assert outcome("bash", {"command": line}, cwd=str(parent)) == ("credentials", "ask")
    plain = " && ".join(f"cd {f}" for f in fillers) + " && cd work && cat plain.md"
    assert outcome("bash", {"command": plain}, cwd=str(parent)) == ("read", "allow")


def test_link_target_cap_fails_closed(tree):
    """Past 128 link targets the line counts, rather than silently dropping the rest."""
    for i in range(129):
        real = tree / f"r{i:03d}"
        real.mkdir()
        (tree / f"l{i:03d}").symlink_to(real)
    line = " && ".join(f"cd l{i:03d}" for i in range(129)) + " && cat config"
    assert outcome("bash", {"command": line}, cwd=str(tree)) == ("credentials", "ask")


def test_glob_cost_stays_linear_over_many_folders(tree):
    """64 folders of 300 plain files, a cd chain and `cat *`: the old code took ~24s of CPU here."""
    parent = tree.parent
    for i in range(64):
        d = parent / f"d{i:02d}"
        d.mkdir()
        for j in range(300):
            (d / f"file{j:03d}.txt").write_text("plain\n")
    line = " && ".join(f"cd d{i:02d}" for i in range(64)) + " && cat *"
    start = time.monotonic()
    assert outcome("bash", {"command": line}, cwd=str(parent)) == ("read", "allow")
    assert time.monotonic() - start < 10
