#!/usr/bin/env python3
"""Never looser than main: compare the gate of two checkouts over a corpus and a policy set (#423).

    python scripts/gate_diff.py --base ../main --head . [--corpus lines.txt ...] [--policies more.py ...]
        [--jobs N] [--json out.json] [--max-examples N] [--home DIR] [--timeout SECS]

Each tree is a checkout of this repo. The script runs `gate.classify` and `gate.decide` for every action of the
corpus under every policy of the set, once in each tree, in two child processes started with `python -I` and the
tree's `src` first on `sys.path`. Each child checks that `gate.__file__` is inside its tree. This process never
imports either gate. Both children get the same small environment (a shared temp HOME holding `~/.exo/state`,
`~/.ssh`, `~/.kube` and `~/.config/gh`, no `EXO_*`, `DIVAN_*` or `CLAUDE_CONFIG_DIR`) and run in that HOME, so
store and secret paths classify the same way in both. The HOME is kept out of /tmp (work_dir).

A pair (action, policy) is lowered when the head's outcome ranks below the base's (allow < log < ask < deny), when
the head keeps the outcome but has fewer reserved hits than the base (or loses a reserved category), or when any of
the base's posting violations is missing from the head's (compared by text, so a reworded violation counts too). At
an equal deny, lost reserved hits and violations are not counted: the hook blocks the call, and both only matter
for an ask (a reserved hit keeps a standing grant from covering it, a violation is named in its message). A classify
or decide exception counts as the outcome the hook gives on an error: ask, or allow for a read tool whose arguments
name no secret. Tightenings are counted only. Actions whose category or target changed while every decision stayed
the same are counted with a few examples.

An action is {"tool", "args"}, with an optional "cwd": the session folder the hook passes to classify (#209). Each
action runs as written. A plain command line is the terminal call {"tool": "terminal", "args": {"command": line}}.

The built-in corpus is harvested from the gate tests of both trees (tests/security/test_gate*.py and
plugins/exo-gate/tests/*.py, read with `ast`, never run), so a head that deletes a test row still runs its line.
The literal tables of both gates (GATE_TABLES, gate_tables) are read the same way. To that come the generated forms:
- a line for each entry of the program, prefix and shell tables (table_lines: each PROGRAMS key, SUBCOMMANDS pair,
  prefix, wrapper, tmux command, shell and interpreter flag) and of the allowlists (allowlist_lines);
- compound variants (VARIANTS) of each simple line of the harvest, of the --corpus files, of SEED_COMMANDS and of
  the table lines;
- each line under the other shell tool names and keys the gate reads (shell_forms: every seed under each, every
  other line under one in turn);
- folder rows (cwd_rows): calls that read a secret store only as resolved from the session folder or from the call's
  own cwd (#209), each of which main decides otherwise without the folder;
- calls of the other tools (tool_actions): each TOOLS key, each name classify compares a tool with and a name for
  each NAME_HINTS word, store and secret paths under each path key, and posts and targets under each text, title and
  target key.
The built-in policies are below (BUILTIN_POLICIES).

Left out: the environment of a real seat (EXO_HOME, EXO_PRINCIPAL_STORE, EXO_KEYS_FILE and EXO_BUS_* move the
stores, and both trees run without them), session folders other than the corpus's own "cwd" values and cwd_rows,
argument keys and tool names the gate reads outside its tables (only where a test names them), and every hook step
after decide: standing grants and NO_GRANT, binds_exact, the registry risk floor, the required executor, a policy
changed mid-session and the decision model.

Exit status: 0 when nothing is lowered, 1 when anything is lowered, 2 when a tree could not be run, the script
itself failed (a bad flag or path, a policy file that raises or holds no plain data) or a SIGTERM or SIGHUP stopped
it. Both tree processes and the work folder are removed on each of these. A stop signal the script started with
ignored (under nohup) stays ignored, so the run goes on.
"""
from __future__ import annotations

import argparse
import ast
import collections
import gzip
import itertools
import json
import os
import runpy
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zlib
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any, Callable, Collection, Iterable, Iterator

RANK = {"allow": 0, "log": 1, "ask": 2, "deny": 3}
# The calls the gate reads as a shell line (gate._runs_shell, gate.classify): the tool names _runs_shell lists, with
# the line under "command" or "cmd", and a tool not in gate.TOOLS whose name ends in terminal or shell, when its
# "command" is a string. build_corpus adds any other name a tree's _runs_shell lists.
SHELL_TOOLS = ("terminal", "shell", "bash", "run_command", "exec_command")
SHELL_SUFFIXES = ("terminal", "shell")
SHELL_NAMES = SHELL_TOOLS + ("remote_terminal", "remote_shell", "powershell", "mcp__host__terminal")
SHELL_KEYS = ("command", "cmd")


def shell_forms(tools: Iterable[str] = SHELL_TOOLS) -> tuple[tuple[str, str], ...]:
    """Each (tool name, key) the gate reads as a shell line, but the canonical terminal and command."""
    tools = tuple(dict.fromkeys(tools))
    names = tools + tuple(n for n in SHELL_NAMES if n not in tools)
    return tuple((t, k) for t in names for k in SHELL_KEYS
                 if (t in tools or k == "command") and (t, k) != ("terminal", "command"))


SHELL_FORMS = shell_forms()
HARVEST_GLOBS = ("tests/security/test_gate*.py", "plugins/exo-gate/tests/*.py")
MAX_COMMAND = 10000           # longer harvested lines (stress tests) are left out


# --- built-in policies ---------------------------------------------------------------------------------------------

def _rule(command: Any = None, decision: str = "allow", **match: Any) -> dict:
    m = dict(match)
    if command is not None:
        m["command"] = command
    return {"match": m, "decision": decision, "reason": f"{decision} {command if command is not None else match}"}


_MAIN = ("delete", "force_push", "store_write", "credentials", "github_post", "github_close", "opaque_exec",
         "local_write", "vcs_push", "vcs_merge", "history_rewrite", "remote_write", "shell", "unknown")
_EXPOSURES = ("private", "internal", "public")
_ROWS = ("reversible", "costly", "irreversible")
_ALLOW_ALL = {r: {e: "allow" for e in _EXPOSURES} for r in _ROWS}
_ASK_ALL = {r: {e: "ask" for e in _EXPOSURES} for r in _ROWS}
_DENY_SOME = {c: "deny" for c in ("delete", "force_push", "github_post", "vcs_push", "local_write", "opaque_exec",
                                 "web_post", "credentials", "web_read")}

BUILTIN_POLICIES: dict[str, dict] = {
    "default": {},
    # deny lists by category
    **{f"deny_{c}": {"gates": {"deny": [c]}}
       for c in ("force_push", "delete", "store_write", "credentials", "github_post", "opaque_exec", "local_write")},
    # category lines for the main categories
    **{f"categories_{o}": {"gates": {"categories": {c: o for c in _MAIN}}} for o in ("deny", "ask", "allow", "log")},
    "category_delete_allow": {"gates": {"categories": {"delete": "allow"}}},
    "category_local_write_deny": {"gates": {"categories": {"local_write": "deny"}}},
    "category_store_write_deny": {"gates": {"categories": {"store_write": "deny"}}},
    "category_vcs_push_allow": {"gates": {"categories": {"vcs_push": "allow", "vcs_merge": "allow"}}},
    "category_opaque_exec_allow": {"gates": {"categories": {"opaque_exec": "allow"}}},
    "category_opaque_exec_log": {"gates": {"categories": {"opaque_exec": "log"}}},
    "rule_opaque_exec_allow": {"gates": {"rules": [_rule(".", category="opaque_exec")]}},
    "rule_opaque_exec_log_then_allow": {"gates": {"rules": [_rule("^ls", "log", category="opaque_exec"),
                                                            _rule(".", category="opaque_exec")]}},
    # reserved entries
    "reserved_store_write": {"gates": {"reserved": ["store_write"]}},
    "reserved_delete_force_push": {"gates": {"reserved": ["delete", "force_push"]}},
    "reserved_command_rm": {"gates": {"reserved": ["command:\\brm\\b"]}},
    "reserved_target_ssh": {"gates": {"reserved": ["target:*/.ssh/*"]}},
    "reserved_post_mapping": {"gates": {"reserved": [{"category": "github_post", "target": "pub/*"}]}},
    "reserved_tool_terminal": {"gates": {"reserved": ["tool:terminal"]}},
    # scopes
    "scope_internal_all": {"gates": {"scopes": {"internal": ["*"]}}},
    "scope_private_all": {"gates": {"scopes": {"private": ["*"]}}},
    "scope_private_own": {"gates": {"scopes": {"private": ["own/*", "telegram:own-*"], "internal": ["pub/*"]}}},
    # target deny entries
    "deny_target_ssh": {"gates": {"deny": ["target:*/.ssh/*"]}},
    "deny_target_pub": {"gates": {"deny": ["target:pub/*"]}},
    "deny_target_store": {"gates": {"deny": ["target:*/.exo/*", "target:*.exo*"]}},
    "deny_target_dev_proc": {"gates": {"deny": ["target:/dev*", "target:/proc*"]}},
    # command allow rules
    "allow_ls_anchored": {"gates": {"rules": [_rule("^ls( |$)")]}},
    "allow_ls_unanchored": {"gates": {"rules": [_rule("ls")]}},
    "allow_git_status": {"gates": {"rules": [_rule("^git status$")]}},
    "allow_git_push": {"gates": {"rules": [_rule("^git push")]}},
    "allow_git_push_force_reserved": {"gates": {"rules": [_rule("^git push --force origin main$")],
                                                "reserved": ["force_push"]}},
    "allow_rm_rf_build": {"gates": {"rules": [_rule("^rm -rf build$")]}},
    "allow_rm_rf_build_delete_deny": {"gates": {"categories": {"delete": "deny"},
                                                "rules": [_rule("^rm -rf build$", category="delete")]}},
    "allow_bash_c": {"gates": {"rules": [_rule("^bash -c ")]}},
    "allow_ssh": {"gates": {"rules": [_rule("^ssh ")]}},
    "allow_everything": {"gates": {"rules": [_rule("")]}},
    "allow_everything_under_denies": {"gates": {"categories": _DENY_SOME, "rules": [_rule(".*")]}},
    "allow_command_list": {"gates": {"rules": [_rule(["^ls", "^git status", "^rm -rf build$", "^make test$"])]}},
    # command ask and deny rules
    "ask_curl": {"gates": {"rules": [_rule("curl", "ask")]}},
    "ask_git": {"gates": {"rules": [_rule("^git", "ask")]}},
    "deny_rule_rm": {"gates": {"rules": [_rule("\\brm\\b", "deny")]}},
    "deny_rule_push_force": {"gates": {"rules": [_rule("push.*(-f|--force)", "deny")]}},
    "deny_command_force": {"gates": {"deny": ["command:git push (-f|--force)"]}},
    "deny_command_store": {"gates": {"deny": ["command:\\.exo/"]}},
    # rule orders
    "order_allow_then_ask": {"gates": {"rules": [_rule("^git"), _rule("git push", "ask")]}},
    "order_ask_then_allow": {"gates": {"rules": [_rule("git push", "ask"), _rule("^git")]}},
    "order_log_then_allow": {"gates": {"rules": [_rule("^ls", "log"), _rule(".*")]}},
    "order_ask_under_category_deny": {"gates": {"categories": {"delete": "deny", "force_push": "deny"},
                                                "rules": [_rule("^git status", "ask"), _rule(".*")]}},
    "order_deny_then_allow": {"gates": {"categories": {"delete": "deny"},
                                        "rules": [_rule("curl", "deny"), _rule(["^ls", "^git", "^rm -rf build$"])]}},
    "order_ask_then_log_all": {"gates": {"categories": _DENY_SOME, "rules": [_rule("^git", "ask"), _rule(".", "log")]}},
    # matrix
    "matrix_deny_cells": {"gates": {"matrix": {"irreversible": {"public": "deny", "internal": "deny"},
                                               "costly": {"public": "deny"}}}},
    "matrix_all_allow": {"gates": {"matrix": _ALLOW_ALL}},
    "matrix_all_ask": {"gates": {"matrix": _ASK_ALL}},
    "matrix_allow_log_then_allow": {"gates": {"matrix": _ALLOW_ALL, "rules": [_rule("^ls$", "log"), _rule(".*")]}},
    "matrix_ask_log_then_allow": {"gates": {"matrix": _ASK_ALL, "rules": [_rule("^git status$", "log"), _rule("git")]}},
    # posting rules
    "posting_footer": {"posting": {"footer": "-- sent by an agent"}},
    "posting_forbid_words": {"posting": {"forbid_words": ["secret", "internal", "hi"]}},
    "posting_forbid_patterns": {"posting": {"forbid": [{"pattern": "https?://", "label": "link"}, "password"]}},
    "posting_max_chars": {"posting": {"max_chars": 20}},
    "posting_scrub_names": {"posting": {"scrub": {"names": ["builder", "scout", "acme-internal"]}},
                            "internal_names": ["builder"]},
    "posting_agent_names": {"posting": {"forbid_agent_names": True}, "agents": ["builder", "scout"]},
    "posting_close_and_merge": {"posting": {"applies_to": ["github_post", "github_close", "vcs_merge", "message_send"],
                                            "footer": "-- bot", "forbid_words": ["done", "secret"]}},
    "posting_forbid_ignore_case": {"posting": {"forbid": [{"pattern": "secret", "label": "secret", "ignore_case": True}]}},
    "posting_footer_everywhere": {"gates": {"scopes": {"private": ["*"]}},
                                  "posting": {"footer": "-- bot", "footer_exposures": list(_EXPOSURES)}},
    # escalate settings
    "escalate_off": {"gates": {"escalate": {"third_party": False, "cost_over_budget": False, "categories": []}}},
    "escalate_more": {"gates": {"escalate": {"categories": ["delete", "store_write", "local_write", "opaque_exec",
                                                           "vcs_push", "credentials"]}}},
    "loosest": {"gates": {"matrix": _ALLOW_ALL, "categories": {c: "allow" for c in _MAIN},
                          "escalate": {"third_party": False, "cost_over_budget": False, "categories": []},
                          "rules": [_rule(".*")]}},
    # other match keys and the budget
    "tool_rules": {"gates": {"rules": [{"match": {"tool": "write_file"}, "decision": "allow", "reason": "files"},
                                       {"match": {"tool": "send_message", "target": "telegram:own-*"},
                                        "decision": "allow", "reason": "own chat"}],
                             "deny": ["tool:browser_*"]}},
    "axis_rules": {"gates": {"rules": [{"match": {"exposure": "public"}, "decision": "deny", "reason": "public"},
                                       {"match": {"reversibility": "irreversible"}, "decision": "log", "reason": "irr"}]}},
    "third_party_allow_gh": {"gates": {"rules": [_rule("^gh ", third_party=True)]}},
    "budget_spent": {"policy": {"budget": {"per_action_max": 1, "per_day_max": 5}, "gates": {"rules": [_rule(".*")]}},
                     "context": {"spent_today": 10}},
    # rules written flat and named, and the keys normalize_policies only carries along
    "rules_flat_named": {"gates": {"rules": [{"command": "^git status$", "decision": "allow", "name": "status"},
                                             {"category": "delete", "decision": "log", "name": "deletes"}],
                                   "ask_default": "deny"},
                         "budget": {"per_month_ceiling": 50},
                         "approvals": {"approvers": {"default": ["principal"]}, "always": {"days": 1}},
                         "interruptions": {"quiet": True}},
}


def _entry(raw: Any) -> dict:
    """A policy as {"policy": ..., "context": ...}. A plain mapping is a policy with no context."""
    if isinstance(raw, dict) and raw and set(raw) <= {"policy", "context"}:
        return {"policy": raw.get("policy") or {}, "context": raw.get("context")}
    return {"policy": raw or {}, "context": None}


def load_policy_file(path: Path) -> dict[str, dict]:
    """Policies from a .json file (a name -> policy mapping, or a list) or a .py file (POLICIES, and CONTEXTS as
    name -> (policy, context)). The file is plain data: it must not import a gate."""
    if path.suffix == ".py":
        ns = runpy.run_path(str(path))
        raw = dict(ns.get("POLICIES") or {})
        for name, (pol, ctx) in dict(ns.get("CONTEXTS") or {}).items():
            raw[name] = {"policy": pol, "context": ctx}
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        raw = {f"{path.stem}[{i}]": p for i, p in enumerate(raw)}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a mapping of name to policy")
    out = {str(k): _entry(v) for k, v in raw.items()}
    try:
        json.dumps(out)       # must travel to the trees as JSON
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path}: a policy is not plain JSON data ({exc})") from None
    return out


# --- corpus harvest ------------------------------------------------------------------------------------------------

_SHELL_FNS = {"classify_shell"}
_ACTION_FNS = {"classify"}
_CODE_FNS = {"classify_code"}
_HOOK_FNS = {"pre_tool_call", "apre_tool_call"}
_DIRECT = _SHELL_FNS | _ACTION_FNS | _CODE_FNS | _HOOK_FNS
_CMD_NAMES = ("cmd", "command", "line", "commands", "lines", "cmds")
_CAP = 4096                   # values one expression may stand for
_DEPTH = 16


class _Unknown:
    def __repr__(self) -> str:
        return "<unknown>"


UNKNOWN = _Unknown()


class _Param(tuple):
    """pytest.param(...): its positional values."""


class _Scope:
    """Names an expression may refer to: each name maps to a list of bindings (kind, node, scope, extra)."""

    def __init__(self, parent: "_Scope | None" = None):
        self.names: dict[str, list[tuple]] = {}
        self.parent = parent
        self.module: _Module | None = None

    def bind(self, name: str, kind: str, node: ast.AST | None, scope: "_Scope | None" = None, extra: Any = None):
        self.names.setdefault(name, []).append((kind, node, scope or self, extra))

    def lookup(self, name: str) -> list[tuple] | None:
        s: _Scope | None = self
        while s is not None:
            if name in s.names:
                return s.names[name]
            s = s.parent
        return None


def _callee(call: ast.Call) -> str:
    f = call.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def _names_in(node: ast.AST) -> set[str]:
    """The names a call uses, but not those only used as a key (``TABLE[name]``): such a name is a row label."""
    out, todo = set(), [node]
    while todo:
        n = todo.pop()
        if isinstance(n, ast.Name):
            out.add(n.id)
        todo.extend(ch for ch in ast.iter_child_nodes(n) if not (isinstance(n, ast.Subscript) and ch is n.slice))
    return out


def _own_nodes(body: Iterable[ast.AST]) -> Iterator[ast.AST]:
    """Every node below these statements, nested function and class bodies left out (lambdas kept)."""
    todo = list(body)
    while todo:
        n = todo.pop()
        yield n
        for ch in ast.iter_child_nodes(n):
            if not isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                todo.append(ch)


class _Func:
    def __init__(self, node: ast.FunctionDef | ast.AsyncFunctionDef, module: "_Module", method: bool):
        self.node, self.module, self.method = node, module, method
        a = node.args
        self.params = [p.arg for p in a.posonlyargs + a.args]
        self.kwonly = [p.arg for p in a.kwonlyargs]
        n_def = len(a.defaults)
        self.defaults = dict(zip(self.params[len(self.params) - n_def:], a.defaults)) if n_def else {}
        self.defaults.update({p.arg: d for p, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None})
        self.rets = [n.value for n in _own_nodes(node.body) if isinstance(n, ast.Return) and n.value is not None]
        self.calls = [n for n in _own_nodes(node.body) if isinstance(n, ast.Call)]
        self.feeds = False

    def scope(self, parent: _Scope) -> _Scope:
        """A fresh scope for one evaluation of the body: locals, loop and comprehension targets, and parametrize
        rows."""
        s = _Scope(parent)
        for n in _own_nodes(self.node.body):
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    _bind_target(s, t, n.value)
            elif isinstance(n, ast.AnnAssign) and n.value is not None:
                _bind_target(s, n.target, n.value)
            elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)):
                _bind_target(s, n.target, n.iter, loop=True)
        for dec in self.node.decorator_list:
            if isinstance(dec, ast.Call) and _callee(dec) == "parametrize" and len(dec.args) >= 2:
                names = _argnames(dec.args[0])
                for i, name in enumerate(names):
                    s.bind(name, "row", dec.args[1], parent, (i, len(names)))
        return s


def _argnames(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [x.strip() for x in node.value.split(",") if x.strip()]
    if isinstance(node, (ast.List, ast.Tuple)):
        return [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return []


def _bind_target(s: _Scope, target: ast.AST, value: ast.AST, loop: bool = False, index: tuple = ()) -> None:
    if isinstance(target, ast.Name):
        s.bind(target.id, "elem" if loop else "expr", value, s, index)
    elif isinstance(target, (ast.Tuple, ast.List)):
        if not loop and isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) == len(target.elts):
            for t, v in zip(target.elts, value.elts):
                _bind_target(s, t, v)
        else:
            for i, t in enumerate(target.elts):
                _bind_target(s, t, value, loop, index + (i,))


def _pick(v: Any, index: tuple) -> Any:
    for i in index:
        if not isinstance(v, (list, tuple)) or len(v) <= i:
            return UNKNOWN
        v = v[i]
    return v


def _product(groups: list[list]) -> Iterator[tuple]:
    return itertools.islice(itertools.product(*groups), _CAP)


class _Module:
    def __init__(self, path: Path, tree: ast.Module, pool: dict[str, list[_Func]]):
        self.path, self.tree, self.pool = path, tree, pool
        self.scope = _Scope()
        self.funcs: dict[str, list[_Func]] = {}
        for st in tree.body:
            if isinstance(st, ast.Assign):
                for t in st.targets:
                    _bind_target(self.scope, t, st.value)
            elif isinstance(st, ast.AnnAssign) and st.value is not None:
                _bind_target(self.scope, st.target, st.value)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for m in node.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self.funcs.setdefault(m.name, []).append(_Func(m, self, True))
        for st in tree.body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.funcs.setdefault(st.name, []).append(_Func(st, self, False))
                for sub in ast.walk(st):
                    if sub is not st and isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self.funcs.setdefault(sub.name, []).append(_Func(sub, self, False))

    def helpers(self, name: str) -> list[_Func]:
        return self.funcs.get(name) or self.pool.get(name) or []


class Harvest:
    """Shell lines and tool actions the gate suites feed to gate.classify, read statically."""

    def __init__(self) -> None:
        self.commands: dict[str, None] = {}
        self.actions: dict[str, dict] = {}

    # values ------------------------------------------------------------------------------------------------------
    def values(self, node: ast.AST | None, scope: _Scope, depth: int = 0, active: frozenset = frozenset()) -> list:
        if node is None or depth > _DEPTH:
            return []
        out = self._values(node, scope, depth + 1, active)
        return out[:_CAP]

    def _values(self, node: ast.AST, scope: _Scope, depth: int, active: frozenset) -> list:
        V = self.values
        if isinstance(node, ast.Constant):
            return [node.value] if isinstance(node.value, (str, int, float, bool, type(None))) else []
        if isinstance(node, ast.Name):
            return self._name(node.id, scope, depth, active)
        groups: list[list] = []
        out: list = []
        if isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.Constant):
                    groups.append([str(part.value)])
                elif isinstance(part, ast.FormattedValue) and part.format_spec is None:
                    vals = [v for v in V(part.value, scope, depth, active) if v is not UNKNOWN]
                    conv: Callable[[Any], str] = repr if part.conversion == 114 else ascii if part.conversion == 97 else str
                    groups.append([conv(v) for v in vals if isinstance(v, (str, int, float, bool))])
                else:
                    return []
            return ["".join(p) for p in _product(groups)]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = V(node.left, scope, depth, active), V(node.right, scope, depth, active)
            for a, b in _product([left, right]):
                if isinstance(a, str) and isinstance(b, str):
                    out.append(a + b)
                elif isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
                    out.append(list(a) + list(b))
            if not left or not right:       # a list plus a part not read: keep the rows that were read
                out.extend(list(v) for v in left + right if isinstance(v, (list, tuple)))
            return out
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for e in node.elts:
                if isinstance(e, ast.Starred):
                    star = [("*", list(v)) for v in V(e.value, scope, depth, active) if isinstance(v, (list, tuple, dict))]
                    if star:                # a part not read is left out, the other elements kept
                        groups.append(star)
                else:
                    vals = V(e, scope, depth, active)
                    if vals or isinstance(node, ast.Tuple):
                        groups.append(vals or [UNKNOWN])
            out = []
            for combo in _product(groups):
                items: list = []
                for x in combo:
                    if isinstance(x, tuple) and len(x) == 2 and x[0] == "*" and isinstance(x[1], (list, tuple)):
                        items.extend(x[1])
                    else:
                        items.append(x)
                out.append(tuple(items) if isinstance(node, ast.Tuple) else items)
            return out
        if isinstance(node, ast.Dict):
            keys: list = []
            for k, v in zip(node.keys, node.values):
                if k is None:
                    keys.append(None)
                    groups.append([d for d in V(v, scope, depth, active) if isinstance(d, dict)] or [{}])
                    continue
                kv = [x for x in V(k, scope, depth, active) if isinstance(x, (str, int, float, bool, type(None)))]
                if not kv:
                    continue
                keys.append(kv[0])
                groups.append(V(v, scope, depth, active) or [UNKNOWN])
            for combo in _product(groups):
                d: dict = {}
                for key, val in zip(keys, combo):
                    if key is None:
                        d.update(val)
                    else:
                        d[key] = val
                out.append(d)
            return out
        if isinstance(node, ast.IfExp):
            return V(node.body, scope, depth, active) + V(node.orelse, scope, depth, active)
        if isinstance(node, (ast.ListComp, ast.GeneratorExp, ast.SetComp)):
            # One value: the list of every element (the elements of each alternative of the iterables together).
            # Filters are not read, so the list may hold more than the code makes. That only adds lines.
            if self._comp(node.elt, None, node.generators, scope, depth, active, out):
                return [out]
            return []
        if isinstance(node, ast.DictComp):
            pairs: list = []
            if self._comp(node.key, node.value, node.generators, scope, depth, active, pairs):
                return [{k: v for k, v in pairs if isinstance(k, (str, int, float, bool, type(None)))}]
            return []
        if isinstance(node, ast.Call):
            return self._call_values(node, scope, depth, active)
        return []

    def _comp(self, elt: ast.AST, val: ast.AST | None, gens: list[ast.comprehension], scope: _Scope, depth: int,
              active: frozenset, out: list) -> bool:
        """Add the elements of a comprehension to ``out`` (key, value pairs when ``val`` is given). False when no
        iterable could be read."""
        gen, rest = gens[0], gens[1:]
        read = False
        for seq in self.values(gen.iter, scope, depth, active):
            if not isinstance(seq, (list, tuple, dict)):
                continue
            read = True
            for item in list(seq)[:_CAP]:
                s = _Scope(scope)
                _bind_value(s, gen.target, item)
                if rest:
                    self._comp(elt, val, rest, s, depth, active, out)
                elif val is None:
                    out.extend(self.values(elt, s, depth, active))
                else:
                    out.extend(_product([self.values(elt, s, depth, active) or [UNKNOWN],
                                         self.values(val, s, depth, active) or [UNKNOWN]]))
                if len(out) >= _CAP:
                    return True
        return read

    def _name(self, name: str, scope: _Scope, depth: int, active: frozenset) -> list:
        binds = scope.lookup(name)
        if not binds:
            return []
        out: list = []
        for kind, node, s, extra in binds:
            key = (id(s), name, id(node))
            if key in active:
                continue
            act = active | {key}
            if kind == "value":
                out.append(extra)
            elif kind == "expr":
                out.extend(_pick(v, extra) if extra else v for v in self.values(node, s, depth, act))
            elif kind == "elem":
                for seq in self.values(node, s, depth, act):
                    if isinstance(seq, (list, tuple, dict)):
                        out.extend(_pick(x, extra) for x in list(seq)[:_CAP])
            elif kind == "row":
                i, n = extra
                for rows in self.values(node, s, depth, act):
                    if not isinstance(rows, (list, tuple, dict)):
                        continue
                    for row in list(rows):
                        if isinstance(row, _Param):
                            row = row[0] if n == 1 and row else row
                        out.append(row if n == 1 else _pick(row, (i,)))
            if len(out) >= _CAP:
                break
        return out

    def _call_values(self, node: ast.Call, scope: _Scope, depth: int, active: frozenset) -> list:
        V = self.values
        name = _callee(node)
        if name == "param":
            groups = [V(a, scope, depth, active) or [UNKNOWN] for a in node.args if not isinstance(a, ast.Starred)]
            return [_Param(c) for c in _product(groups)]
        if name == "join" and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Constant) \
                and isinstance(node.func.value.value, str) and node.args:
            sep = node.func.value.value
            return [sep.join(seq) for seq in V(node.args[0], scope, depth, active)
                    if isinstance(seq, (list, tuple)) and all(isinstance(x, str) for x in seq)]
        if name in ("list", "tuple", "sorted", "set", "frozenset", "reversed") and isinstance(node.func, ast.Name) \
                and len(node.args) == 1:
            return [list(v) for v in V(node.args[0], scope, depth, active) if isinstance(v, (list, tuple, dict))]
        if name in ("items", "keys", "values") and isinstance(node.func, ast.Attribute) and not node.args:
            dicts = [d for d in V(node.func.value, scope, depth, active) if isinstance(d, dict)]
            return [[tuple(kv) for kv in d.items()] if name == "items" else list(getattr(d, name)()) for d in dicts]
        if name == "dict" and isinstance(node.func, ast.Name) and not node.args:
            groups = [V(k.value, scope, depth, active) or [UNKNOWN] for k in node.keywords if k.arg]
            keys = [k.arg for k in node.keywords if k.arg]
            return [dict(zip(keys, c)) for c in _product(groups)]
        mod = _module_of(scope)
        out: list = []
        for fn in (mod.helpers(name) if mod else []):
            if not fn.rets:
                continue
            s = self._bind_call(fn, node, scope, depth, active)
            for r in fn.rets:
                out.extend(self.values(r, s, depth, active))
        return out

    def _bind_call(self, fn: _Func, call: ast.Call, scope: _Scope, depth: int, active: frozenset) -> _Scope:
        s = fn.scope(fn.module.scope)
        params = fn.params[1:] if fn.method and fn.params[:1] in (["self"], ["cls"]) else fn.params
        given: dict[str, tuple] = {}
        for p, a in zip(params, call.args):
            if isinstance(a, ast.Starred):
                break
            given[p] = (a, scope)
        for k in call.keywords:
            if k.arg:
                given[k.arg] = (k.value, scope)
        for p in params + fn.kwonly:
            if p in given:
                node, sc = given[p]
                s.names[p] = [("expr", node, sc, ())]
            elif p in fn.defaults:
                s.names[p] = [("expr", fn.defaults[p], fn.module.scope, ())]
        return s

    # feeding -----------------------------------------------------------------------------------------------------
    def feeds(self, call: ast.Call, mod: _Module) -> bool:
        name = _callee(call)
        return name in _DIRECT or any(f.feeds for f in mod.helpers(name))

    def feed(self, call: ast.Call, scope: _Scope, mod: _Module, depth: int = 0) -> int:
        """Record what this call hands to gate.classify. Returns how many values it gave."""
        if depth > 6:
            return 0
        name, args = _callee(call), call.args
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        n = 0
        if name in _SHELL_FNS and args:
            for v in self.values(args[0], scope):
                n += self.add_command(v)
        elif name in _ACTION_FNS and (args or "action" in kw):
            cwds = self._cwds(args[1] if len(args) > 1 else kw.get("cwd"), scope)
            for v in self.values(args[0] if args else kw["action"], scope):
                for cwd in cwds:
                    n += self.add_action(v, cwd)
        elif name in _CODE_FNS and args:
            for v in self.values(args[0], scope):
                if isinstance(v, str):
                    n += self.add_action({"tool": "execute_code", "args": {"code": v}})
        elif name in _HOOK_FNS:
            tool = args[0] if args else kw.get("tool_name")
            targs = args[1] if len(args) > 1 else kw.get("args")
            cwds = self._cwds(kw.get("cwd"), scope)
            for t, a in _product([self.values(tool, scope), self.values(targs, scope)]):
                if isinstance(t, str) and isinstance(a, dict):
                    for cwd in cwds:
                        n += self.add_action({"tool": t, "args": a}, cwd)
        else:
            for fn in mod.helpers(name):
                if not fn.feeds:
                    continue
                s = self._bind_call(fn, call, scope, 0, frozenset())
                for inner in fn.calls:
                    if self.feeds(inner, fn.module):
                        n += self.feed(inner, s, fn.module, depth + 1)
        if not n:               # nothing evaluated: keep the literal command strings the call holds
            for d in ast.walk(call):
                if isinstance(d, ast.Dict):
                    for k, v in zip(d.keys, d.values):
                        if isinstance(k, ast.Constant) and k.value in ("command", "cmd") \
                                and isinstance(v, ast.Constant) and isinstance(v.value, str):
                            n += self.add_command(v.value)
        return n

    def _cwds(self, node: ast.AST | None, scope: _Scope) -> list[str | None]:
        """The session folders a call passes to classify: None (always, as the hook passes none when it has none)
        and each literal folder the argument may hold."""
        found = self.values(node, scope) if node is not None else []
        return [None] + list(dict.fromkeys(v for v in found if isinstance(v, str) and v.strip() and "\x00" not in v))

    # results -----------------------------------------------------------------------------------------------------
    def add_command(self, v: Any) -> int:
        if not isinstance(v, str) or not v.strip() or len(v) > MAX_COMMAND or "\x00" in v:
            return 0
        self.commands.setdefault(v, None)
        return 1

    def add_action(self, v: Any, cwd: str | None = None) -> int:
        """Keep the call as written: its tool name and every argument, and ``cwd`` when the caller passed one. Only a
        terminal call with nothing but a command is kept as that command, its canonical form."""
        if isinstance(v, _Param):
            v = v[0] if len(v) == 1 else tuple(v)
        if isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[0], str):
            tool, args = v
        elif isinstance(v, dict):
            tool = v.get("tool") or v.get("tool_name") or v.get("name")
            args = v.get("args") if "args" in v else v.get("arguments", {})
        else:
            return 0
        if not isinstance(tool, str) or not tool:
            return 0
        if not isinstance(args, (dict, str)) or _has_unknown(args):
            return 0
        if cwd is None and tool == "terminal" and isinstance(args, dict) and set(args) == {"command"}:
            return self.add_command(args["command"])
        action: dict[str, Any] = {"tool": tool, "args": _plain(args)}
        if cwd is not None:
            action["cwd"] = cwd
        try:
            key = json.dumps(action, sort_keys=True)
        except (TypeError, ValueError):
            return 0
        self.actions.setdefault(key, action)
        return 1


def _bind_value(s: _Scope, target: ast.AST, value: Any) -> None:
    if isinstance(target, ast.Name):
        s.bind(target.id, "value", None, s, value)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for i, t in enumerate(target.elts):
            _bind_value(s, t, _pick(value, (i,)))


def _module_of(scope: _Scope) -> _Module | None:
    s: _Scope | None = scope
    while s is not None:
        if s.module is not None:
            return s.module
        s = s.parent
    return None


def _has_unknown(v: Any) -> bool:
    if v is UNKNOWN:
        return True
    if isinstance(v, dict):
        return any(_has_unknown(x) for x in v.values())
    if isinstance(v, (list, tuple)):
        return any(_has_unknown(x) for x in v)
    return False


def _plain(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def harvest(tree: Path) -> Harvest:
    """Parse the gate test files of ``tree`` and collect what they feed to the gate. Nothing is imported or run."""
    files = sorted({p for g in HARVEST_GLOBS for p in tree.glob(g) if p.is_file()})
    pool: dict[str, list[_Func]] = {}
    mods = []
    for p in files:
        try:
            t = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        m = _Module(p, t, pool)
        m.scope.module = m
        mods.append(m)
    for m in mods:
        for name, fns in m.funcs.items():
            if not name.startswith("test_"):
                pool.setdefault(name, []).extend(fns)
    h = Harvest()
    for _ in range(6):              # a helper that calls a helper that calls classify feeds too
        changed = False
        for m in mods:
            for fns in m.funcs.values():
                for fn in fns:
                    if not fn.feeds and any(h.feeds(c, m) for c in fn.calls):
                        fn.feeds = changed = True
        if not changed:
            break
    for m in mods:
        for c in _own_nodes(m.tree.body):
            if isinstance(c, ast.Call) and h.feeds(c, m):
                h.feed(c, m.scope, m)
        for fns in m.funcs.values():
            for fn in fns:
                s = fn.scope(m.scope)
                param_names = {n for n, b in s.names.items() if any(k == "row" for k, *_ in b)}
                missed: set[str] = set()
                for c in fn.calls:
                    if h.feeds(c, m) and not h.feed(c, s, m):
                        missed |= _names_in(c) & param_names
                for name in param_names:
                    if name in missed or name in _CMD_NAMES or name.endswith(("cmd", "command", "line")):
                        for v in h.values(ast.Name(id=name, ctx=ast.Load()), s):
                            h.add_command(v)
    return h


# --- generated variants --------------------------------------------------------------------------------------------

# A few commands per category, so the variants reach every category even where the suites name it only inside
# longer lines. A capital letter in a post checks that forbidden words still match in any case.
SEED_COMMANDS = (
    "ls", "git status", "cat README.md", "echo hi", "make test", "touch notes.md", "sed -i s/a/b/ config.json",
    "rm x", "rm -rf build", "git commit -m wip", "git reset --hard HEAD~1", "git push origin main",
    "git push --force origin main", "gh pr comment 3 --body hi", 'gh pr comment 3 --body "Secret plan"',
    "gh issue close 3 --comment done",
    "gh pr merge 3 --body done", "curl -X POST https://example.com/api -d x", "npm publish", "python3 x.py",
    "bash x.sh", "cp /tmp/x gate-grants.json", "printf x > gate-grants.json", "tee policies/gates.yaml < /tmp/x",
    "printf x > ~/.exo/state/gate-grants.json", "rm -rf ~/.exo/state", "cat ~/.ssh/id_rsa", "cat ~/.kube/config",
    "gh auth token", "printenv", "ssh host ls", "kubectl delete pod x", "crontab -l", 'eval "$X"', "$CMD",
)

# Each simple command is also run inside these lines. {c} is the command, {q} the command as one quoted word.
VARIANTS = (
    "true; {c}", "{c}; true", "true && {c}", "{c} && true", "echo x | {c}", "{c} | cat", "echo $({c})",
    "bash -c {q}", "sudo {c}", "env X=1 {c}", "timeout 5 {c}", "ssh host {q}", "bash <<'EOF'\n{c}\nEOF",
    "bash <<< {q}", "cd ~/.exo/state && {c}", "cd ~/.exo/state && (cd /dev) && {c}", "cd /dev && {c}",
)

# Calls that read a secret store only as resolved from a folder (#209): the session folder the hook passes to
# classify, a shell call's own cwd argument, or that argument joined to the session folder. A relative `keys`, `run`
# or file below a secret root names no store by itself, so main decides each row otherwise without its folder
# (tests/test_gate_diff.py checks that), and a head that stops reading the folder one of these ways shows.
CWD_LINES = (("~/.exo", "cat keys"), ("~/.exo", "ls run"), ("~/.claude", "cat settings.json"),
             ("~/.exo/keys", "ls"), ("~/.exo/keys", "cat credentials.db"))
CWD_CALLS = (("~/.exo", {"tool": "read_file", "args": {"path": "keys"}}),
             ("~/.exo", {"tool": "list_dir", "args": {"path": "run"}}),
             ("~/.claude", {"tool": "read_file", "args": {"path": "settings.json"}}),
             ("~/.exo/keys", {"tool": "read_file", "args": {"path": "credentials.db"}}),
             ("~/.exo/keys", {"tool": "write_file", "args": {"path": "seat.db", "content": "x"}}),
             ("~/.exo/keys", {"tool": "edit_file", "args": {"file_path": "seat.db", "old_string": "a",
                                                            "new_string": "b"}}))
CWD_JOINED = ("~/.exo", "keys", "ls")     # the session folder, the call's own cwd below it, the line


def cwd_rows(forms: Iterable[tuple[str, str]] = SHELL_FORMS) -> list[dict]:
    """The folder rows: each line of CWD_LINES under terminal and every shell form, once in the folder as the session
    folder and once with it as the call's own cwd; CWD_JOINED's line under each form with its cwd below the session
    folder; and each file call of CWD_CALLS in its session folder."""
    every = (("terminal", "command"), *forms)
    out: list[dict] = []
    for folder, line in CWD_LINES:
        for tool, key in every:
            out.append({"tool": tool, "args": {key: line}, "cwd": folder})
            out.append({"tool": tool, "args": {key: line, "cwd": folder}})
    folder, sub, line = CWD_JOINED
    out += [{"tool": tool, "args": {key: line, "cwd": sub}, "cwd": folder} for tool, key in every]
    out += [{**call, "cwd": folder} for folder, call in CWD_CALLS]
    return out


def without_cwd(action: dict) -> dict:
    """The same call with no session folder and no cwd argument."""
    out = {k: v for k, v in action.items() if k != "cwd"}
    if isinstance(out.get("args"), dict) and "cwd" in out["args"]:
        out["args"] = {k: v for k, v in out["args"].items() if k != "cwd"}
    return out


# --- the gate's own tables -----------------------------------------------------------------------------------------

# The module-level literals of gate.py the generated forms come from. They are read with ast from both trees, never
# imported, so an entry one tree drops or adds still runs.
GATE_TABLES = ("TOOLS", "OUTBOUND", "PROGRAMS", "SUBCOMMANDS", "READ_PROGRAMS", "SHELL_PROGRAMS", "TOOL_SUBCOMMANDS",
               "_RUN_WRAPPERS", "_PREFIXES", "_WRAPPERS", "_WRAPPER_OPERANDS", "_TMUX_RUNS", "_TMUX_ALIASES", "_SHELLS",
               "_INTERPRETERS", "_PATH_ARG_KEYS", "_TEXT_KEYS", "_TITLE_KEYS", "_TARGET_KEYS", "_AMOUNT_KEYS",
               "NAME_HINTS", "_READ_VERBS")
_BUILDERS: dict[str, Callable[..., Any]] = {"frozenset": frozenset, "set": set, "tuple": tuple, "list": list,
                                            "sorted": sorted, "dict": dict}


class _NotLiteral(Exception):
    """An expression gate_tables does not evaluate."""


def _lit(node: ast.AST, env: dict[str, Any]) -> Any:
    """The value of a literal expression: constants, containers, names bound above, comprehensions over them, `+`
    and `|`, and the frozenset, set, tuple, list, sorted, dict, split, items, keys and values calls."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise _NotLiteral(node.id)
        return env[node.id]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        items: list = []
        for e in node.elts:
            if isinstance(e, ast.Starred):
                items.extend(_lit(e.value, env))
            else:
                items.append(_lit(e, env))
        return tuple(items) if isinstance(node, ast.Tuple) else set(items) if isinstance(node, ast.Set) else items
    if isinstance(node, ast.Dict):
        d: dict = {}
        for k, v in zip(node.keys, node.values):
            if k is None:
                d.update(_lit(v, env))
            else:
                d[_lit(k, env)] = _lit(v, env)
        return d
    if isinstance(node, (ast.DictComp, ast.ListComp, ast.SetComp, ast.GeneratorExp)) and len(node.generators) == 1 \
            and not node.generators[0].ifs:
        gen, rows = node.generators[0], []
        for item in _lit(gen.iter, env):
            local = dict(env)
            _lit_bind(gen.target, item, local)
            if isinstance(node, ast.DictComp):
                rows.append((_lit(node.key, local), _lit(node.value, local)))
            else:
                rows.append(_lit(node.elt, local))
        return dict(rows) if isinstance(node, ast.DictComp) else set(rows) if isinstance(node, ast.SetComp) else rows
    if isinstance(node, ast.Call) and not node.keywords:
        args = [_lit(a, env) for a in node.args]
        f = node.func
        if isinstance(f, ast.Name) and f.id in _BUILDERS and len(args) <= 1:
            return _BUILDERS[f.id](*args)
        if isinstance(f, ast.Attribute) and f.attr in ("split", "items", "keys", "values"):
            obj = _lit(f.value, env)
            if f.attr == "split" and isinstance(obj, str) and all(isinstance(a, str) for a in args):
                return obj.split(*args)
            if f.attr != "split" and isinstance(obj, dict) and not args:
                return list(getattr(obj, f.attr)())
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.BitOr)):
        left, right = _lit(node.left, env), _lit(node.right, env)
        return left + right if isinstance(node.op, ast.Add) else left | right
    raise _NotLiteral(type(node).__name__)


def _lit_bind(target: ast.AST, value: Any, env: dict[str, Any]) -> None:
    if isinstance(target, ast.Name):
        env[target.id] = value
    elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (tuple, list)) \
            and len(value) == len(target.elts):
        for t, v in zip(target.elts, value):
            _lit_bind(t, v, env)
    else:
        raise _NotLiteral("a loop target")


def gate_tables(tree: Path) -> dict[str, Any]:
    """The GATE_TABLES of a tree's gate.py that are plain literals, and from its code "tool_names" (each name it
    compares `tool` with), "shell_tools" (those _runs_shell names) and "first_keys" (each argument key classify hands
    to _first in a literal tuple). Empty when the file does not parse."""
    path = tree / "src" / "exo" / "gate.py"
    try:
        mod = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return {}
    env: dict[str, Any] = {}
    for st in mod.body:
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            name, value = st.targets[0].id, st.value
        elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name) and st.value is not None:
            name, value = st.target.id, st.value
        else:
            continue
        try:
            env[name] = _lit(value, env)
        except (_NotLiteral, TypeError, ValueError, KeyError, AttributeError, RecursionError):
            env.pop(name, None)       # a later assignment that is no literal hides the earlier one
    out: dict[str, Any] = {k: env[k] for k in GATE_TABLES if k in env}

    def strings(node: ast.AST) -> list[str]:
        vals = node.elts if isinstance(node, (ast.Tuple, ast.List, ast.Set)) else [node]
        return [v.value for v in vals if isinstance(v, ast.Constant) and isinstance(v.value, str)]

    def compared(root: ast.AST) -> list[str]:
        return list(dict.fromkeys(s for n in ast.walk(root) if isinstance(n, ast.Compare)
                                  and isinstance(n.left, ast.Name) and n.left.id == "tool"
                                  for c in n.comparators for s in strings(c)))

    out["tool_names"] = compared(mod)
    out["shell_tools"] = [s for fn in mod.body if isinstance(fn, ast.FunctionDef) and fn.name == "_runs_shell"
                          for s in compared(fn)]
    out["first_keys"] = list(dict.fromkeys(s for n in ast.walk(mod) if isinstance(n, ast.Call)
                                           and _callee(n) == "_first" and len(n.args) > 1 for s in strings(n.args[1])))
    return out


def _names(v: Any) -> list[str]:
    """The string entries of a table, sorted: a mapping's keys, or the items of a sequence or set."""
    if isinstance(v, dict):
        v = list(v)
    if not isinstance(v, (list, tuple, set, frozenset)):
        return []
    return sorted({x for x in v if isinstance(x, str) and x.strip()})


def _pairs(v: Any) -> list[tuple[str, str]]:
    """The (program, subcommand) entries of a table of pairs, sorted."""
    keys = list(v) if isinstance(v, (dict, list, tuple, set, frozenset)) else []
    return sorted({(k[0], k[1]) for k in keys if isinstance(k, tuple) and len(k) == 2
                   and all(isinstance(x, str) and x.strip() for x in k)})


_CODE = 'system("rm -rf build")'      # program text: the gate reads its string literal as a shell line


def table_lines(tables: Iterable[dict]) -> list[str]:
    """A line for each entry of the program, prefix and shell tables of each tree: each PROGRAMS key with a file
    operand, each SUBCOMMANDS pair, `rm -rf build` behind each _RUN_WRAPPERS pair, prefix and wrapper (after the
    operands _WRAPPER_OPERANDS counts, and as -c text where the wrapper takes -c) and each tmux command that runs
    one, the same line run by each shell with -c and from its input, and code that runs it given to each interpreter
    with each of its flags and on its input."""
    q = shlex.quote
    out: dict[str, None] = {}
    for t in tables:
        lines = [f"{q(p)} notes.md" for p in _names(t.get("PROGRAMS"))]
        lines += [f"{q(a)} {q(b)}" for a, b in _pairs(t.get("SUBCOMMANDS"))]
        lines += [f"{q(a)} {q(b)} rm -rf build" for a, b in _pairs(t.get("_RUN_WRAPPERS"))]
        lines += [f"{q(p)} rm -rf build" for p in _names(t.get("_PREFIXES"))]
        wrappers, operands = t.get("_WRAPPERS"), t.get("_WRAPPER_OPERANDS")
        for w in _names(wrappers):
            n = operands.get(w, 0) if isinstance(operands, dict) else 0
            lead = q(w) + " 1" * (n if isinstance(n, int) and 0 <= n <= 4 else 0)
            lines.append(f"{lead} rm -rf build")
            if "-c" in _names(wrappers.get(w) if isinstance(wrappers, dict) else ()):
                lines.append(f"{lead} -c 'rm -rf build'")
        lines += [f"tmux {q(c)} 'rm -rf build'" for c in _names(t.get("_TMUX_RUNS")) + _names(t.get("_TMUX_ALIASES"))]
        for s in _names(t.get("_SHELLS")):
            lines += [f"{q(s)} -c 'rm -rf build'", f"echo 'rm -rf build' | {q(s)}"]
        interpreters = t.get("_INTERPRETERS")
        for i in _names(interpreters):
            flags = interpreters.get(i) if isinstance(interpreters, dict) else ()
            lines += [f"{q(i)} {q(f)} {q(_CODE)}" for f in _names(flags)] + [f"echo {q(_CODE)} | {q(i)}"]
        out.update(dict.fromkeys(lines))
    return list(out)


def allowlist_lines(tables: Iterable[dict]) -> list[str]:
    """A line for each program of the allowlists (READ_PROGRAMS, SHELL_PROGRAMS) with a file operand, and each
    subcommand TOOL_SUBCOMMANDS allows, of each tree: a head that adds or moves an entry shows on its line."""
    q = shlex.quote
    out: dict[str, None] = {}
    for t in tables:
        lines = [f"{q(p)} notes.md" for p in _names(t.get("READ_PROGRAMS")) + _names(t.get("SHELL_PROGRAMS"))]
        subs = t.get("TOOL_SUBCOMMANDS")
        for p in _names(subs):
            lines += [f"{q(p)} {q(s)}" for s in _names(subs.get(p) if isinstance(subs, dict) else ())]
        out.update(dict.fromkeys(lines))
    return list(out)


# What the generated tool calls carry: a store path, a secret path, a post with a forbidden word (posting_* policies
# forbid "secret"), a target deny_target_pub denies, and the words around a NAME_HINTS word in a tool name.
STORE_PATH, SECRET_PATH = "~/.exo/state/gate-grants.json", "~/.ssh/id_rsa"
POST_TEXT, POST_TARGET = "Secret plan", "pub/notes"
HINT_AROUND = ("acme", "item")


def _hints(t: dict) -> list[tuple[list[str], list[str], str]]:
    """The NAME_HINTS entries of a tree as (words, words it needs, category)."""
    raw = t.get("NAME_HINTS")
    return [(_names(e[0]), _names(e[1]), e[2]) for e in (raw if isinstance(raw, (list, tuple)) else [])
            if isinstance(e, (list, tuple)) and len(e) == 3 and isinstance(e[2], str)]


def hint_names(t: dict) -> list[str]:
    """A tool name for each NAME_HINTS word, alone and after each word it needs, as an MCP tool and as a plugin tool,
    and an MCP tool name for each read verb: mcp_acme_trash_item, mcp_github_merge_item, acme_trash_item."""
    x, y = HINT_AROUND
    out: dict[str, None] = {}
    for hits, needs, _ in _hints(t):
        for w in hits:
            for lead in [x, *needs]:
                out.update(dict.fromkeys((f"mcp_{lead}_{w}_{y}", f"{lead}_{w}_{y}")))
    out.update(dict.fromkeys(f"mcp_{x}_{v}_{y}" for v in _names(t.get("_READ_VERBS"))))
    return list(out)


def tool_actions(tables: Iterable[dict], shell_tools: Iterable[str] = SHELL_TOOLS) -> list[dict]:
    """Calls of the tools that are not shell tools, from the tables of each tree. With no arguments: each TOOLS key,
    each name classify compares a tool with and each hint_names name. A store path and a secret path under each path
    key (_PATH_ARG_KEYS and the keys classify hands to _first) for those tools but the hint names, and one name per
    NAME_HINTS entry. For each outbound tool (a TOOLS key or NAME_HINTS entry whose category is in OUTBOUND), a post
    with a forbidden word under each text and title key and a pub/ target under each target key. For a payment tool, an
    amount under each _AMOUNT_KEYS key."""
    shell = set(shell_tools)
    x, y = HINT_AROUND
    named: dict[str, None] = {}
    hinted: dict[str, None] = {}
    outbound: dict[str, None] = {}
    payment: dict[str, None] = {}
    keys: dict[str, dict[str, None]] = {"path": {}, "text": {}, "target": {}, "amount": {}}
    for t in tables:
        tools, out_cats = t.get("TOOLS"), set(_names(t.get("OUTBOUND")))
        for name in _names(tools):
            named[name] = None
            if isinstance(tools, dict) and tools.get(name) in out_cats:
                outbound[name] = None
        named.update(dict.fromkeys(n for n in _names(t.get("tool_names")) if n not in shell))
        hinted.update(dict.fromkeys(hint_names(t)))
        for hits, needs, cat in _hints(t):
            if hits:
                rep = f"mcp_{needs[0] if needs else x}_{hits[0]}_{y}"
                named[rep] = None
                if cat in out_cats:
                    outbound[rep] = None
                if cat == "payment":
                    payment[rep] = None
        first = _names(t.get("first_keys"))
        keys["path"].update(dict.fromkeys(_names(t.get("_PATH_ARG_KEYS")) + first))
        keys["text"].update(dict.fromkeys(_names(t.get("_TEXT_KEYS")) + _names(t.get("_TITLE_KEYS")) + first))
        keys["target"].update(dict.fromkeys(_names(t.get("_TARGET_KEYS")) + first))
        keys["amount"].update(dict.fromkeys(_names(t.get("_AMOUNT_KEYS"))))
    calls = [(n, {}) for n in {**named, **hinted}]
    calls += [(n, {k: v}) for n in named for k in keys["path"] for v in (STORE_PATH, SECRET_PATH)]
    calls += [(n, {k: POST_TEXT}) for n in outbound for k in keys["text"]]
    calls += [(n, {k: POST_TARGET}) for n in outbound for k in keys["target"]]
    calls += [(n, {k: "10"}) for n in payment for k in keys["amount"]]
    return [{"tool": n, "args": a} for n, a in calls]


def simple(cmd: str) -> bool:
    """A single command with no list, pipe, subshell or substitution (a cheap textual check)."""
    return (0 < len(cmd) <= 300 and not any(ch in cmd for ch in ";&|\n`(){}")
            and not cmd.lstrip().startswith(("#", "!")))


def variants(commands: Iterable[str]) -> Iterator[str]:
    for c in commands:
        if simple(c):
            q = shlex.quote(c)
            for v in VARIANTS:
                yield v.format(c=c, q=q)


def read_corpus_file(path: Path) -> list[Any]:
    if path.suffix == ".txt":
        return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON list of commands or actions")
    return data


def normal_action(item: Any) -> dict | None:
    """A command string, or a tool action {"tool", "args"} with an optional "cwd", kept as written. A command string
    and a terminal call with nothing but a command are the same action."""
    h = Harvest()
    if isinstance(item, str):
        h.add_command(item)
    elif isinstance(item, dict) and "cwd" in item:
        cwd = item["cwd"]
        if cwd is not None and (not isinstance(cwd, str) or not cwd.strip()):
            raise ValueError(f"an action's cwd must be a folder name: {json.dumps(item)[:200]}")
        h.add_action({k: v for k, v in item.items() if k != "cwd"}, cwd)
    else:
        h.add_action(item)
    for cmd in h.commands:
        return {"tool": "terminal", "args": {"command": cmd}}
    for action in h.actions.values():
        return action
    return None


def shell_line(action: dict, tools: Collection[str] = SHELL_TOOLS, known: Collection[str] = ()) -> str | None:
    """The line of a call the gate reads as a shell line, or None. As gate._runs_shell: a tool of ``tools``, or a tool
    not in ``known`` (gate.TOOLS) whose name ends in terminal or shell when its "command" is a string. The line is
    "command", or "cmd" when that is empty."""
    tool, args = action.get("tool"), action.get("args")
    if not isinstance(tool, str) or not isinstance(args, dict):
        return None
    if not (tool in tools or (tool not in known and tool.endswith(SHELL_SUFFIXES)
                              and isinstance(args.get("command"), str))):
        return None
    line = args.get("command") or args.get("cmd")
    return line if isinstance(line, str) and line.strip() else None


def shell_form(line: str, forms: tuple[tuple[str, str], ...] = SHELL_FORMS) -> tuple[str, str]:
    """The tool name and key a line runs under besides terminal: one of ``forms``, the same in every run."""
    return forms[zlib.crc32(line.encode("utf-8", "surrogatepass")) % len(forms)]


def build_corpus(trees: Iterable[Path], files: Iterable[Path] = (), builtin: bool = True, generate: bool = True
                 ) -> tuple[list[dict], dict[str, int]]:
    """The deduplicated actions to run, and how many came from each source.

    ``trees`` is (head, base). Both are harvested, so a row the head deletes from a test is still run: a change
    cannot hide a lowered line by dropping its test. What only the base names is counted as harvested_base_only.
    The tables of both gates are read the same way (gate_tables). ``builtin`` adds the harvest, the seeds, the table
    lines, the folder rows and the tool calls. ``generate`` adds the generated forms of every line: of the --corpus
    files alone when ``builtin`` is off.
    """
    trees = list(dict.fromkeys(trees))
    out: dict[str, dict] = {}
    counts: Counter[str] = Counter()
    lines: dict[str, None] = {}     # the shell lines of the files and the harvest, in order
    tables = [gate_tables(t) for t in trees] if generate else []
    known = {n for t in tables for n in _names(t.get("TOOLS"))}
    shell_tools = tuple(dict.fromkeys([*SHELL_TOOLS, *(n for t in tables for n in _names(t.get("shell_tools")))]))
    forms = shell_forms(shell_tools)

    def put(item: Any, source: str) -> None:
        item = normal_action(item)
        if item is None:
            return
        key = json.dumps(item, sort_keys=True)
        if key not in out:
            out[key] = item
            counts[source] += 1
        line = shell_line(item, shell_tools, known)
        if line is not None and source in ("files", "harvested", "harvested_base_only"):
            lines.setdefault(line, None)

    for f in files:                 # first: --json keeps the lowered rows in corpus order, up to its cap
        for item in read_corpus_file(f):
            put(item, "files")
    if builtin:
        for k, tree in enumerate(trees):
            h = harvest(tree)
            source = "harvested" if k == 0 else "harvested_base_only"
            for cmd in h.commands:
                put(cmd, source)
            for a in h.actions.values():
                put(a, source)
    if not generate:
        return list(out.values()), dict(counts)
    seeds = list(SEED_COMMANDS) if builtin else []
    seed_lines = seeds + list(variants(seeds))
    for cmd in seed_lines:
        put(cmd, "generated")
    table = table_lines(tables) if builtin else []
    allow = allowlist_lines(tables) if builtin else []
    for cmd in table + allow:
        put(cmd, "tables")
    seed_set = set(seeds)
    wrapped = list(dict.fromkeys(c for c in [*lines, *table] if c not in seed_set))
    for cmd in variants(wrapped):
        put(cmd, "generated")
    # The gate reads the line under other tool names and keys too: a head that drops one must show. Every seed line
    # (the delete, store and credential seeds among them) runs under each form, and every other line under one, so
    # each form gets many lines while the run grows by a fraction, not by a factor of 13.
    for cmd in seed_lines:
        for tool, key in forms:
            put({"tool": tool, "args": {key: cmd}}, "shell_forms")
    for cmd in dict.fromkeys(wrapped + allow):
        tool, key = shell_form(cmd, forms)
        put({"tool": tool, "args": {key: cmd}}, "shell_forms")
    if builtin:
        for a in cwd_rows(forms):
            put(a, "cwd_forms")
        for a in tool_actions(tables, shell_tools):
            put(a, "tool_forms")
    return list(out.values()), dict(counts)


# --- one tree ------------------------------------------------------------------------------------------------------

_W: dict[str, Any] = {}


def _load_tree(tree: str, corpus: str, policies: str) -> None:
    if _W.get("tree") == tree:
        return
    src = os.path.join(tree, "src")
    sys.path.insert(0, src)
    from exo import gate  # the tree's own gate: this runs only in a child started for that tree
    here = os.path.realpath(gate.__file__)
    if not here.startswith(os.path.realpath(tree) + os.sep):
        raise SystemExit(f"gate_diff: gate loaded from {here}, not from {tree}")
    with open(corpus, encoding="utf-8") as f:
        actions = json.load(f)
    with open(policies, encoding="utf-8") as f:
        raw = json.load(f)
    pols, errors = [], {}
    for name, entry in raw.items():
        try:
            pol = gate.normalize_policies(entry["policy"])
        except Exception as exc:       # the hook falls back to the defaults on a policy error
            errors[name] = f"{type(exc).__name__}: {exc}"[:200]
            pol = gate.normalize_policies({})
        pols.append((pol, entry.get("context")))
    safe = {k for k, v in getattr(gate, "TOOLS", {}).items() if v == "read"}
    _W.update(tree=tree, gate=gate, file=here, actions=actions, pols=pols, names=list(raw), errors=errors, safe=safe)


def _on_error(tool: str, args: Any) -> str:
    """The hook's outcome when the gate raises: ask, or allow for a read tool with no secret in its arguments."""
    try:
        secret = bool(_W["gate"].args_sensitive(args if isinstance(args, dict) else {}))
    except Exception:
        secret = True
    return "allow" if tool in _W["safe"] and not secret else "ask"


def _row(i: int) -> list:
    gate, a = _W["gate"], _W["actions"][i]
    tool, args, cwd = a.get("tool", ""), a.get("args", {}), a.get("cwd")
    npol = len(_W["pols"])
    try:                            # as the hook calls it: with the session folder when there is one
        c = gate.classify({"tool": tool, "args": args}, cwd=cwd) if cwd else gate.classify({"tool": tool, "args": args})
        cat, target = str(c.category), str(c.target)
    except Exception as exc:
        why = f"classify raised {type(exc).__name__}: {exc}"[:200]
        return [None, None, why, [[_on_error(tool, args), "error", why, [], []]], [0] * npol, 0]
    uniq: dict[tuple, int] = {}
    idx, nerr = [], 0
    for pol, ctx in _W["pols"]:
        try:
            d = gate.decide(c, pol, ctx)
            key = (str(d.outcome), str(d.rule), str(d.reason)[:300],
                   tuple(sorted({(str(x[0]), str(x[1])) for x in d.reserved})), tuple(sorted(set(map(str, d.violations)))))
        except Exception as exc:
            nerr += 1
            key = (_on_error(tool, args), "error", f"decide raised {type(exc).__name__}: {exc}"[:300], (), ())
        idx.append(uniq.setdefault(key, len(uniq)))
    return [cat, target, None, [[o, r, why, [list(x) for x in res], list(v)] for o, r, why, res, v in uniq], idx, nerr]


def _chunk(span: tuple[int, int]) -> str:
    return "".join(json.dumps(_row(i)) + "\n" for i in range(*span))


def _chunks(spans: list[tuple[int, int]], jobs: int, init: tuple) -> Iterator[tuple[tuple[int, int], str]]:
    """The rows of each span, in order. With more than one job they come from a process pool that holds a few
    spans per worker at a time, so memory stays bounded. A dead worker raises BrokenProcessPool."""
    if jobs <= 1:
        for span in spans:
            yield span, _chunk(span)
        return
    import multiprocessing as mp
    ctx = mp.get_context("fork" if "fork" in mp.get_all_start_methods() else "spawn")
    with ProcessPoolExecutor(jobs, mp_context=ctx, initializer=_load_tree, initargs=init) as pool:
        todo = iter(spans)
        running = collections.deque((sp, pool.submit(_chunk, sp)) for sp in itertools.islice(todo, jobs * 4))
        while running:
            span, fut = running.popleft()
            text = fut.result()
            for sp in itertools.islice(todo, 1):
                running.append((sp, pool.submit(_chunk, sp)))
            yield span, text


def _tree_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="gate_diff.py _tree")
    for k in ("--tree", "--corpus", "--policies", "--out", "--label"):
        p.add_argument(k, required=True)
    p.add_argument("--jobs", type=int, default=1)
    a = p.parse_args(argv)
    t0 = time.monotonic()
    parent = os.getppid()
    _load_tree(a.tree, a.corpus, a.policies)
    n = len(_W["actions"])
    step = 64
    spans = [(i, min(i + step, n)) for i in range(0, n, step)]
    head = {"label": a.label, "tree": a.tree, "gate": _W["file"], "actions": n, "policies": _W["names"],
            "policy_errors": _W["errors"]}
    last = time.monotonic()
    with gzip.open(a.out, "wt", encoding="utf-8", compresslevel=1) as out:
        out.write(json.dumps({"header": head}) + "\n")
        try:
            for span, text in _chunks(spans, a.jobs if n > step else 1, (a.tree, a.corpus, a.policies)):
                if os.getppid() != parent:    # the parent was killed outright (SIGKILL): stop, nobody reads this
                    print(f"gate_diff [{a.label}]: the parent process is gone, stopping", file=sys.stderr, flush=True)
                    return 4
                out.write(text)
                if time.monotonic() - last > 15:
                    last = time.monotonic()
                    print(f"gate_diff [{a.label}] {span[1]}/{n} actions, {last - t0:.0f}s", file=sys.stderr, flush=True)
        except BrokenProcessPool as exc:      # a worker died (a crash, os._exit, the OOM killer): fail, never hang
            print(f"gate_diff [{a.label}]: a worker process died: {exc}", file=sys.stderr, flush=True)
            return 3
        out.write(json.dumps({"footer": {"secs": round(time.monotonic() - t0, 1)}}) + "\n")
    print(f"gate_diff [{a.label}] done: {n} actions in {time.monotonic() - t0:.0f}s", file=sys.stderr, flush=True)
    return 0


def work_dir() -> Path:
    """A new folder for the corpus, the results and the shared HOME, kept out of /tmp. With the HOME under /tmp,
    lines that name /tmp (rm -rf /tmp, chmod -R 777 /tmp) would touch the store in both trees, and the store's floor
    would hide a head that loosens them. Corpus lines rarely name /var/tmp."""
    tmp = os.path.realpath("/tmp")
    for base in (Path("/var/tmp"), Path.home() / ".cache"):
        real = os.path.realpath(base)
        if real == tmp or real.startswith(tmp + os.sep):
            continue
        try:
            base.mkdir(parents=True, exist_ok=True)
            return Path(tempfile.mkdtemp(prefix="gate-diff-", dir=base))
        except OSError:
            continue
    return Path(tempfile.mkdtemp(prefix="gate-diff-"))


def make_home(root: Path) -> Path:
    home = root / "home"
    for d in (".exo/state", ".ssh", ".kube", ".config/gh"):
        (home / d).mkdir(parents=True, exist_ok=True)
    return home


def tree_env(home: Path) -> dict[str, str]:
    """The whole environment of each tree's child: the same for both, with no variable the gate reads beyond HOME."""
    return {"HOME": str(home), "PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


def start_tree(label: str, tree: Path, corpus: Path, policies: Path, out: Path, jobs: int, home: Path
               ) -> subprocess.Popen:
    cmd = [sys.executable, "-I", str(Path(__file__).resolve()), "_tree", "--tree", str(tree), "--corpus", str(corpus),
           "--policies", str(policies), "--out", str(out), "--label", label, "--jobs", str(jobs)]
    # A session of its own, so a kill reaches the tree's pool workers too (stop_tree).
    return subprocess.Popen(cmd, env=tree_env(home), cwd=str(home), start_new_session=os.name == "posix")


def stop_tree(p: subprocess.Popen) -> None:
    """Kill a tree's child and its workers, unless it ended well (its workers are gone then)."""
    if p.poll() == 0:
        return
    try:
        if os.name == "posix":
            os.killpg(p.pid, signal.SIGKILL)
        else:
            p.kill()
    except OSError:
        pass
    p.wait()


def wait_trees(procs: dict[str, subprocess.Popen], timeout: float) -> tuple[dict[str, int], str]:
    """Wait for both trees. Stop the other when one fails, and both after ``timeout`` seconds (0: no limit).
    Returns the exit codes of those that ended and, when the run failed, why."""
    deadline = time.monotonic() + timeout if timeout > 0 else None
    codes: dict[str, int] = {}
    while len(codes) < len(procs) and not any(codes.values()):
        for k, p in procs.items():
            if k not in codes:
                try:
                    codes[k] = p.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    pass
        if deadline is not None and time.monotonic() > deadline and len(codes) < len(procs):
            for p in procs.values():
                stop_tree(p)
            return codes, f"a tree ran longer than --timeout {timeout:g}s and was stopped"
    for p in procs.values():
        stop_tree(p)
    if any(codes.values()):
        return codes, "a tree failed to run (" + ", ".join(
            f"{k} exit {codes[k]}" if k in codes else f"{k} stopped" for k in procs) + ")"
    return codes, ""


# --- compare -------------------------------------------------------------------------------------------------------

def _rows(path: Path) -> Iterator[Any]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)


def show_action(a: dict, width: int = 160) -> str:
    if a.get("tool") == "terminal" and set(a.get("args") or {}) == {"command"} and not a.get("cwd"):
        text = a["args"]["command"]
    else:
        text = f"{a.get('tool')} {json.dumps(a.get('args'), sort_keys=True)}"
        if a.get("cwd"):
            text += f" in {a['cwd']}"
    text = "".join(ch if ch.isprintable() else repr(ch)[1:-1] for ch in text)   # newlines, escapes, surrogates
    return text if len(text) <= width else text[:width - 3] + "..."


def _lost(db: list, dh: list) -> list[str]:
    """What the head lost at the same outcome (never called for a deny: see the module docstring). A reserved hit
    holds a target the head may name differently, so fewer hits or a lost category counts. A violation is its
    text, so any of the base's that the head lacks counts, also when the head words it otherwise."""
    kinds = []
    rb, rh = {tuple(x) for x in db[3]}, {tuple(x) for x in dh[3]}
    if rb and (len(rh) < len(rb) or not {c for c, _ in rb} <= {c for c, _ in rh}):
        kinds.append("reserved")
    if set(db[4]) - set(dh[4]):
        kinds.append("violations")
    return kinds


COUNT_KEYS = ("actions", "pairs_same", "lowered_pairs", "lowered_actions", "lowered_outcome", "lowered_reserved",
              "lowered_violations", "tightened_pairs", "tightened_actions", "pairs_detail_changed",
              "classification_only_actions", "base_classify_errors", "base_decide_errors", "head_classify_errors",
              "head_decide_errors")


def compare(base_out: Path, head_out: Path, actions: list[dict], max_examples: int = 3, json_limit: int = 5000
            ) -> dict:
    rb_iter, rh_iter = _rows(base_out), _rows(head_out)
    hb, hh = next(rb_iter)["header"], next(rh_iter)["header"]
    names = hb["policies"]
    if names != hh["policies"] or hb["actions"] != hh["actions"] or hb["actions"] != len(actions):
        raise RuntimeError("the two trees did not run the same corpus and policies")
    counts = Counter({k: 0 for k in COUNT_KEYS})
    by_policy: Counter[str] = Counter()
    by_transition: Counter[str] = Counter()
    tightened_by: Counter[str] = Counter()
    ex_policy: dict[str, list] = {}
    ex_transition: dict[str, list] = {}
    cls_examples: list = []
    lowered: list = []
    lowered_actions: list = []
    footers = {}
    for i, (rb, rh) in enumerate(zip(rb_iter, rh_iter)):
        if isinstance(rb, dict) or isinstance(rh, dict):
            footers = {"base": rb.get("footer", {}) if isinstance(rb, dict) else {},
                       "head": rh.get("footer", {}) if isinstance(rh, dict) else {}}
            if not (isinstance(rb, dict) and isinstance(rh, dict)):
                raise RuntimeError("the two result streams have different lengths")
            break
        counts["actions"] += 1
        for side, r in (("base", rb), ("head", rh)):
            counts[f"{side}_classify_errors"] += r[2] is not None
            counts[f"{side}_decide_errors"] += r[5]
        cb, tb, _, ub, pb, _ = rb
        ch, th, _, uh, ph, _ = rh
        cls_changed = (cb, tb) != (ch, th)
        lb, lh = cb or "error", ch or "error"     # a classify that raised has no category
        if ub == uh and pb == ph:
            counts["pairs_same"] += len(names)
            if cls_changed:
                counts["classification_only_actions"] += 1
                if len(cls_examples) < max_examples:
                    cls_examples.append({"action": actions[i], "base": [cb, tb], "head": [ch, th]})
            continue
        low_here = tight_here = 0
        mine: dict | None = None
        for j, name in enumerate(names):
            db, dh = ub[pb[j]], uh[ph[j]]
            if db == dh:
                counts["pairs_same"] += 1
                continue
            rkb, rkh = RANK.get(db[0], RANK["ask"]), RANK.get(dh[0], RANK["ask"])
            kinds = ["outcome"] if rkh < rkb else (_lost(db, dh) if rkh == rkb and db[0] != "deny" else [])
            if kinds:
                low_here += 1
                counts["lowered_pairs"] += 1
                for k in kinds:
                    counts[f"lowered_{k}"] += 1
                tag = f"{lb} {db[0]} -> {lh} {dh[0]}" + ("" if kinds == ["outcome"] else f" ({', '.join(kinds)} lost)")
                by_policy[name] += 1
                by_transition[tag] += 1
                item = {"action": actions[i], "policy": name, "kinds": kinds,
                        "base": {"category": cb, "target": tb, "outcome": db[0], "rule": db[1], "reason": db[2],
                                 "reserved": db[3], "violations": db[4]},
                        "head": {"category": ch, "target": th, "outcome": dh[0], "rule": dh[1], "reason": dh[2],
                                 "reserved": dh[3], "violations": dh[4]}}
                for key, store in ((name, ex_policy), (tag, ex_transition)):
                    lst = store.setdefault(key, [])
                    if len(lst) < max_examples and all(x["action"] is not item["action"] for x in lst):
                        lst.append(item)
                if len(lowered) < json_limit:
                    lowered.append(item)
                if mine is None:
                    mine = {"action": actions[i], "policies": [], "transitions": [], "first": item}
                    if len(lowered_actions) < json_limit:
                        lowered_actions.append(mine)
                mine["policies"].append(name)
                if tag not in mine["transitions"]:
                    mine["transitions"].append(tag)
            elif rkh > rkb:
                tight_here += 1
                counts["tightened_pairs"] += 1
                tightened_by[f"{lb} {db[0]} -> {lh} {dh[0]}"] += 1
            else:
                counts["pairs_detail_changed"] += 1
        counts["lowered_actions"] += low_here > 0
        counts["tightened_actions"] += tight_here > 0
        if cls_changed and not low_here and not tight_here:
            counts["classification_only_actions"] += 1
            if len(cls_examples) < max_examples:
                cls_examples.append({"action": actions[i], "base": [cb, tb], "head": [ch, th]})
    if counts["actions"] != len(actions):
        raise RuntimeError("a result stream ended early")
    return {"policies": names, "policy_errors": {"base": hb["policy_errors"], "head": hh["policy_errors"]},
            "gate_files": {"base": hb["gate"], "head": hh["gate"]}, "counts": dict(counts), "tree_secs": footers,
            "lowered_by_policy": dict(by_policy.most_common()), "lowered_by_transition": dict(by_transition.most_common()),
            "tightened_by_transition": dict(tightened_by.most_common()), "examples_by_policy": ex_policy,
            "examples_by_transition": ex_transition, "classification_only_examples": cls_examples,
            "lowered_actions": lowered_actions, "lowered": lowered,
            "lowered_truncated": counts["lowered_pairs"] > len(lowered) or counts["lowered_actions"] > len(lowered_actions)}


def _decision(d: dict) -> str:
    extra = ""
    if d["reserved"]:
        extra += f", reserved {len(d['reserved'])}"
    if d["violations"]:
        extra += f", violations {len(d['violations'])}"
    return f"{d['category'] or 'error'} {d['outcome']} ({d['rule']}{extra})"


def summary_text(rep: dict, max_examples: int = 3, groups: int = 6) -> str:
    c = rep["counts"]
    secs = rep.get("tree_secs") or {}
    out = [f"gate_diff: base {rep['base']} vs head {rep['head']}",
           f"  {c.get('actions', 0)} actions x {len(rep['policies'])} policies = "
           f"{c.get('actions', 0) * len(rep['policies'])} pairs in {rep['wall_secs']:.0f}s "
           f"(base {secs.get('base', {}).get('secs', '?')}s, head {secs.get('head', {}).get('secs', '?')}s)",
           f"  corpus: {', '.join(f'{k} {v}' for k, v in rep['corpus_counts'].items()) or 'empty'}",
           f"  lowered: {c.get('lowered_pairs', 0)} pairs on {c.get('lowered_actions', 0)} actions "
           f"(outcome {c.get('lowered_outcome', 0)}, reserved lost {c.get('lowered_reserved', 0)}, "
           f"violations lost {c.get('lowered_violations', 0)})",
           f"  tightened: {c.get('tightened_pairs', 0)} pairs on {c.get('tightened_actions', 0)} actions",
           f"  same outcome, other detail changed: {c.get('pairs_detail_changed', 0)} pairs",
           f"  classification only (category or target changed, same decisions): "
           f"{c.get('classification_only_actions', 0)} actions",
           f"  errors: base classify {c.get('base_classify_errors', 0)} decide {c.get('base_decide_errors', 0)}, "
           f"head classify {c.get('head_classify_errors', 0)} decide {c.get('head_decide_errors', 0)}"]
    for side, errs in rep["policy_errors"].items():
        for name, msg in errs.items():
            out.append(f"  policy error in {side} ({name}, defaults used): {msg}")
    if rep["lowered_by_transition"]:
        out.append("Lowered, by base -> head transition:")
        for tag, n in list(rep["lowered_by_transition"].items())[:groups]:
            out.append(f"  {n:7d}  {tag}")
            for e in rep["examples_by_transition"].get(tag, [])[:max_examples]:
                out.append(f"           [{e['policy']}] {show_action(e['action'], 120)}  "
                           f"({e['base']['rule']} -> {e['head']['rule']})")
        out.append("Lowered, by policy:")
        for name, n in list(rep["lowered_by_policy"].items())[:groups]:
            out.append(f"  {n:7d}  {name}")
            for e in rep["examples_by_policy"].get(name, [])[:max_examples]:
                out.append(f"           {show_action(e['action'])}")
                out.append(f"             base {_decision(e['base'])} -> head {_decision(e['head'])}")
    if rep["tightened_by_transition"]:
        out.append("Tightened, top transitions (counts only):")
        for tag, n in list(rep["tightened_by_transition"].items())[:groups]:
            out.append(f"  {n:7d}  {tag}")
    if rep["classification_only_examples"]:
        out.append("Classification only, examples:")
        for e in rep["classification_only_examples"][:max_examples]:
            out.append(f"           {show_action(e['action'])}  {e['base']} -> {e['head']}")
    if rep.get("lowered_truncated"):
        out.append(f"  (--json keeps the first {len(rep['lowered_actions'])} lowered actions and "
                   f"{len(rep['lowered'])} lowered pairs, in corpus order with the --corpus items first)")
    out.append("result: " + ("LOWERED (exit 1): fix each line or record it in the decision amendment"
                             if c.get("lowered_pairs") else "nothing lowered (exit 0)"))
    return "\n".join(out)


# --- main ----------------------------------------------------------------------------------------------------------

def _tree_arg(s: str) -> Path:
    p = Path(s).expanduser().resolve()
    if not (p / "src" / "exo" / "gate.py").is_file():
        raise argparse.ArgumentTypeError(f"{s}: no src/exo/gate.py (not a checkout of this repo)")
    return p


def _commit(tree: Path) -> str:
    try:
        r = subprocess.run(["git", "-C", str(tree), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                           timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Report gate decisions the head tree makes lower than the base tree.")
    p.add_argument("--base", required=True, type=_tree_arg, help="checkout to compare against (usually main)")
    p.add_argument("--head", required=True, type=_tree_arg, help="checkout with the gate change")
    p.add_argument("--corpus", nargs="+", action="extend", default=[], type=Path,
                   help="more actions: a JSON list of commands or {tool, args, cwd} actions (cwd optional), or a "
                        ".txt file, one command per line")
    p.add_argument("--only-corpus", action="store_true",
                   help="skip the built-in corpus (the harvest and the seeds): run the --corpus items and their "
                        "generated forms")
    p.add_argument("--no-variants", action="store_true",
                   help="skip every generated form (compound variants, other shell tool names and keys, session "
                        "folders)")
    p.add_argument("--policies", nargs="+", action="extend", default=[], type=Path,
                   help="more policies: a .py file with POLICIES (and CONTEXTS) or a .json mapping")
    p.add_argument("--only-policies", action="store_true", help="skip the built-in policies")
    p.add_argument("--pick", default="", help="comma-separated names of built-in policies to keep")
    p.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2),
                   help="worker processes in each tree (default: half the CPUs)")
    p.add_argument("--json", type=Path, help="write the counts and the lowered list here")
    p.add_argument("--json-limit", type=int, default=5000,
                   help="most lowered actions, and most lowered pairs, kept in --json in corpus order, the --corpus "
                        "items first (default 5000 each)")
    p.add_argument("--max-examples", type=int, default=3, help="examples per group in the summary (default 3)")
    p.add_argument("--timeout", type=float, default=3600,
                   help="seconds a tree may run before both are stopped and the run exits 2 (default 3600, 0: none)")
    p.add_argument("--home", type=Path,
                   help="HOME for both trees (default: a temp folder under /var/tmp with the usual stores)")
    p.add_argument("--dump-corpus", type=Path, help="write the corpus as JSON here, then run as usual")
    return p.parse_args(argv)


class Failed(Exception):
    """An error in this script or its input: exit 2 with this message."""


class Stopped(BaseException):
    """A SIGTERM or SIGHUP (a CI cancel, a closed terminal). It unwinds like an error, so the finally in run stops
    both trees and removes the work folder. A BaseException, so no ``except Exception`` on the way catches it."""


_STOP_SIGNALS = tuple(getattr(signal, n) for n in ("SIGTERM", "SIGHUP") if hasattr(signal, n))


def _stop(signum: int, _frame: Any) -> None:
    for sig in _STOP_SIGNALS:       # a second signal must not cut the cleanup short
        signal.signal(sig, signal.SIG_IGN)
    raise Stopped(signal.Signals(signum).name)


def _out_path(p: Path | None, flag: str) -> Path | None:
    if p is None:
        return None
    p = p.expanduser().absolute()
    if p.is_dir():
        raise Failed(f"{flag} {p}: is a folder")
    if not p.parent.is_dir():
        raise Failed(f"{flag} {p}: the folder {p.parent} does not exist")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["_tree"]:
        return _tree_main(argv[1:])
    a = parse_args(argv)
    before = {}
    try:
        for sig in _STOP_SIGNALS:
            if signal.getsignal(sig) is signal.SIG_IGN:   # nohup, or a parent that ignores it: keep ignoring it,
                continue                                  # as Python does for SIGINT
            before[sig] = signal.signal(sig, _stop)
    except ValueError:              # not the main thread: no handlers, as before
        pass
    try:
        return run(a)
    except Failed as exc:
        print(f"gate_diff: {exc}", file=sys.stderr)
    except Stopped as exc:
        try:                        # after a SIGHUP the terminal may be gone: still exit 2
            print(f"gate_diff: stopped by {exc}, both trees stopped and the work folder removed", file=sys.stderr)
        except OSError:
            pass
    except Exception as exc:        # a fault of this script is never a lowered line: exit 2, not 1
        print(f"gate_diff: internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        for sig, handler in before.items():
            signal.signal(sig, handler)
    return 2


def run(a: argparse.Namespace) -> int:
    t0 = time.monotonic()
    json_path, dump_path = _out_path(a.json, "--json"), _out_path(a.dump_corpus, "--dump-corpus")
    home = a.home.expanduser().resolve() if a.home else None
    if home is not None and not home.is_dir():
        raise Failed(f"--home {a.home}: no such folder")
    try:
        actions, corpus_counts = build_corpus((a.head, a.base), a.corpus, builtin=not a.only_corpus,
                                              generate=not a.no_variants)
    except (OSError, ValueError) as exc:
        raise Failed(f"could not build the corpus: {exc}") from None
    policies = {} if a.only_policies else {k: _entry(v) for k, v in BUILTIN_POLICIES.items()}
    if a.pick:
        keep = [x.strip() for x in a.pick.split(",") if x.strip()]
        missing = [k for k in keep if k not in policies]
        if missing:
            raise Failed(f"unknown built-in policies: {', '.join(missing)}")
        policies = {k: policies[k] for k in keep}
    for f in a.policies:
        try:
            loaded = load_policy_file(f)
        except Exception as exc:    # a .py policy file may raise anything
            raise Failed(f"could not load policies from {f}: {type(exc).__name__}: {exc}") from None
        for name, entry in loaded.items():
            policies[name if name not in policies else f"{name}@{f.name}"] = entry
    if not actions or not policies:
        raise Failed("the corpus or the policy set is empty")
    if dump_path:
        dump_path.write_text(json.dumps(actions, indent=0) + "\n", encoding="utf-8")
    print(f"gate_diff: {len(actions)} actions ({', '.join(f'{k} {v}' for k, v in corpus_counts.items())}), "
          f"{len(policies)} policies, {a.jobs} jobs per tree", file=sys.stderr, flush=True)
    work = work_dir()
    procs: dict[str, subprocess.Popen] = {}
    try:
        home = home or make_home(work)
        corpus_path, pol_path = work / "corpus.json", work / "policies.json"
        corpus_path.write_text(json.dumps(actions), encoding="utf-8")
        pol_path.write_text(json.dumps(policies), encoding="utf-8")
        outs = {"base": work / "base.jsonl.gz", "head": work / "head.jsonl.gz"}
        for k in ("base", "head"):
            procs[k] = start_tree(k, getattr(a, k), corpus_path, pol_path, outs[k], max(1, a.jobs), home)
        _, why = wait_trees(procs, a.timeout)
        if why:
            raise Failed(why)
        print("gate_diff: comparing", file=sys.stderr, flush=True)
        try:
            rep = compare(outs["base"], outs["head"], actions, a.max_examples, a.json_limit)
        except (RuntimeError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise Failed(f"could not compare the results: {exc}") from None
    finally:
        for p in procs.values():     # an interrupt or an error here: no tree keeps running
            stop_tree(p)
        shutil.rmtree(work, ignore_errors=True)
    rep.update(base=f"{a.base} {_commit(a.base)}".strip(), head=f"{a.head} {_commit(a.head)}".strip(),
               corpus_counts=corpus_counts, wall_secs=round(time.monotonic() - t0, 1))
    if json_path:                   # before the summary, so a failed write never follows a printed result
        full = {k: v for k, v in rep.items() if k not in ("examples_by_policy", "examples_by_transition")}
        try:
            json_path.write_text(json.dumps(full, indent=1) + "\n", encoding="utf-8")
        except OSError as exc:
            raise Failed(f"could not write --json {json_path}: {exc}") from None
    print(summary_text(rep, a.max_examples))
    return 1 if rep["counts"].get("lowered_pairs") else 0


if __name__ == "__main__":
    sys.exit(main())
