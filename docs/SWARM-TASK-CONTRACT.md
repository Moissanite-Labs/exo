# Swarm Task Contract: Schema Documentation

This module is a checker for task plans. It takes a plan written by a model and the permissions the
host allows, and says whether the plan is acceptable. This document says what the module decides and
what it does **not** do. It covers the checker only. No runtime is claimed.

- Module: `src/exo/core/group_task_contract.py` (496 lines)
- Tests: `tests/test_group_task_contract.py` (418 lines, 25 test functions)
- Public surface: none (no symbols exported from any package `__init__`)

## 1. What this module is

`validate_plan(raw, grants, *, group)` decides one thing. Given an untrusted, model-supplied
plan and the host's explicit authority grants, is the plan well-formed and admissible?
It returns an immutable, topologically ordered `ValidatedPlan`, or raises `ContractError`
(message form `"<rule> <task_id|plan>: <reason>"`).

It **builds, schedules, persists, routes and approves nothing**. It produces a value.
A value executes nothing. There is no code path from `validate_plan` to execution. The
module has no runtime call sites anywhere in the tree (grep for `group_task_contract`,
`validate_plan` and `ValidatedPlan` outside the module, its test and this document returns zero rows).
Nothing connects it to `GroupHost`. As written, `GroupHost.start` takes no `plan` or `grants`
parameter, and this module does not change that. This layer is a standalone checker. A caller would
have to add the wiring, and nothing here supplies it.

The module makes no file, network, database or clock calls. Importing it still loads other `exo`
modules, because it imports `RouteConfig` from `exo.core.agents.route_config`. That import also
loads the agents, session and model modules. In a test import, `sqlite3` and `httpx` ended up
loaded. Those modules are loaded but not called by this module. Do not read this as "nothing else
is imported" or as an isolation guarantee.

## 2. Exact schema and a minimal valid example

Top-level plan keys are exactly a subset of `{"schema", "tasks", "review_of"}`. A task has
five required keys (`task_id`, `role`, `goal`, `profile`, `acceptance`) and six optional
(`depends_on`, `inputs`, `outputs`, `write_scopes`, `permits`, `route`). References are
exactly `{"kind", "ref"}`. Nothing else is accepted. Unknown structural keys are refused,
and `group_id`, `owner`, `mode`, `members`, `plan_id`, `grants` are unknown keys in `raw`
(identity comes from the `group` argument, never from the plan).

```python
from exo.core.agents.route_config import RouteConfig
from exo.core.group_task_contract import HostGrants, validate_plan
from exo.core.groups import SavedGroup, GroupMember

grants = HostGrants(
    owner="carol",
    profiles=frozenset({"alice", "bob"}),
    permits={"alice": frozenset({"read_repo"}), "bob": frozenset()},
    write_roots={"alice": ("/srv/w",), "bob": ()},
    routes=RouteConfig(),                                   # "" route = config default
    reviewer_profiles=frozenset({"bob"}),
)

group = SavedGroup(
    id="g_1", owner="carol", mode="socratic", conductor=None,
    members=(GroupMember(profile="alice", session="s1"),
             GroupMember(profile="bob", session="s2")),
    created="2026-11-06T00:00:00Z",
)

raw = {
    "schema": "swarm.task-plan.v1",
    "review_of": {"build_report": "check_report"},
    "tasks": [
        {
            "task_id": "build_report",
            "role": "worker",
            "goal": "Write the quarterly report.",
            "profile": "alice",
            "acceptance": ["report file exists", "contains Q3 figures"],
            "depends_on": [],
            "inputs": [],
            "outputs": [{"kind": "path", "ref": "/srv/w/report.md"}],
            "write_scopes": ["/srv/w"],
            "permits": ["read_repo"],
            "route": "",
        },
        {
            "task_id": "check_report",
            "role": "reviewer",
            "goal": "Verify the report against the Q3 ledger.",
            "profile": "bob",
            "acceptance": ["review note recorded"],
            "depends_on": ["build_report"],
            "inputs": [{"kind": "message", "ref": "msg_42"}],
            "outputs": [{"kind": "message", "ref": "review_note_7"}],
            "write_scopes": [],          # reviewers are read-only
            "permits": [],
            "route": "",
        },
    ],
}

plan = validate_plan(raw, grants, group=group)
# plan.plan_id -> 64-hex sha256; plan.mode == "socratic"; plan.tasks -> topological order
```

API notes, checked against the module:

- `HostGrants(owner, profiles, permits, write_roots, routes, reviewer_profiles=frozenset())`.
- `validate_plan(raw, grants, *, group)` requires a real `HostGrants` (a plain dict is refused).
  It reads the `SavedGroup` fields `id`, `owner`, `mode` and `members`.
- `ValidatedPlan` exposes `plan_id`, `schema`, `group_id`, `owner`, `mode`, `tasks`, `review_of`,
  `task(id)` (`KeyError` if absent) and `to_dict()`.
- All containers are immutable (`frozenset`, `tuple`, `MappingProxyType`).

## 3. What the schema decides (invariants, summarized)

- **Shape and order.** `tasks` is a non-empty list. Each `task_id` is unique and matches
  `[A-Za-z][A-Za-z0-9_.-]{0,63}`. `depends_on` entries are declared ids, with no self or duplicate
  dependencies. Cycles are refused (I5). Independent roots (several tasks with empty
  `depends_on`) are valid. Order is a deterministic Kahn topological sort with the ready set
  sorted by `task_id`, so the same input always yields byte-identical output (I6).
- **Explicit references and acceptance.** Every input/output is a `Reference` with a kind:
  `task_output` (must be a dependency), `artifact` (must be produced by a dependency, unique
  plan-wide), `path` (used for outputs: must be lexically inside the task's write scopes), or
  `message` (an opaque pointer to an existing message id). The validator has no I/O and cannot
  verify that the message exists. The runtime resolves it. `acceptance` is a non-empty list of
  trimmed non-empty strings of at most 500 characters (I9).
- **Roles are data, not grants.** `role` never implies `profile`. A task's `profile` is
  required and explicit, and must be in `grants.profiles` and in the group's membership (I10).
  `role="reviewer"` grants nothing by itself.
- **Authority stays with the host.** `grants.owner` must equal `group.owner` (I10a).
  `permits` are requests, not grants. Each must be present in `grants.permits.get(profile)`.
  A missing profile key means an empty set, so every request fails closed (I11). Write scopes
  must be inside the profile's granted roots (I12).
- **Elevation names are a blocklist.** `ELEVATION_CAPABILITIES` is a fixed set of exact strings:
  `approve`, `approval`, `grant`, `grant_capability`, `escalate`, `escalation`, `spawn`,
  `spawn_group`, `switch_profile`, `set_owner`, `write_any`, `bypass_scope`, `credential`,
  `credential_use`, `network`, `net`, `install`, `commit` and `merge`. A plan that requests one of
  these names is refused even if the host grant lists it (I11a). The check is an exact string
  match. It does not decode meaning, so a differently spelled capability (for example `net.fetch`)
  is not caught by the blocklist. It passes only if the host grants it. The host allowlist in
  `grants.permits` is the real authority. The blocklist is an extra guard on top of it and does not
  replace it.
- **Routes.** `route` is either empty or the name of a route that the host configured in
  `grants.routes`, which is a `RouteConfig`. An empty value means the `RouteConfig` default, which
  is the delegation tiers or the parent's own model. A non-empty name is refused when it is not
  configured. Under the default backend there are no named routes, so any non-empty name is refused.
  `RouteConfig` reserves the tier names (`fast`, `standard`, `deep`) and the role names (`main`,
  `aux`, `delegate`, `decision`, `eval`), so none of these can be a route name here. A direct
  `provider:model` ref is also refused, because it is not a configured name. This module checks
  only that the name is configured and resolves. It does not limit which task may use which route,
  so any configured route is usable by any task. It does not check the resolved model against the
  model catalog or tier limits. The `RouteConfig` documentation says `Delegator` does that check
  (see `docs/SWARM-DELEGATION.md`). This module never calls `Delegator`.
- **Reviewers: distinct identity and dependency.** For each `(builder, reviewer)` in
  `review_of`:
  - both tasks are declared and different.
  - the reviewer's role is a reviewer role.
  - the reviewer's profile is in `grants.reviewer_profiles` and differs from the builder's profile.
  - the reviewer must `depends_on` the reviewed builder.
  - a reviewer reviews at most one task.
  - mutual review is a cycle and is refused (I5/I14).

  Reviewers are read-only. They have empty `write_scopes`, and outputs only of kind
  `message` or `artifact`, never `path` (I15).
- **Mode is carried, not interpreted.** `mode` is copied from `group.mode` and must be one of
  `"socratic"` or `"conductor"` (I17). The schema never reads audiences, never routes messages,
  and never decides who speaks. The socratic-vs-conductor distinction lives in `SavedGroup`
  (the `conductor` field). This module does not consume it.

## 4. Lexical scopes, not a sandbox

Paths are an **absolute canonical POSIX lexical contract**: no `\`, NUL or control chars, no
`//`, no `.`/`..` components, no trailing `/` (except `/`), `normpath`-stable. Containment is
component-boundary (`/a/bc` is NOT within `/a/b`). A write root of exactly `/` is refused.

This is **not** filesystem enforcement and **not** a sandbox: no `realpath`, no `stat`, no
symlink/hardlink/mount reasoning, no existence check. TOCTOU, an in-root symlink pointing
outside its root, and bind mounts are out of scope. The runtime that actually writes **must
reauthorize the profile and re-resolve (realpath) the concrete path at write time**. A
`ValidatedPlan` never itself authorizes a write. Likewise `path` inputs are recorded references
only, not read grants. This module has no read-authority model, and the runtime governs reads.

## 5. plan_id: identity, not authorization, not a checkpoint

`plan_id` is the sha256 hex of canonical JSON (`sort_keys`, compact separators, ASCII) over the
**validated** data: schema, group id, owner, mode, review_of, and each task in topological
order. It is stable across re-validation and independent of declaration order. The sort makes
key order irrelevant, and topological order is deterministic. It is **identity only**. Anyone can
compute one, it is not authorization proof (authority is re-checked at run time), and it is not
a persisted checkpoint. Nothing here writes or reserves state.

## 6. Non-claims and current status

- No secret-content detection. Unknown structural keys are refused, but `goal` and `acceptance`
  text is not inspected for secret-looking strings. The credential boundary is enforced by the
  runtime and environment, not by this validator.
- No runtime call sites exist. Nothing in this module can start, schedule or complete swarm
  execution. Using it from `GroupHost` would need new code that does not exist here.
- `HostGrants` is defined in this module. It needs no other new code, and no change to `RouteConfig`.

## 7. Size note

The module is 496 lines and the test file is 418 lines with 25 test functions. The reviewer and
verifier logic (I14/I15), reference resolution (I7) and grants coercion take most of the extra
length. No rule was compressed to save lines, and no rule depends on the code that could be
trimmed. For example, `_freeze_permits` and `_freeze_roots` could be merged, and the module
docstring shortened.
