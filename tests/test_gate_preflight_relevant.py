"""scripts/gate_preflight_relevant.py: path filter for the never-looser workflow."""

import ast
import importlib.machinery
import subprocess
import sys
from pathlib import Path

from scripts import gate_preflight_relevant
from scripts.gate_preflight_relevant import (
    GATE_RELEVANT_PATTERNS,
    changed_paths_from_name_status,
    diff_touches_gate_paths,
    git_changed_paths,
    is_gate_relevant,
    path_matches_pattern,
)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def test_patterns_cover_workflow_list():
    assert ".github/workflows/gate-preflight.yml" in GATE_RELEVANT_PATTERNS
    assert "scripts/gate_preflight_relevant.py" in GATE_RELEVANT_PATTERNS
    assert "tests/test_gate_preflight_relevant.py" in GATE_RELEVANT_PATTERNS
    assert "pyproject.toml" in GATE_RELEVANT_PATTERNS
    assert "uv.lock" in GATE_RELEVANT_PATTERNS


def test_gate_py_and_plugin_paths_match():
    assert path_matches_pattern("src/exo/gate.py", "src/exo/gate.py")
    assert path_matches_pattern("plugins/exo-gate/foo.py", "plugins/exo-gate/**")
    assert not path_matches_pattern("plugins/exo-other/x.py", "plugins/exo-gate/**")


def test_test_globs_match():
    assert path_matches_pattern("tests/security/test_gate_foo.py", "tests/security/test_gate*.py")
    assert path_matches_pattern("tests/test_gate_diff_extra.py", "tests/test_gate_diff*.py")
    assert not path_matches_pattern("tests/test_other.py", "tests/test_gate_diff*.py")


def test_unrelated_diff_is_not_gate_relevant():
    fixture = ["README.md", "src/exo/other.py", "plugins/exo-command/x.py"]
    assert not diff_touches_gate_paths(fixture)


def test_dependency_and_workflow_edits_are_gate_relevant():
    assert diff_touches_gate_paths(["pyproject.toml"])
    assert diff_touches_gate_paths(["uv.lock"])
    assert diff_touches_gate_paths([".github/workflows/gate-preflight.yml"])


def test_mixed_diff_detects_any_gate_path():
    fixture = ["docs/CHANGELOG.md", "scripts/gate_diff.py"]
    assert diff_touches_gate_paths(fixture)


def test_pr_weakening_detector_still_gate_relevant():
    """Workflow runs the base copy of the detector; a head edit cannot skip via this file."""
    assert diff_touches_gate_paths(["scripts/gate_preflight_relevant.py"])


def test_base_detector_run_full_when_only_detector_path_changes(tmp_path, monkeypatch):
    """Even a PR whose only change is weakening the detector still gets run_full=true from base."""
    out = tmp_path / "github_output.txt"

    def fake_git_changed_paths(base_sha: str):
        assert base_sha == "deadbeef"
        return ["scripts/gate_preflight_relevant.py"], None

    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setattr(gate_preflight_relevant, "git_changed_paths", fake_git_changed_paths)
    assert gate_preflight_relevant.main(["--base", "deadbeef"]) == 0
    assert out.read_text(encoding="utf-8").strip() == "run_full=true"


def test_rename_away_from_watched_path_is_gate_relevant():
    status = "R100\0src/exo/gate.py\0src/exo/gate2.py\0"
    paths = changed_paths_from_name_status(status)
    assert "src/exo/gate.py" in paths
    assert diff_touches_gate_paths(paths)


def test_rename_into_watched_path_is_gate_relevant():
    status = "R100\0src/exo/other.py\0src/exo/gate.py\0"
    paths = changed_paths_from_name_status(status)
    assert diff_touches_gate_paths(paths)


def test_delete_of_watched_path_is_gate_relevant():
    status = "D\0src/exo/gate.py\0"
    paths = changed_paths_from_name_status(status)
    assert diff_touches_gate_paths(paths)


def test_git_diff_rename_away_lists_old_path(tmp_path, monkeypatch):
    """End-to-end: git diff --name-only hides renames; our parser must not."""
    monkeypatch.chdir(tmp_path)
    for cmd in (
        ["git", "init"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    gate = tmp_path / "src" / "exo" / "gate.py"
    gate.parent.mkdir(parents=True)
    gate.write_text("# gate\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "base"], check=True, capture_output=True, text=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    gate.rename(tmp_path / "src" / "exo" / "gate2.py")
    subprocess.run(["git", "add", "-A"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "rename"], check=True, capture_output=True, text=True)
    paths, err = git_changed_paths(base_sha)
    assert err is None
    assert "src/exo/gate.py" in paths
    assert diff_touches_gate_paths(paths)


def test_files_that_stand_in_for_or_run_with_the_gate_are_gate_relevant():
    """What the comparator's child can run when it imports a tree's gate (#1151 review, F1)."""
    for path in (
        # gate.py and what Python imports in its place: a package, an extension module, a cached .pyc
        "src/exo/gate/__init__.py",
        "src/exo/gate/extra.py",
        "src/exo/gate.cpython-311-x86_64-linux-gnu.so",
        "src/exo/gate.abi3.so",
        "src/exo/__pycache__/gate.cpython-311.pyc",
        # the package's own init, which runs first, and its stand-ins
        "src/exo/__init__.py",
        "src/exo/__init__.abi3.so",
        "src/exo/__init__.cpython-311-x86_64-linux-gnu.so",
        "src/exo/__pycache__/__init__.cpython-311.pyc",
        # store.py, which gate.py imports, and its stand-ins
        "src/exo/store.py",
        "src/exo/store/__init__.py",
        "src/exo/store.abi3.so",
        "src/exo/__pycache__/store.cpython-313.pyc",
        # a .pyc anywhere in the package, for a module the gate comes to import later
        "src/exo/core/__pycache__/anything.cpython-311.pyc",
        # the data file gate.py reads
        "src/exo/core/models/catalog.toml",
        # what changes those files' bytes on checkout (working-tree-encoding, eol) with no change to them
        ".gitattributes",
        "src/exo/core/models/.gitattributes",
        # src/ outside the package: first on the child's sys.path, ahead of the standard library
        "src/tomllib.py",
        "src/copy/__init__.py",
        "src/__pycache__/hashlib.cpython-311.pyc",
        # the editable install's roots: a sitecustomize.py there runs at start-up in both children
        "src/sitecustomize.py",
        "clients/tui/sitecustomize.py",
        "clients/tui/pyproject.toml",
    ):
        assert diff_touches_gate_paths([path]), path


def test_editable_roots_are_the_parents_of_the_wheel_packages():
    """The editable install puts the parent of each wheel package on sys.path (hatchling's .pth). A new package
    brings a new root, and EDITABLE_ROOTS must name it."""
    import tomllib

    packages = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["hatch"]["build"]["targets"][
        "wheel"
    ]["packages"]
    want = {(f"{p.rpartition('/')[0]}/", f"{p}/") for p in packages}
    assert set(gate_preflight_relevant.EDITABLE_ROOTS) == want


def test_the_rest_of_the_package_is_not_gate_relevant():
    """The list stays narrow: most PRs touch src/exo/ and should not pay for the comparator."""
    for path in (
        "src/exo/other.py",
        "src/exo/storage.py",
        "src/exo/cli/gate_cmd.py",
        "src/exo/core/seat_binding_conformance.py",
        "src/exo/core/models/other.toml",
        "srcfoo/tomllib.py",
        "clients/tui/exo_tui/app.py",
        "clients/web/sitecustomize.py",
    ):
        assert not is_gate_relevant(path), path


def _module_file(name: str) -> Path:
    """The file under src/ that `import <name>` loads: a package's __init__.py or a module's .py."""
    base = SRC.joinpath(*name.split("."))
    return base / "__init__.py" if base.is_dir() else base.with_suffix(".py")


def _exo_imports(path: Path, package: str) -> set[str]:
    """Every exo module a source file imports anywhere in its body, function-level imports included."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
                target = f"{base}.{node.module}" if node.module else base
            else:
                target = node.module or ""
            if target == "exo" or target.startswith("exo."):
                found.add(target)
                found.update(f"{target}.{alias.name}" for alias in node.names if _module_file(f"{target}.{alias.name}").exists())
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if alias.name == "exo" or alias.name.startswith("exo."))
    return found


def _top_level_imports(path: Path) -> set[str]:
    """The first part of every absolute import outside exo: a module of that name in src/ would be imported first."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            names.add(node.module.split(".")[0])
    return names - {"exo"}


# The forms this test knows a module can use to find, read or run another file. The walk below follows only imports
# and `Path(__file__).parent / "a" / "b"`; any other use of these fails the test until it is followed or listed below.
# It is a tripwire for honest changes, not a proof: a form missing here passes.
LOADING_NAMES = frozenset({"__file__", "__spec__", "__path__", "__loader__", "__import__", "exec", "eval", "compile"})
LOADING_ATTRIBUTES = frozenset({"__file__", "__spec__", "__path__", "__loader__", "co_filename"})
LOADING_MODULES = frozenset({"importlib", "pkgutil", "runpy", "zipimport", "builtins", "inspect"})

# Uses of those in a file the gate loads that the walk does not follow, by file and function, each with the reason
# the gate never runs it. store.template_dir finds the template a new principal store is copied from, and the gate
# imports only explicit_roots from store.
NOT_RUN_BY_THE_GATE = {"src/exo/store.py": {"template_dir"}}


def _file_uses(path: Path) -> tuple[set[Path], set[str]]:
    """The files a module reads as `Path(__file__).parent / "a" / "b"`, and the functions (`<module>` for the module
    body) that use a name, attribute or module in LOADING_* any other way, which this walk cannot follow."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[Path] = set()
    followed: set[int] = set()
    divs = [n for n in ast.walk(tree) if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)]
    inner = {id(n.left) for n in divs}  # `p / "a"` inside `p / "a" / "b"`: only the whole path is read
    for node in divs:
        if id(node) in inner:
            continue
        parts: list[str] = []
        left: ast.expr = node
        while isinstance(left, ast.BinOp) and isinstance(left.op, ast.Div):
            if not (isinstance(left.right, ast.Constant) and isinstance(left.right.value, str)):
                break
            parts.insert(0, left.right.value)
            left = left.left
        else:
            if (
                isinstance(left, ast.Attribute)
                and left.attr == "parent"
                and isinstance(left.value, ast.Call)
                and getattr(left.value.func, "id", "") == "Path"
                and [getattr(a, "id", "") for a in left.value.args] == ["__file__"]
            ):
                found.add(path.parent.joinpath(*parts))
                followed.add(id(left.value.args[0]))
    other: set[str] = set()

    def visit(node: ast.AST, where: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Name) and child.id in LOADING_NAMES and id(child) not in followed:
                other.add(where)
            elif isinstance(child, ast.Attribute) and child.attr in LOADING_ATTRIBUTES:
                other.add(where)
            elif isinstance(child, ast.Constant) and child.value in LOADING_ATTRIBUTES:
                other.add(where)  # globals()["__file__"], getattr(m, "__spec__")
            elif isinstance(child, ast.Import) and any(a.name.split(".")[0] in LOADING_MODULES for a in child.names):
                other.add(where)
            elif isinstance(child, ast.ImportFrom) and (child.module or "").split(".")[0] in LOADING_MODULES:
                other.add(where)
            named = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            visit(child, child.name if named else where)

    visit(tree, "<module>")
    return found, other


def _stand_ins(path: Path) -> list[Path]:
    """The files Python imports in place of a module's source. For d/m.py: a package d/m/, a file d/m.<suffix> (an
    extension module comes before the .py), and d/__pycache__/m.<tag>.pyc when that .pyc says it is current."""
    suffixes = importlib.machinery.all_suffixes()
    out = [path.parent / "__pycache__" / f"{path.stem}.{sys.implementation.cache_tag}.pyc"]
    if path.name == "__init__.py":
        return out + [path.parent / f"__init__{s}" for s in suffixes]
    stem = path.with_suffix("")
    return out + [stem / f"__init__{s}" for s in suffixes] + [stem.with_name(stem.name + s) for s in suffixes]


def test_every_file_the_gate_loads_is_gate_relevant():
    """Walk what `from exo import gate` imports and reads (#1151 review, F1). Each of those files, each file Python
    would import in its place, and a module in src/ named like a top-level module they import must be gate-relevant."""
    seen: set[str] = set()
    todo = ["exo", "exo.gate"]
    files: set[Path] = set()
    candidates: set[Path] = set()
    unfollowed: set[tuple[str, str]] = set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        path = _module_file(name)
        assert path.exists(), name
        rel = path.relative_to(ROOT).as_posix()
        reads, other = _file_uses(path)
        files |= {path} | reads
        candidates |= set(_stand_ins(path))
        for top in _top_level_imports(path):
            candidates |= {SRC / f"{top}.py", SRC / top / "__init__.py"}
        unfollowed |= {(rel, where) for where in other - NOT_RUN_BY_THE_GATE.get(rel, set())}
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        for imported in sorted(_exo_imports(path, package)):
            parts = imported.split(".")
            todo.extend(".".join(parts[: i + 1]) for i in range(len(parts)))
    assert not unfollowed, (
        f"a way to find, read or run another file that this walk cannot follow: {sorted(unfollowed)}. Follow it "
        "here, or add the function to NOT_RUN_BY_THE_GATE with the reason the gate never runs it."
    )
    loaded = sorted(p.relative_to(ROOT).as_posix() for p in files)
    assert {"src/exo/__init__.py", "src/exo/gate.py", "src/exo/store.py", "src/exo/core/models/catalog.toml"} <= set(
        loaded
    ), loaded
    every = sorted({p.relative_to(ROOT).as_posix() for p in files | candidates})
    assert "src/exo/store/__init__.py" in every and "src/tomllib.py" in every, every
    missing = [p for p in every if not is_gate_relevant(p)]
    assert not missing, f"files the comparator can run with the gate but the preflight would skip: {missing}"


def test_name_status_keeps_both_paths_of_a_rename_or_copy():
    out = "R100\0src/exo/gate.py\0src/exo/gate2.py\0C075\0a.py\0b.py\0D\0uv.lock\0M\0README.md\0"
    assert changed_paths_from_name_status(out) == ["README.md", "a.py", "b.py", "src/exo/gate.py", "src/exo/gate2.py", "uv.lock"]


def test_git_diff_lists_names_git_would_quote(tmp_path, monkeypatch):
    """Without -z git C-quotes the first two names, so they matched no pattern and the check was skipped. The third
    has a space, which -z keeps as it is."""
    monkeypatch.chdir(tmp_path)
    for cmd in (
        ["git", "init"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "base"], check=True, capture_output=True, text=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    names = ["plugins/exo-gate/exo_gate/é.py", 'tests/security/test_gate"q".py', "src/exo/gate/a b.py"]
    for name in names:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# new\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "add"], check=True, capture_output=True, text=True)
    paths, err = git_changed_paths(base_sha)
    assert err is None
    assert sorted(paths) == sorted(names)
    for name in names:
        assert diff_touches_gate_paths([name]), name
