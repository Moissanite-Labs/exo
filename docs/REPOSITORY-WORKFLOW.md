# One implementation, reviewed repository syncs

Exo owns shared code. Exo Desktop owns its native shell and packaging. Implement each shared change once, then bring merged Exo commits into the desktop fork through a reviewed sync PR.

This is the principal's [approved direction of 2026-10-28](https://github.com/Moissanite-Labs/exo/discussions/746), recorded as D-089. It changes the default routing of new work while retaining a temporary alpha exception.

## Where a change belongs

| Change | Implementation and review home |
| --- | --- |
| Core, providers, memory, permissions, protocol, shared plugins and web UI | `Moissanite-Labs/exo` |
| Documentation and tests for shared behavior | `Moissanite-Labs/exo`, beside that implementation |
| Native desktop shell, desktop packaging, native bridges and their documentation and tests | `Moissanite-Labs/exo-desktop` |
| Desktop sync automation and its tests | `Moissanite-Labs/exo-desktop` |

`Moissanite-Labs/exo-cli` is currently a fork snapshot. Its extraction proposal does not change the shared ownership above. Preserve existing terminal PRs and link work across repositories until a reviewed migration defines the package and API boundary.

A web UI change in `plugins/exo-command` is shared code even when the desktop pilot discovers it. A feature needing a shared API and native integration can have two PRs for those different parts. Do not create a second implementation PR for the same shared change.

## Author and review flow

1. Claim the change through the team's existing coordination process. Keep the author responsible for rework, or record an explicit handoff.
2. Make the implementation in its home repository from a current base. Run the relevant tests and obtain independent review of the exact head. Existing merge rights and verification requirements still apply.
3. Record parent PRs for a stack. A merge into a feature branch is not a main landing. Recheck the target branch before calling work landed or deleting a branch.
4. Merge the canonical change into Exo main. Record its commit and affected behavior in the next desktop sync.

The code change is reviewed once upstream. The downstream reviewer checks the combined tree, conflict resolutions and desktop integration. Upstream approval does not prove that native behavior or downstream integration works.

## Desktop syncs

A sync PR records the pinned Exo main commit, the desktop main commit it starts from, carried alpha patches, conflict resolutions and verification results. It brings a batch of merged upstream commits into the fork with Git merge history intact. Do not replace the fork tree wholesale or silently drop native files.

Automation prepares a draft sync PR on a reserved branch. It never pushes to main, merges a PR, force-pushes an author branch or restarts a live install. A textual conflict goes back to the responsible authors. Fetch, authentication, merge, push and PR-creation failures must be reported as the operation that failed.

Test the resulting tree with the relevant shared and desktop checks, then obtain independent review. Preserve commit ancestry where available. Squash merges, cherry-picks and already-existing files also need content and behavior checks, so an ancestry test alone is not proof that a fix exists.

Private-source access and workflow-file updates require suitable credentials. The automation must report missing configuration clearly. Do not copy a local agent token into repository secrets or widen access as part of a sync. Until credentials and hosted Actions are working, an authorized maintainer uses the same PR-based process manually.

The fork's `CONTINUITY.md` documents its script, reserved branch and credential requirements. This file is the authority for shared work routing. Core decisions are recorded here in Exo and referenced by the fork, rather than independently redefined there.

## Existing PRs and alpha exceptions

Inventory each existing pair before changing it: canonical Exo PR and head, remaining native files, temporary shared patches, author, reviewer and intended sync. Stop adding shared feature work to the desktop copy. Preserve unique commits, tests and review findings.

A legacy desktop PR can close as superseded only after the canonical change has reached main, downstream integration is verified, and its unique native work is retained. Coordinate the close with its author under the existing process. A policy change alone does not justify closing, deleting or resetting unfinished work.

An urgent alpha build may temporarily carry a shared patch. Record its canonical Exo PR, owner, exact source commit and reconciliation step. The carry is temporary, not a second permanent implementation. A local build is not proof of an upstream merge or downstream release.

Upstream landing, desktop integration and live installation are separate states. A sync does not authorize installing it over an active test run or changing credentials, model choices or permission scope.

## Communication

Use the linked decision discussion for the shared transition record, and the Bus for private assignments and handoffs. Feature requests have one discussion each in the Feature requests category. Bugs remain issues in the implementation's home repository, with links from dependent desktop work.
