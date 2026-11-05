"""Validated orchestration schema for Swarm groups (plan D05 + D06 boundary).

Owns ONE decision: given an untrusted, model-supplied plan and the host's explicit authority grants,
is this plan well-formed and admissible? Yes/no, plus an immutable topologically ordered plan.
Builds, schedules, persists, routes and approves nothing. Produces a value; a value executes nothing.
Makes no file, network, database or clock calls. Importing it still loads other exo modules through
``RouteConfig`` (delegate, sessions, models); they are loaded, not called. Not an isolation guarantee.

Non-claims, so no caller can infer more than is true:
* Path scopes are a LEXICAL contract, not filesystem enforcement and not a sandbox. No ``realpath``,
  no ``stat``, no symlink/hardlink/mount reasoning, no existence check. TOCTOU, an in-root symlink
  pointing outside its root, and bind mounts are OUT OF SCOPE. The runtime that actually writes MUST
  reauthorize the profile and re-resolve (realpath) the path at write time; a ``ValidatedPlan`` never
  itself authorizes a write. ``inputs`` of kind ``"path"`` are recorded references only, not read
  grants: this slice has no read-authority model and the runtime governs reads.
* Unknown STRUCTURAL keys are refused; there is NO secret-content detection. ``goal``/``acceptance``
  text is not inspected for secret-looking strings and this module must not be described as detecting
  them. The credential boundary (D10) is enforced by the runtime and the environment, not here.
* ``plan_id`` is IDENTITY, not authorization proof (anyone can compute one; authority is re-checked at
  run time) and NOT a persisted checkpoint. Nothing here writes or reserves state.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from exo.core.agents.route_config import RouteConfig, RouteConfigError

if TYPE_CHECKING:  # annotations only: the validator reads group.id/owner/mode/members and nothing else
    from exo.core.groups import SavedGroup


class ContractError(ValueError):
    """A plan that must not be handed to any runtime. Message form: "<rule> <task_id|plan>: <reason>"."""


SCHEMA_VERSION = "swarm.task-plan.v1"
REVIEWER_ROLES = frozenset({"reviewer", "verifier", "critic"})  # roles are data, never grants
REFERENCE_KINDS = frozenset({"message", "artifact", "path", "task_output"})
GROUP_MODES = frozenset({"socratic", "conductor"})  # matches SavedGroup.mode literals
# Capabilities that transfer authority. Refused unconditionally: a grant cannot enable them (I11a).
ELEVATION_CAPABILITIES = frozenset({
    "approve", "approval", "grant", "grant_capability", "escalate", "escalation",
    "spawn", "spawn_group", "switch_profile", "set_owner", "write_any", "bypass_scope",
    "credential", "credential_use", "network", "net", "install", "commit", "merge",
})
TASK_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
ROLE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
ARTIFACT_NAME_RE = TASK_ID_RE
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.@-]{0,63}$")  # owner / profile names
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_GOAL_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # a goal may contain newlines and tabs
PLAN_KEYS = frozenset({"schema", "tasks", "review_of"})
TASK_KEYS_REQUIRED = frozenset({"task_id", "role", "goal", "profile", "acceptance"})
TASK_KEYS_OPTIONAL = frozenset({"depends_on", "inputs", "outputs", "write_scopes", "permits", "route"})
REFERENCE_KEYS = frozenset({"kind", "ref"})


def _fail(rule: str, target: str, reason: str) -> None:
    raise ContractError(f"{rule} {target}: {reason}")


def _walk(value: Any, rule: str, target: str, depth: int = 0) -> None:
    """Fail closed on anything that is not untrusted plain data (I2): no NaN/Inf, objects or callables."""
    if depth > 8:
        _fail(rule, target, "plan nests too deeply")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or _CTRL.search(key):
                _fail(rule, target, f"mapping key {key!r} must be a string without control characters")
            _walk(item, rule, target, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _walk(item, rule, target, depth + 1)
    elif isinstance(value, str):
        if "\x00" in value:
            _fail(rule, target, "NUL in string value")
    else:
        _fail(rule, target, f"unsupported value type {type(value).__name__}: strings, mappings and lists only")


def _seq(value: Any, rule: str, target: str, name: str) -> tuple:
    if not isinstance(value, (list, tuple)):
        _fail(rule, target, f"{name} must be a list")
    return tuple(value)


def _text(value: Any, rule: str, target: str, name: str,
          pattern: re.Pattern[str] | None = None, max_len: int | None = None) -> str:
    if not isinstance(value, str) or not value:
        _fail(rule, target, f"{name} must be a non-empty string")
    if _CTRL.search(value):
        _fail(rule, target, f"control character in {name}")
    if name.endswith("entry") and value != value.strip():
        _fail(rule, target, f"{name} must be trimmed")
    if pattern is not None and not pattern.match(value):
        _fail(rule, target, f"{name} {value!r} has an unacceptable form")
    if max_len is not None and len(value) > max_len:
        _fail(rule, target, f"{name} exceeds {max_len} characters")
    return value


def _canonical_abs(value: Any, rule: str, target: str, what: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(rule, target, f"{what} must be a non-empty absolute POSIX path")
    if not value.startswith("/"):
        _fail(rule, target, f"{what} {value!r} is not absolute")
    if "\\" in value or _CTRL.search(value):
        _fail(rule, target, f"{what} {value!r} contains a backslash or control character")
    if "//" in value:
        _fail(rule, target, f"{what} {value!r} contains '//'")
    parts = value.split("/")[1:]
    if ".." in parts or "." in parts:
        _fail(rule, target, f"{what} {value!r} contains a '.' or '..' component")
    if value.endswith("/") and value != "/":
        _fail(rule, target, f"{what} {value!r} has a trailing '/'")
    if posixpath.normpath(value) != value:
        _fail(rule, target, f"{what} {value!r} is not canonical")
    return value


def _within(child: str, root: str) -> bool:
    """Component-boundary prefix containment: '/a/bc' is NOT within '/a/b'. Lexical only, no I/O."""
    return child == root or root == "/" or child.startswith(root + "/")


@dataclass(frozen=True)
class Reference:
    kind: str  # one of REFERENCE_KINDS; the target type is fixed per kind (task id / artifact / path / id)
    ref: str   # never empty, never control characters, never ".."


@dataclass(frozen=True)
class HostGrants:
    """Host-side authority: the ONLY source of authority a plan meets. Built from the host's explicit
    mapping, never from a model plan; a plan can neither construct, extend nor mutate one."""

    owner: str
    profiles: frozenset[str]
    permits: Mapping[str, frozenset[str]]
    write_roots: Mapping[str, tuple[str, ...]]
    routes: RouteConfig
    reviewer_profiles: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        rule = "I-grants"
        _text(self.owner, rule, "grants", "owner", ID_RE)
        profiles = _id_set(self.profiles, rule, "grants", "profiles")
        permits = _freeze_permits(self.permits, profiles, rule)
        roots = _freeze_roots(self.write_roots, profiles, rule)
        reviewers = _id_set(self.reviewer_profiles, rule, "grants", "reviewer_profiles")
        if not reviewers <= profiles:
            _fail(rule, "grants", "reviewer_profiles must be a subset of profiles")
        if not isinstance(self.routes, RouteConfig):
            _fail(rule, "grants", f"routes must be a RouteConfig, got {type(self.routes).__name__}")
        object.__setattr__(self, "profiles", profiles)
        object.__setattr__(self, "permits", permits)
        object.__setattr__(self, "write_roots", roots)
        object.__setattr__(self, "reviewer_profiles", reviewers)


def _set_of(value: Any, rule: str, target: str, name: str) -> frozenset:
    if isinstance(value, (str, bytes)) or not isinstance(value, (frozenset, set, tuple, list)):
        _fail(rule, target, f"{name} must be a set, not {type(value).__name__}")
    return frozenset(value)


def _id_set(value: Any, rule: str, target: str, name: str) -> frozenset[str]:
    return frozenset(_text(v, rule, target, f"{name} entry", ID_RE)
                     for v in _set_of(value, rule, target, name))


def _freeze_permits(value: Any, profiles: frozenset[str], rule: str) -> Mapping[str, frozenset[str]]:
    if not isinstance(value, Mapping):
        _fail(rule, "grants", "permits must be a mapping of profile -> capabilities")
    frozen: dict[str, frozenset[str]] = {}
    for profile, caps in value.items():
        _text(profile, rule, "grants", "permits profile", ID_RE)
        if profile not in profiles:
            _fail(rule, "grants", f"permits names unauthorized profile {profile!r}")
        frozen[profile] = frozenset(_text(c, rule, "grants", "capability", CAPABILITY_RE)
                                    for c in _set_of(caps, rule, "grants", f"permits[{profile!r}]"))
    return MappingProxyType(frozen)


def _freeze_roots(value: Any, profiles: frozenset[str], rule: str) -> Mapping[str, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        _fail(rule, "grants", "write_roots must be a mapping of profile -> roots")
    frozen: dict[str, tuple[str, ...]] = {}
    for profile, roots in value.items():
        _text(profile, rule, "grants", "write_roots profile", ID_RE)
        if profile not in profiles:
            _fail(rule, "grants", f"write_roots names unauthorized profile {profile!r}")
        checked = []
        for root in _set_of(roots, rule, "grants", f"write_roots[{profile!r}]"):
            _canonical_abs(root, "I12", "grants", f"write root of {profile!r}")
            if root == "/":
                _fail("I12", "grants", f"profile {profile!r} claims a whole-filesystem write root")
            checked.append(root)
        frozen[profile] = tuple(checked)
    return MappingProxyType(frozen)


@dataclass(frozen=True)
class TaskContract:
    """One immutable task. Every field is declared by the plan; none is inferred from `role`."""

    task_id: str
    role: str
    goal: str
    profile: str  # REQUIRED and explicit: never derived from role (D03 needs the profile)
    acceptance: tuple[str, ...]  # non-empty
    depends_on: tuple[str, ...] = ()
    inputs: tuple[Reference, ...] = ()
    outputs: tuple[Reference, ...] = ()
    route: str = ""  # "" = RouteConfig default/parent model; else a configured route name
    write_scopes: tuple[str, ...] = ()  # absolute canonical POSIX; empty = read-only task
    permits: tuple[str, ...] = ()  # capabilities the task ASKS for; only HostGrants can satisfy


@dataclass(frozen=True)
class ValidatedPlan:
    """The only thing a runtime is ever handed. Immutable, ordered, self-describing, inert."""

    plan_id: str  # sha256 hex over canonical JSON of the validated data; no clock, no uuid
    schema: str
    group_id: str  # from group.id, never from raw
    owner: str  # from group.owner, never from raw
    mode: str  # from group.mode, carried and never interpreted here (I17)
    tasks: tuple[TaskContract, ...]  # topological order; ties broken by task_id
    review_of: Mapping[str, str]  # builder task_id -> reviewer task_id, immutable copy

    def __post_init__(self) -> None:
        object.__setattr__(self, "review_of", MappingProxyType(dict(self.review_of)))

    def task(self, task_id: str) -> TaskContract:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(task_id)

    def to_dict(self) -> dict:
        """Pure; the single shared shape for CLI, desktop and tests. Serializing it does nothing else."""
        return {"plan_id": self.plan_id, "schema": self.schema, "group_id": self.group_id,
                "owner": self.owner, "mode": self.mode, "review_of": dict(self.review_of),
                "tasks": [_task_dict(task) for task in self.tasks]}


def _task_dict(task: TaskContract) -> dict:
    return {
        "task_id": task.task_id, "role": task.role, "goal": task.goal, "profile": task.profile,
        "route": task.route, "write_scopes": list(task.write_scopes), "permits": list(task.permits),
        "acceptance": list(task.acceptance), "depends_on": list(task.depends_on),
        "inputs": [{"kind": r.kind, "ref": r.ref} for r in task.inputs],
        "outputs": [{"kind": r.kind, "ref": r.ref} for r in task.outputs],
    }


def _reference(raw: Any, rule: str, target: str, name: str) -> Reference:
    if not isinstance(raw, Mapping):
        _fail(rule, target, f"{name} must be a reference mapping")
    if set(raw) != REFERENCE_KEYS:
        _fail(rule, target, f"{name} keys must be exactly kind/ref, got {sorted(map(str, set(raw)))}")
    kind, ref = raw["kind"], raw["ref"]
    if not isinstance(kind, str) or kind not in REFERENCE_KINDS:
        _fail(rule, target, f"{name} kind {kind!r} is not one of {sorted(REFERENCE_KINDS)}")
    if not isinstance(ref, str) or not ref or _CTRL.search(ref):
        _fail(rule, target, f"{name} ref must be a non-empty string without control characters")
    if ".." in ref:
        _fail(rule, target, f"{name} ref {ref!r} contains '..'")
    if kind == "task_output" and not TASK_ID_RE.match(ref):
        _fail("I7", target, f"{name} task_output ref {ref!r} is not a task id")
    if kind == "artifact" and not ARTIFACT_NAME_RE.match(ref):
        _fail("I8", target, f"{name} artifact ref {ref!r} is not a valid artifact name")
    if kind == "path":
        _canonical_abs(ref, "I8", target, f"{name} path ref")
    if kind == "message" and (len(ref) > 128 or any(ch.isspace() for ch in ref)):
        _fail("I8", target, f"{name} message ref must be 1..128 characters with no whitespace")
    return Reference(kind, ref)


def _read_only(target: str, role: str, scopes: tuple[str, ...], outputs: tuple[Reference, ...]) -> None:
    if scopes:
        _fail("I15", target, f"reviewer role {role!r} must be read-only: write_scopes must be empty")
    for output in outputs:
        if output.kind not in ("message", "artifact"):
            _fail("I15", target, f"reviewer output kind {output.kind!r} is not allowed; message/artifact only")


def _parse_task(index: int, raw: Any, grants: HostGrants, members: frozenset[str]) -> TaskContract:
    if not isinstance(raw, Mapping):
        _fail("I3", f"tasks[{index}]", "a task must be a mapping")
    raw_id = raw.get("task_id")
    target = raw_id if isinstance(raw_id, str) and TASK_ID_RE.match(raw_id) else f"tasks[{index}]"
    unknown = set(raw) - (TASK_KEYS_REQUIRED | TASK_KEYS_OPTIONAL)
    if unknown:
        _fail("I1", target, f"unknown task key(s): {sorted(map(str, unknown))}")
    missing = TASK_KEYS_REQUIRED - set(raw)
    if missing:
        _fail("I2", target, f"missing required key(s): {sorted(missing)}")
    task_id = _text(raw["task_id"], "I3", target, "task_id", TASK_ID_RE)
    role = _text(raw["role"], "I1", target, "role", ROLE_RE)
    goal = _text(raw["goal"], "I1", target, "goal", max_len=4000)
    if _GOAL_BAD.search(goal):
        _fail("I1", target, "control character in goal")
    profile = _text(raw["profile"], "I10", target, "profile", ID_RE)
    acceptance = tuple(_text(item, "I9", target, "acceptance entry", max_len=500)
                       for item in _seq(raw["acceptance"], "I9", target, "acceptance"))
    if not acceptance:
        _fail("I9", target, "acceptance must be non-empty")
    if profile not in grants.profiles:
        _fail("I10", target, f"profile {profile!r} is not authorized by the host grants")
    if profile not in members:
        _fail("I10", target, f"profile {profile!r} is not a member of this group")
    depends_on = tuple(_text(item, "I4", target, "depends_on entry", TASK_ID_RE)
                       for item in _seq(raw.get("depends_on", ()), "I4", target, "depends_on"))
    if len(set(depends_on)) != len(depends_on):
        _fail("I4", target, "duplicate depends_on entry")
    if task_id in depends_on:
        _fail("I4", target, "task depends on itself")
    inputs = tuple(_reference(r, "I8", target, "input")
                   for r in _seq(raw.get("inputs", ()), "I8", target, "inputs"))
    outputs = tuple(_reference(r, "I8", target, "output")
                    for r in _seq(raw.get("outputs", ()), "I8", target, "outputs"))
    route = raw.get("route", "")
    if route != "":
        route = _text(route, "I1", target, "route")
        if route not in grants.routes.names():
            _fail("I1", target, f"route {route!r} is not configured; have {list(grants.routes.names())}")
        try:
            grants.routes.resolve(route)
        except RouteConfigError as exc:
            _fail("I1", target, f"route {route!r} does not resolve: {exc}")
    scopes = tuple(_canonical_abs(s, "I12", target, "write scope")
                   for s in _seq(raw.get("write_scopes", ()), "I12", target, "write_scopes"))
    roots = grants.write_roots.get(profile, ())
    for scope in scopes:
        if not any(_within(scope, root) for root in roots):
            _fail("I12", target, f"write scope {scope!r} is outside every granted root of {profile!r}")
    permits = tuple(_text(p, "I11", target, "permit", CAPABILITY_RE)
                    for p in _seq(raw.get("permits", ()), "I11", target, "permits"))
    if len(set(permits)) != len(permits):
        _fail("I11", target, "duplicate permit")
    granted = grants.permits.get(profile, frozenset())
    for permit in permits:
        if permit in ELEVATION_CAPABILITIES:
            _fail("I11a", target, f"capability {permit!r} transfers authority; a plan may never request it")
        if permit not in granted:
            _fail("I11", target, f"capability {permit!r} is not permitted to profile {profile!r}")
    for output in outputs:
        if output.kind == "path" and not any(_within(output.ref, scope) for scope in scopes):
            _fail("I8", target, f"output path {output.ref!r} is outside the task's write scopes")
    if role in REVIEWER_ROLES:
        _read_only(task_id, role, scopes, outputs)
    return TaskContract(task_id, role, goal, profile, acceptance, depends_on, inputs, outputs,
                        route, scopes, permits)


def _topo_order(by_id: dict[str, TaskContract]) -> tuple[TaskContract, ...]:
    """Kahn with the ready set sorted by task_id (I6): deterministic, independent roots interleaved."""
    remaining = {tid: set(task.depends_on) for tid, task in by_id.items()}
    ordered: list[TaskContract] = []
    while remaining:
        ready = sorted(tid for tid, deps in remaining.items() if not deps)
        if not ready:  # I5: name one cycle member plus the rule id; nothing about reachability
            _fail("I5", min(remaining), f"dependency cycle among {', '.join(sorted(remaining))}")
        ordered.extend(by_id[tid] for tid in ready)
        for tid in ready:
            del remaining[tid]
        for deps in remaining.values():
            deps.difference_update(ready)
    return tuple(ordered)


def _check_references(by_id: dict[str, TaskContract]) -> None:
    artifacts: dict[str, str] = {}
    for task in by_id.values():
        for output in task.outputs:
            if output.kind == "artifact":
                if output.ref in artifacts:
                    _fail("I8", task.task_id, f"artifact {output.ref!r} is declared by two tasks")
                artifacts[output.ref] = task.task_id
    for task in by_id.values():
        for ref in task.inputs:
            if ref.kind == "task_output" and ref.ref not in task.depends_on:
                _fail("I7", task.task_id, f"input {ref.ref!r} is not a dependency of this task")
            if ref.kind == "artifact":
                if ref.ref not in artifacts:
                    _fail("I7", task.task_id, f"input artifact {ref.ref!r} is not declared by any task")
                produced = [d for d in task.depends_on
                            if any(o.kind == "artifact" and o.ref == ref.ref for o in by_id[d].outputs)]
                if not produced:
                    _fail("I7", task.task_id, f"input artifact {ref.ref!r} has no producer in depends_on")


def _check_review(raw: Any, by_id: dict[str, TaskContract], grants: HostGrants) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        _fail("I14", "plan", "review_of must be a mapping of builder -> reviewer")
    review: dict[str, str] = {}
    for builder, reviewer in raw.items():
        builder, reviewer = str(builder), str(reviewer)
        if builder not in by_id or reviewer not in by_id:
            _fail("I14", "plan", f"review pair {builder!r} -> {reviewer!r} names an undeclared task")
        if builder == reviewer:
            _fail("I14", builder, "a task cannot review itself")
        task = by_id[reviewer]
        if task.role not in REVIEWER_ROLES:
            _fail("I14", reviewer, f"role {task.role!r} is not a reviewer role")
        if task.profile not in grants.reviewer_profiles:
            _fail("I14", reviewer, f"profile {task.profile!r} is not an authorized reviewer")
        if task.profile == by_id[builder].profile:
            _fail("I14", reviewer, f"self-review: reviewer and builder share profile {task.profile!r}")
        if builder not in task.depends_on:
            _fail("I14", reviewer, f"reviewer must depend_on the reviewed task {builder!r}")
        if reviewer in review.values():
            _fail("I14", reviewer, "a reviewer may review at most one task")
        _read_only(reviewer, task.role, task.write_scopes, task.outputs)
        review[builder] = reviewer
    return dict(sorted(review.items()))


def _group_facts(group: Any) -> tuple[str, str, str, frozenset[str]]:
    group_id, owner, mode = getattr(group, "id", None), getattr(group, "owner", None), getattr(group, "mode", None)
    members = getattr(group, "members", None)
    if not isinstance(group_id, str) or not group_id:
        _fail("I1", "plan", "group.id must be a non-empty string")
    if not isinstance(owner, str) or not owner:
        _fail("I1", "plan", "group.owner must be a non-empty string")
    if not isinstance(mode, str) or mode not in GROUP_MODES:
        _fail("I17", "plan", f"group mode {mode!r} is not one of {sorted(GROUP_MODES)}")
    if not isinstance(members, (tuple, list)):
        _fail("I10", "plan", "group.members must be a sequence")
    profiles = set()
    for member in members:
        profile = getattr(member, "profile", None)
        if not isinstance(profile, str) or not profile:
            _fail("I10", "plan", "group.members entries must carry a profile name")
        profiles.add(profile)
    return group_id, owner, mode, frozenset(profiles)


def validate_plan(raw: Mapping[str, Any], grants: HostGrants, *, group: SavedGroup) -> ValidatedPlan:
    """Validate an untrusted plan against explicit host authority (I1-I18). Refuses, never repairs.

    ``group`` supplies identity (id/owner/mode) and membership; nothing in ``raw`` can override them.
    """
    if not isinstance(grants, HostGrants):
        _fail("I-grants", "plan", "grants must be a HostGrants built by the host")
    if not isinstance(raw, Mapping):
        _fail("I1", "plan", "plan must be a mapping")
    _walk(raw, "I2", "plan")
    unknown = set(raw) - PLAN_KEYS
    if unknown:
        _fail("I1", "plan", f"unknown plan key(s): {sorted(map(str, unknown))}")
    group_id, owner, mode, members = _group_facts(group)
    if grants.owner != owner:  # I10a: identity strings compared for equality, nothing more
        _fail("I10a", "plan", f"grants owner {grants.owner!r} != group owner {owner!r}")
    for key in ("schema", "tasks"):
        if key not in raw:
            _fail("I2", "plan", f"missing required plan key {key!r}")
    if raw["schema"] != SCHEMA_VERSION:
        _fail("I1", "plan", f"schema must be {SCHEMA_VERSION!r}, got {raw['schema']!r}")
    if not members <= grants.profiles:
        _fail("I10", "plan", "group membership is not covered by the host grants")
    raw_tasks = _seq(raw["tasks"], "I3", "plan", "tasks")
    if not raw_tasks:
        _fail("I3", "plan", "tasks must be non-empty")
    by_id: dict[str, TaskContract] = {}
    for index, item in enumerate(raw_tasks):
        task = _parse_task(index, item, grants, members)
        if task.task_id in by_id:
            _fail("I3", task.task_id, "duplicate task_id")
        by_id[task.task_id] = task
    for task in by_id.values():
        for dep in task.depends_on:
            if dep not in by_id:
                _fail("I4", task.task_id, f"depends_on names undeclared task {dep!r}")
        for ref in task.inputs:
            if ref.kind == "task_output" and ref.ref not in by_id:
                _fail("I7", task.task_id, f"input names undeclared task {ref.ref!r}")
    _check_references(by_id)
    review = _check_review(raw.get("review_of", {}), by_id, grants)
    ordered = _topo_order(by_id)
    payload = {"schema": SCHEMA_VERSION, "group_id": group_id, "owner": owner, "mode": mode,
               "review_of": review, "tasks": [_task_dict(task) for task in ordered]}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return ValidatedPlan(hashlib.sha256(blob.encode("ascii")).hexdigest(), SCHEMA_VERSION,
                         group_id, owner, mode, ordered, review)
