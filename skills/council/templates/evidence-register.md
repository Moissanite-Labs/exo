# Evidence register — template (v1.0.0)

    council: <short-lane-name>

| id | claim | source (live read / path / artifact) | verified by | status |
|---|---|---|---|---|
| E1 | <factual claim> | <exact source> | <seat / check command> | verified / unverified / stale |

Rules: every factual claim in any seat's output carries a register row.
Unverifiable claims are marked `unverified` — never silently normalized.
Dissents get their own rows:

| id | seat | dissents from | because | resolution |
|---|---|---|---|---|
| D1 | <seat> | <claim / E-id> | <evidence> | <resolved / open item O-n> |
