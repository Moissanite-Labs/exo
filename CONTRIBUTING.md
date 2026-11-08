# Contributing

Follow the [repository workflow](docs/REPOSITORY-WORKFLOW.md) for change ownership, checks and independent review.

For improvements discovered while using Exo, follow the [pilot iteration and review procedure](docs/REPOSITORY-WORKFLOW.md#pilot-iteration-and-review). Record the finding and reasoning in an issue, hand any proposed patch to an independent reviewer, and keep tests, live observations and merge status explicit. A useful bug report does not require a proposed fix.

After the history cleanup, start from a fresh clone. Never merge or push pre-cleanup ancestry or refs back into this repository. Recover local work only through an independently reviewed clean patch or cherry-pick, after checking its diff, resulting tree and commit message for removed sensitive material.
