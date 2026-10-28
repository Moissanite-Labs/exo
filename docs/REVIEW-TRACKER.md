# Re-review the original audit queue

Issue #51 preserves twelve findings from the audit at `e079a65`. Its historical test counts and
branch descriptions belong to that revision. Refresh the whole queue before making a current
claim, rather than treating its old list of draft PRs as live state.

```sh
python scripts/review_tracker.py --live
```

The report always includes #31, #46, #49, #130, #131, #132, #133, #135, #136, #137, #143 and #128,
in the original order. It queries issue state, PR cross-references, current PR heads/base branches,
draft/review status and explicit issue-closing events. It changes no issue, review, label or ref.
`--snapshot FILE` reads a previously saved query response without network access.

An open issue with a PR reference remains open. A reference can cover one slice or discuss a
related defect; it does not prove full implementation. Check the PR body, the exact-head review
and remaining acceptance before deciding whether the link is sufficient. Cross-repository links
retain their full URLs so identically numbered desktop and Exo PRs are not confused.

For a closed issue, the report distinguishes a merged PR recorded as the closer from a mere
reference to a merged PR. A manual closure without that evidence is marked for verification.
Even a merged closer is not proof the intended implementation reached today's main branch:
verify ancestry and current source separately, especially for feature-branch merges.

The script rejects missing findings and malformed/incomplete source data. A timeline longer than
the requested page marks `complete: false` and exits 2; paginate that timeline before treating the
report as complete. Query/access failures also exit 2 without printing server error details.
A complete report exits 0, including when work remains. It is an inventory, not a merge gate or
an assertion that the audit is finished.

At the 2026-10-28 check, all twelve findings were returned without timeline truncation. Eight had
closed through a merged PR; four remained open with PR references. Those numbers are a dated
snapshot, not an instruction to close or approve anything. The original sandbox, engine/interface,
history and parameter-expansion acceptance remains with its issue and exact current review.
