"""The learner's store lives as long as the daemon. A node another store object removed or added since it last
read (`exo proposals revert` or `accept` in the CLI) must not break the next promotion: `proposal_accept` lints and
links against the disk, not the learner's first read."""
from exo.learn.promote import apply_policy
from exo.store import PrincipalStore

from helpers import propose, ruling


def test_promotion_after_another_store_removed_a_node(learner):
    rt = learner.runtime
    assert "work/projects" in rt.store.nodes  # the learner has read the store
    (PrincipalStore(rt.root).nodes_dir / "work" / "projects.md").unlink()  # the CLI, in another process
    r = ruling(rt, "From now on, status lines are plain, not decorated, because they get pasted into tickets.")
    res = propose(rt, evidence=[r["id"]])
    (d,) = apply_policy(learner)
    assert d.id == res["id"] and d.action == "promoted" and d.rule == 1
    assert "work/projects" not in rt.store.nodes
