"""Recall reads belief nodes fresh on each turn.

The provider keeps one store object per process. Nodes reach the disk through other store objects: the CLI's
(`exo proposals accept` in another process) and the learner's own runtime in the same daemon (promotion, revert).
Before, recall served the nodes it read on its first call until a restart: an accepted belief never showed, a
reverted one kept showing. exo_get served an edited node's old body and failed on a removed one instead of
saying "no node". The pack stays built once per process (prompt caching).
"""
import json

from exo import consolidate as C
from exo.learn.runtime import Runtime
from exo.store import PrincipalStore

NODE = ("---\nid: work/zebra-deploys\ntitle: Zebra deploys\ntype: rule\ndomain: work\n"
        "summary: Zebra deploys go out on Fridays only.\nload_when: zebra deploy\nconfidence: high\n"
        "stability: durable\nvalid_from: 2026-01-01\nsources: [test]\nlinks: []\n---\n{body}\n")


def test_recall_sees_a_node_written_after_the_first_recall(provider, store):
    pack = provider.system_prompt_block()
    assert "## Principal beliefs" in provider.prefetch("communication style and reporting")
    other = PrincipalStore(store.root)  # the CLI in another process
    path = other.nodes_dir / "work" / "zebra-deploys.md"
    path.write_text(NODE.format(body="Zebra deploys go out on Fridays only."))
    assert "work/zebra-deploys" in provider.prefetch("zebra deploy fridays")
    assert "work/zebra-deploys" in provider.handle_tool_call("exo_recall", {"query": "zebra deploy fridays"})
    path.write_text(NODE.format(body="Zebra deploys moved to Tuesdays, after the release freeze."))
    # exo_get on its own, no recall in between: body_only serves the cached body, so a stale cache shows here
    got = json.loads(provider.handle_tool_call("exo_get", {"id": "work/zebra-deploys", "body_only": True}))
    assert "after the release freeze" in got["result"] and "Fridays only" not in got["result"]
    assert "after the release freeze" in provider.prefetch("zebra deploy tuesdays")
    path.unlink()
    got = json.loads(provider.handle_tool_call("exo_get", {"id": "work/zebra-deploys"}))
    assert got.get("error") == "no node work/zebra-deploys"  # not a failed read of the removed file
    assert "work/zebra-deploys" not in provider.prefetch("zebra deploy tuesdays")
    assert provider.system_prompt_block() == pack  # still built once


def test_recall_follows_the_learner_accepting_and_reverting(provider, store, tmp_path):
    provider.prefetch("communication style and reporting")
    lrt = Runtime(store.root, tmp_path / "exo-home")  # the learner's own store and ledger, as in the daemon
    try:
        out = lrt.ledger.append("utterance", {"text": "old output", "role": "assistant"}, "agent", session="s1")
        said = lrt.ledger.append("verdict", {"verdict": "edit", "target": out["id"],
                                 "text": "releases ship from the quokka branch"}, "principal", session="s1")
        (res,) = C.propose_typed(lrt.store, lrt.ledger, [{
            "type": "fact", "claim": "Releases ship from the quokka branch.", "domain": "work",
            "wants": "releases ship from the quokka branch", "because": "they said so", "evidence": [said["id"]]}],
            author=lrt.agent)
        node = C.proposal_accept(lrt.store, lrt.ledger, res["id"], author=lrt.agent)["node_id"]
        assert node in provider.prefetch("quokka branch releases")
        C.proposal_revert(lrt.store, lrt.ledger, res["id"], "wrong branch", author=lrt.agent, follow_up=False)
        got = json.loads(provider.handle_tool_call("exo_get", {"id": node}))  # before any recall reloads
        assert got.get("error") == f"no node {node}"
        assert node not in provider.prefetch("quokka branch releases")
    finally:
        lrt.close()
