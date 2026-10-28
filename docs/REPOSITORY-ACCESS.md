# Verify repository access for a session

Issue #180 records a standing authorization for `aska-digital/exo`. Authorization and a currently
working credential are different facts. This preflight checks the selected GitHub actor and the
repository role visible to it without changing the host login or exercising a write.

```sh
python scripts/check_repository_access.py --expected-actor EXPECTED_PUBLIC_LOGIN
```

Use the public login approved for the session. The output reports whether it matches, whether Exo
is visible and the account's repository permission. It does not print the selected actor, credential,
GitHub error body or token scope list. Exit 0 requires the expected actor and an account role of
WRITE, MAINTAIN or ADMIN; exit 1 means a mismatch or read-only account role, and exit 2 means access
could not be verified. A narrow token can still lack a write permission despite the account's role,
so `write_operation_verified` remains false. No result proves that credentials never expire or that
access is permanent.

Run this before repository stewardship and after selecting or rotating an authorized seat credential.
Use the existing seat-auth setup when available. Do not switch the host account as a repair for a seat
configuration, and do not paste a token into argv, issues, PRs or audit output. If the preflight fails,
resolve the account/credential selection locally with the authorized operator, then rerun it.

The authorization remains scoped to Exo. It does not authorize deleting resources, changing repository
visibility, rewriting history, account provisioning, payments or high-exposure public posts. The
preflight grants no permission, creates no credential and changes no login, environment or remote state.
A real authorized branch push or PR operation must be verified separately when needed. Issue #180
stays open as the access record; this preparation does not assert a new or perpetual grant.
