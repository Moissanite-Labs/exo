# Hosted CI startup diagnosis

Issue #615 remains unresolved until a new workflow attempt starts jobs and reports
their results. Local test passes and a completed workflow with zero jobs do not
prove that hosted execution works. This diagnostic slice supplies a reproducible,
read-only acceptance check; it does not repair account or organization settings.

Run from an authenticated checkout with Python and `gh`:

```sh
python3 scripts/actions_health.py aska-digital/exo RUN_ID --expected-sha FULL_COMMIT_SHA
```

The script issues only GET requests. It pins the numbered attempt, checks every
job page (at most 1,000 jobs), rejects inconsistent or incomplete inventories and
rechecks the run after collection. Reports omit logs, job names, step bodies and
credential details. A report is a dated observation, not a permanent health claim.

Exit 0 means jobs reported final results for the requested head; it can include
failed tests. `ci_passed` separately requires a successful run and successful or
skipped jobs. Exit 1 means execution is not yet proven (including wrong head,
pending work and zero-job startup failure). Exit 2 means inspection failed, such
as missing access, transport failure, invalid data or a concurrently changed run.
Neither an HTTP error nor an incomplete inventory is evidence of zero jobs.

## Observed baseline and recovery work

On 2026-10-28, main run [40642862800](https://github.com/aska-digital/exo/actions/runs/40642862800)
for `f28934a3f6bfcaec3a5e29223f7354df788afe07` completed as `startup_failure`
with zero jobs. This establishes the symptom, not its cause. Billing, organization
policy and repository configuration require their own evidence.

A maintainer with the required access should inspect GitHub's run diagnostics,
repository/organization Actions settings and billing notices, recording the exact
diagnostic without credentials. Make only the identified authorized repair. Then
inspect a fresh workflow run with this command and its expected commit. Preserve
both the failed baseline and successful hosted-execution report; inspect test
failures separately. Do not mark #615 fixed solely because this draft merges.

User-guide impact: none; this is a maintainer diagnostic. Developer-guide impact:
the merge-flow section links this acceptance check. No runtime, workflow,
credentials, spending or account settings change.
