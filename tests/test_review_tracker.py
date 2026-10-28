"""The audit queue must retain every original finding and distinguish references from closure."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/review_tracker.py"
spec = importlib.util.spec_from_file_location("review_tracker", SCRIPT)
tracker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tracker)


def snapshot():
    return {"data": {"repository": {f"i{n}": {"number": n, "title": f"finding {n}",
        "url": f"https://github.com/aska-digital/exo/issues/{n}", "state": "OPEN",
        "timelineItems": {"pageInfo": {"hasNextPage": False}, "nodes": []}}
        for n in tracker.FINDINGS}}}


def pr(number, state="OPEN", repository="exo"):
    return {"__typename": "PullRequest", "number": number, "title": "fixture repair",
            "url": f"https://github.com/aska-digital/{repository}/pull/{number}", "state": state,
            "headRefOid": "a" * 40, "baseRefName": "feature-base", "reviewDecision": "CHANGES_REQUESTED", "isDraft": True}


def test_full_original_queue_and_cross_repository_identity_are_preserved():
    payload = snapshot()
    issue = payload["data"]["repository"]["i31"]
    issue["timelineItems"]["nodes"] = [{"__typename": "CrossReferencedEvent", "source": pr(7)},
        {"__typename": "CrossReferencedEvent", "source": pr(7, repository="exo-desktop")}]
    report = tracker.summarize(payload)
    assert report["finding_count"] == 12
    assert [x["number"] for x in report["findings"]] == list(tracker.FINDINGS)
    assert report["findings"][0]["status"] == "open_with_pr_references"
    assert len(report["findings"][0]["pr_references"]) == 2
    assert report["findings"][1]["status"] == "open_without_pr_reference"
    assert "do not establish full coverage" in report["limits"]


def test_closed_needs_explicit_merged_closer_and_truncation_never_claims_complete():
    payload = snapshot()
    issue = payload["data"]["repository"]["i31"]
    issue["state"] = "CLOSED"
    issue["timelineItems"]["nodes"] = [{"__typename": "CrossReferencedEvent", "source": pr(8, "MERGED")}]
    assert tracker.summarize(payload)["findings"][0]["status"] == "closed_needs_closure_evidence"
    issue["timelineItems"]["nodes"].append({"__typename": "ClosedEvent", "closer": {
        "__typename": "PullRequest", "number": 8, "url": pr(8)["url"], "merged": True}})
    assert tracker.summarize(payload)["findings"][0]["status"] == "closed_by_merged_pr"
    issue["timelineItems"]["pageInfo"]["hasNextPage"] = True
    assert not tracker.summarize(payload)["complete"]


@pytest.mark.parametrize("mutation", ["missing", "wrong_number", "invalid_state", "errors"])
def test_partial_or_malformed_inventory_is_rejected(mutation):
    payload = snapshot()
    if mutation == "missing":
        del payload["data"]["repository"]["i31"]
    elif mutation == "wrong_number":
        payload["data"]["repository"]["i31"]["number"] = 999
    elif mutation == "invalid_state":
        payload["data"]["repository"]["i31"]["state"] = "UNKNOWN"
    else:
        payload["errors"] = [{"message": "fixture"}]
    with pytest.raises(ValueError):
        tracker.summarize(payload)
