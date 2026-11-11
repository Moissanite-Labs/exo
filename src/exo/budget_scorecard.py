"""Shared D-083 core budget counters used by ``test_core_line_budget`` and opt-in tooling.

The same paths, constants and AST rules as ``tests/integration/test_wave1_acceptance.py`` so
reports cannot drift from the gate test.
"""
from __future__ import annotations

import ast
import collections
import os
from dataclasses import dataclass
from pathlib import Path

CORE = ["src/exo/core", "src/exo/protocol", "src/exo/server", "src/exo/cli_chat.py", "src/exo/upstream.py"]
CORE_FLOOR = 14813
INWARD_IMPORTS_MAX = 39
PRIVATE_STATE_MAX = 8


def find_repo_root() -> Path:
    """The checkout root: ``EXO_SRC``, else parent of the installed ``exo`` package."""
    raw = os.environ.get("EXO_SRC")
    if raw:
        root = Path(raw).expanduser()
        if (root / "src" / "exo").is_dir():
            return root
    here = Path(__file__).resolve().parent
    candidate = here.parent.parent
    if (candidate / "src" / "exo" / "core").is_dir():
        return candidate
    raise FileNotFoundError("cannot locate exo checkout for core budget measurement")


@dataclass(frozen=True)
class CoreBudgetSnapshot:
    core_lines: int
    inward_imports: int
    private_state_reads: int


class Score:
    """A measured count, its hotspots, and a string for the failure message."""

    def __init__(self, title: str, cap: int, hits: list[tuple[str, str]]) -> None:
        self.title, self.cap = title, cap
        self.hits = hits
        self.total = len(hits)
        self.by_file = collections.Counter(f for f, _ in hits)
        self.by_name = collections.Counter(n for _, n in hits)

    def __str__(self) -> str:
        return f"{self.total} {self.title} (cap {self.cap})"


def _inward_modules(node: ast.AST, package: tuple[str, ...], repo: Path) -> list[str]:
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    module = node.module or ""
    if node.level:
        parts = list(package[: len(package) - node.level + 1])
        module = ".".join(parts + (module.split(".") if module else []))
    if module != "exo":
        return [module]
    base = repo / "src" / "exo"
    return [
        f"exo.{a.name}"
        for a in node.names
        if (base / a.name).is_dir() or (base / f"{a.name}.py").exists()
    ]


def scorecard(root: Path, *, repo: Path | None = None) -> tuple[Score, Score]:
    """Measure step-4 structure over ``src/exo/core``: inward product imports and private-state reads."""
    repo = repo or find_repo_root()
    imports: set[tuple[str, str]] = set()
    reads: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        where = path.relative_to(repo).as_posix()
        package = path.relative_to(repo / "src").parts[:-1]
        for node in ast.walk(tree):
            for module in _inward_modules(node, package, repo):
                parts = module.split(".")
                if parts[0] == "exo" and len(parts) > 1 and parts[1] not in {"core", "protocol", "server"}:
                    imports.add((where, parts[1]))
        own, called = set(), set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)) or (
                isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
            ):
                if not isinstance(node, ast.Attribute) or (
                    isinstance(node.value, ast.Name) and node.value.id == "self"
                ):
                    own.add(getattr(node, "attr", None) or getattr(node, "id", ""))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.arg)):
                own.add(getattr(node, "name", None) or getattr(node, "arg", ""))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                called.add(id(node.func))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not isinstance(node.ctx, ast.Load):
                continue
            if not node.attr.startswith("_") or node.attr.startswith("__"):
                continue
            if isinstance(node.value, ast.Name) and node.value.id in {"self", "cls"}:
                continue
            if node.attr not in own and id(node) not in called:
                reads.append((where, node.attr))
    return Score("inward product imports", INWARD_IMPORTS_MAX, sorted(imports)), Score(
        "cross-module private-state reads", PRIVATE_STATE_MAX, reads
    )


def core_line_total(repo: Path | None = None) -> int:
    repo = repo or find_repo_root()
    files = [
        p
        for c in CORE
        for p in ((repo / c).rglob("*.py") if (repo / c).is_dir() else [repo / c])
        if "generated" not in p.parts
    ]
    return sum(len(p.read_text().splitlines()) for p in files)


def core_budget_snapshot(repo: Path | None = None) -> CoreBudgetSnapshot:
    repo = repo or find_repo_root()
    imports, reads = scorecard(repo / "src/exo/core", repo=repo)
    return CoreBudgetSnapshot(
        core_lines=core_line_total(repo),
        inward_imports=imports.total,
        private_state_reads=reads.total,
    )
