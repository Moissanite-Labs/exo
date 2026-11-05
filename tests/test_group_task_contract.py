"""Focused tests for the orchestration schema (src/exo/core/group_task_contract.py).

Literals only: no fixtures, no I/O, no clock, no network. Each test states the invariant it pins.
"""

from __future__ import annotations

import dataclasses

import pytest

from exo.core.agents.route_config import RouteConfig
from exo.core.group_task_contract import (
    SCHEMA_VERSION,
    ContractError,
    HostGrants,
    Reference,
    validate_plan,
)
from exo.core.groups import GroupMember, SavedGroup

OWNER = "alice"


def grp(members=("builder", "checker"), mode="socratic", owner=OWNER):
    return SavedGroup(
        id="g1",
        owner=owner,
        mode=mode,
        conductor=members[0] if mode == "conductor" else None,
        members=tuple(GroupMember(p, f"s-{p}") for p in members),
        created="2026-11-06T00:00:00Z",
    )


def grants(**over):
    base = dict(
        owner=OWNER,
        profiles=frozenset({"builder", "checker"}),
        permits={"builder": frozenset({"read_repo", "write_scope"})},
        write_roots={"builder": ("/srv/w",), "checker": ("/srv/w",)},
        routes=RouteConfig.of(
            {"backend": "model_map", "routes": {"cheap": "fake:fast"}}
        ),
        reviewer_profiles=frozenset({"checker"}),
    )
    base.update(over)
    return HostGrants(**base)


def build_task(**over):
    task = {
        "task_id": "build",
        "role": "builder",
        "goal": "write the thing",
        "profile": "builder",
        "acceptance": ["tests pass"],
    }
    task.update(over)
    return task


def review_task(**over):
    task = {
        "task_id": "check",
        "role": "reviewer",
        "goal": "review the thing",
        "profile": "checker",
        "acceptance": ["findings recorded"],
        "depends_on": ["build"],
        "outputs": [{"kind": "message", "ref": "m-1"}],
    }
    task.update(over)
    return task


def plan(*tasks, **over):
    payload = {"schema": SCHEMA_VERSION, "tasks": list(tasks) or [build_task()]}
    payload.update(over)
    return payload


def reviewed():
    return plan(build_task(), review_task(), review_of={"build": "check"})


# T01 -------------------------------------------------------------------------------------------
def test_minimal_plan_accepts():
    out = validate_plan(plan(), grants(), group=grp())
    assert out.schema == SCHEMA_VERSION
    assert (out.group_id, out.owner, out.mode) == ("g1", OWNER, "socratic")
    assert [t.task_id for t in out.tasks] == ["build"]
    assert len(out.plan_id) == 64 and all(c in "0123456789abcdef" for c in out.plan_id)


# T02 -------------------------------------------------------------------------------------------
def test_unknown_key_rejected():
    for bad in (
        plan(mode="socratic"),
        plan(owner="mallory"),
        plan(seats=[]),
        plan(plan_id="x"),
    ):
        with pytest.raises(ContractError, match="I1"):
            validate_plan(bad, grants(), group=grp())
    with pytest.raises(ContractError, match="I1"):
        validate_plan(plan(build_task(seats=["builder"])), grants(), group=grp())
    with pytest.raises(ContractError, match="I1|I2"):
        validate_plan(
            plan(build_task(outputs=[{"kind": "message", "ref": "m-1", "x": 1}])),
            grants(),
            group=grp(),
        )
    with pytest.raises(ContractError, match="I8"):
        validate_plan(
            plan(build_task(outputs=[{"kind": "message", "ref": "m-1", "x": "y"}])),
            grants(),
            group=grp(),
        )


# T03 -------------------------------------------------------------------------------------------
def test_required_key_and_default_rules():
    no_profile = build_task()
    no_profile.pop("profile")
    with pytest.raises(ContractError, match="I2"):
        validate_plan(plan(no_profile), grants(), group=grp())
    out = validate_plan(plan(), grants(), group=grp())
    assert (
        out.tasks[0].route == ""
        and out.tasks[0].permits == ()
        and out.tasks[0].depends_on == ()
    )
    kept = validate_plan(plan(build_task(route="")), grants(), group=grp())
    assert kept.tasks[0].route == ""
    with pytest.raises(ContractError, match="I9"):
        validate_plan(plan(build_task(acceptance="tests pass")), grants(), group=grp())


# T04 -------------------------------------------------------------------------------------------
def test_duplicate_task_id_rejected():
    with pytest.raises(ContractError, match="I3 build"):
        validate_plan(
            plan(build_task(), build_task(role="verifier")), grants(), group=grp()
        )


# T05 -------------------------------------------------------------------------------------------
def test_bad_task_id_rejected():
    for bad in ("1build", "", "bu ild", "a" * 65, "build/1"):
        with pytest.raises(ContractError):
            validate_plan(plan(build_task(task_id=bad)), grants(), group=grp())


# T06 -------------------------------------------------------------------------------------------
def test_unknown_and_self_dependency_rejected():
    with pytest.raises(ContractError, match="I4"):
        validate_plan(plan(build_task(depends_on=["ghost"])), grants(), group=grp())
    with pytest.raises(ContractError, match="I4"):
        validate_plan(plan(build_task(depends_on=["build"])), grants(), group=grp())
    with pytest.raises(ContractError, match="I4"):
        validate_plan(plan(build_task(depends_on=["a", "a"])), grants(), group=grp())


# T07 -------------------------------------------------------------------------------------------
def test_cycle_rejected_names_member():
    a = build_task(task_id="a", depends_on=["b"])
    b = build_task(task_id="b", depends_on=["a"])
    with pytest.raises(ContractError) as exc:
        validate_plan(plan(a, b), grants(), group=grp())
    assert "I5" in str(exc.value) and ("a" in str(exc.value) and "b" in str(exc.value))


# T08 -------------------------------------------------------------------------------------------
def test_independent_roots_accepted():
    a = build_task(task_id="zeta")
    b = build_task(task_id="alpha")
    out = validate_plan(plan(a, b), grants(), group=grp())
    assert [t.task_id for t in out.tasks] == [
        "alpha",
        "zeta",
    ]  # interleaved by id, not declaration
    again = validate_plan(plan(b, a), grants(), group=grp())
    assert again.plan_id == out.plan_id


# T09 -------------------------------------------------------------------------------------------
def test_topological_order_deterministic():
    second = build_task(task_id="second", depends_on=["first"])
    first = build_task(task_id="first")
    out = validate_plan(plan(second, first), grants(), group=grp())
    assert [t.task_id for t in out.tasks] == ["first", "second"]
    assert (
        out.to_dict()
        == validate_plan(plan(first, second), grants(), group=grp()).to_dict()
    )


# T10 -------------------------------------------------------------------------------------------
def test_task_output_input_requires_dependency():
    task = build_task(inputs=[{"kind": "task_output", "ref": "other"}])
    with pytest.raises(ContractError, match="I7"):
        validate_plan(plan(task, build_task(task_id="other")), grants(), group=grp())


# T11 -------------------------------------------------------------------------------------------
def test_artifact_input_requires_dependency():
    orphan = build_task(
        task_id="use", inputs=[{"kind": "artifact", "ref": "report.md"}]
    )
    producer = build_task(
        task_id="produce", outputs=[{"kind": "artifact", "ref": "report.md"}]
    )
    with pytest.raises(ContractError, match="I7"):
        validate_plan(plan(orphan, producer), grants(), group=grp())
    ok = build_task(
        task_id="use",
        depends_on=["produce"],
        inputs=[{"kind": "artifact", "ref": "report.md"}],
    )
    out = validate_plan(plan(ok, producer), grants(), group=grp())
    assert out.tasks[-1].inputs == (Reference("artifact", "report.md"),)


# T12 -------------------------------------------------------------------------------------------
def test_relative_and_traversal_paths_rejected():
    for bad in ("a/b", "/a/../b", "//x", "/a/", "/a/./b", "", "/a\\b"):
        with pytest.raises(ContractError):
            validate_plan(plan(build_task(write_scopes=[bad])), grants(), group=grp())
        with pytest.raises(ContractError):
            validate_plan(
                plan(build_task(outputs=[{"kind": "path", "ref": bad}])),
                grants(),
                group=grp(),
            )


# T13 -------------------------------------------------------------------------------------------
def test_output_outside_write_scope_rejected():
    task = build_task(
        write_scopes=["/srv/w"], outputs=[{"kind": "path", "ref": "/srv/w2/x"}]
    )
    with pytest.raises(ContractError, match="I8"):
        validate_plan(plan(task), grants(), group=grp())
    inside = build_task(
        write_scopes=["/srv/w"], outputs=[{"kind": "path", "ref": "/srv/w/sub/x"}]
    )
    assert validate_plan(plan(inside), grants(), group=grp()).tasks[0].write_scopes == (
        "/srv/w",
    )


# T14 -------------------------------------------------------------------------------------------
def test_empty_acceptance_rejected():
    for bad in ([], "", "tests pass", ["  "], ["x" * 501], ("a\nb",)):
        with pytest.raises(ContractError):
            validate_plan(plan(build_task(acceptance=bad)), grants(), group=grp())


# T15 -------------------------------------------------------------------------------------------
def test_unauthorized_profile_rejected():
    with pytest.raises(ContractError, match="I10"):
        validate_plan(plan(build_task(profile="mallory")), grants(), group=grp())


# T16 -------------------------------------------------------------------------------------------
def test_non_member_profile_rejected():
    g = grants(
        profiles=frozenset({"builder", "checker", "outsider"}),
        permits={"outsider": frozenset()},
        write_roots={},
    )
    with pytest.raises(ContractError, match="I10"):
        validate_plan(plan(build_task(profile="outsider")), g, group=grp())


# T17 -------------------------------------------------------------------------------------------
def test_role_grants_nothing():
    with pytest.raises(ContractError, match="I10"):
        validate_plan(
            plan(build_task(role="verifier", profile="mallory")), grants(), group=grp()
        )
    with pytest.raises(ContractError, match="I12"):
        validate_plan(
            plan(build_task(role="builder", write_scopes=["/srv/elsewhere"])),
            grants(),
            group=grp(),
        )


# T18 -------------------------------------------------------------------------------------------
def test_unpermitted_capability_fails_closed():
    g = grants(permits={})  # no key for the builder profile
    with pytest.raises(ContractError, match="I11"):
        validate_plan(plan(build_task(permits=["read_repo"])), g, group=grp())
    with pytest.raises(ContractError, match="I11"):
        validate_plan(plan(build_task(permits=["write_any"])), grants(), group=grp())
    ok = validate_plan(plan(build_task(permits=["read_repo"])), grants(), group=grp())
    assert ok.tasks[0].permits == ("read_repo",)


# T19 -------------------------------------------------------------------------------------------
def test_elevation_capability_refused_despite_grant():
    g = grants(permits={"builder": frozenset({"approve", "escalate", "install"})})
    for cap in ("approve", "escalate", "install"):
        with pytest.raises(ContractError, match="I11a"):
            validate_plan(plan(build_task(permits=[cap])), g, group=grp())


# T20 -------------------------------------------------------------------------------------------
def test_write_scope_outside_grant_roots_rejected():
    with pytest.raises(ContractError, match="ContractError|I12"):
        HostGrants(
            owner=OWNER,
            profiles=frozenset({"builder"}),
            permits={},
            write_roots={"builder": ("/",)},
            routes=RouteConfig(),
        )
    with pytest.raises(ContractError, match="I12"):
        validate_plan(plan(build_task(write_scopes=["/tmp/w"])), grants(), group=grp())


# T21 -------------------------------------------------------------------------------------------
def test_owner_mismatch_and_members_not_overridable():
    with pytest.raises(ContractError, match="I10a"):
        validate_plan(plan(), grants(owner="mallory"), group=grp())
    with pytest.raises(ContractError, match="I1"):
        validate_plan(plan(members=["mallory"]), grants(), group=grp())


# T22 -------------------------------------------------------------------------------------------
def test_self_review_rejected():
    same = grants(reviewer_profiles=frozenset({"builder"}))
    reviewer = review_task(profile="builder")
    with pytest.raises(ContractError, match="I14"):
        validate_plan(
            plan(build_task(), reviewer, review_of={"build": "check"}),
            same,
            group=grp(),
        )


# T23 -------------------------------------------------------------------------------------------
def test_reviewer_must_be_authorized_and_depend():
    with pytest.raises(ContractError, match="I14"):
        validate_plan(reviewed(), grants(reviewer_profiles=frozenset()), group=grp())
    with pytest.raises(ContractError, match="I14"):
        validate_plan(
            plan(
                build_task(), review_task(depends_on=[]), review_of={"build": "check"}
            ),
            grants(),
            group=grp(),
        )
    with pytest.raises(ContractError, match="I14"):
        validate_plan(
            plan(
                build_task(), review_task(role="builder"), review_of={"build": "check"}
            ),
            grants(),
            group=grp(),
        )
    with pytest.raises(ContractError, match="I14"):
        validate_plan(
            plan(build_task(), review_task(), review_of={"build": "ghost"}),
            grants(),
            group=grp(),
        )


# T24 -------------------------------------------------------------------------------------------
def test_reviewer_is_read_only():
    writable = review_task(write_scopes=["/srv/w"])
    with pytest.raises(ContractError, match="I15"):
        validate_plan(
            plan(build_task(), writable, review_of={"build": "check"}),
            grants(),
            group=grp(),
        )
    # I8 refuses first here (a reviewer has no write scopes, so no path output can be inside one);
    # the read-only rule I15 is the backstop and fires for any reviewer output that is not message/artifact.
    path_out = review_task(outputs=[{"kind": "path", "ref": "/srv/w/x"}])
    with pytest.raises(ContractError, match="I8|I15"):
        validate_plan(
            plan(build_task(), path_out, review_of={"build": "check"}),
            grants(),
            group=grp(),
        )


# T25 -------------------------------------------------------------------------------------------
def test_plan_is_inert_immutable_and_roundtrips():
    out = validate_plan(reviewed(), grants(), group=grp())
    with pytest.raises(dataclasses.FrozenInstanceError):
        out.mode = "conductor"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        out.tasks[0].goal = "other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        out.review_of["build"] = "x"  # type: ignore[index]
    assert dict(out.review_of) == {"build": "check"}
    assert out.task("build").task_id == "build"
    with pytest.raises(KeyError):
        out.task("ghost")
    body = out.to_dict()
    # Identity keys (plan_id/group_id/owner/mode) are host-supplied and refused inside `raw` (I1), so the
    # round trip re-validates the plan body only and must reproduce the same content hash.
    revalidated = validate_plan(
        {k: body[k] for k in ("schema", "tasks", "review_of")}, grants(), group=grp()
    )
    assert revalidated.plan_id == out.plan_id and revalidated.to_dict() == body
    # plan_id is identity, not authorization proof: a forged id in `raw` is refused outright.
    with pytest.raises(ContractError, match="I1"):
        validate_plan({**body, "plan_id": "0" * 64}, grants(), group=grp())
    import exo.core.group_task_contract as mod

    for forbidden in ("run", "start", "schedule", "store", "execute", "write", "open"):
        assert not hasattr(mod, forbidden)
