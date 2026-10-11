# Self-improving flywheel

> Every incident becomes one bounded rule at the canonical layer, so the team
> learns once and every member inherits the improvement.

## The loop

The team does not treat an incident as a reason to add more effort to the same
run. It converts the incident into a rule that prevents the root failure:

`incident → root cause (not the symptom) → smallest encoded rule → routed to its ONE canonical home → verified by a different member → next incident re-tests it`

Each arrow is a gate. If the root cause is not known, investigate before writing
the rule. If more than one rule is proposed, keep the smallest rule that blocks
the failure and record the other proposals as out of scope.

## Canonical home

A rule belongs at the shared or router layer when it governs team behaviour.
That gives the rule one home, one owner for changes, and one inheritance path for
every member. Do not copy a shared rule into individual role cards or local
profiles. Copies drift, create conflicting interpretations, and allow a member
to bypass the rule by using an older copy.

The canonical home must be the surface that can enforce the rule. A rule in a
report, prompt, or retrospective is not enough when the failure occurs in a
router, contract, gate, or other control surface. The handoff names the exact
canonical path and the enforcement surface.

## Guardrails

- **Bounded.** Encode the smallest useful rule. Do not add unbounded polish,
  extra process, or a general framework to solve one incident.
  `[VERIFIED — internal operating record, 2026-09-15]`
- **Provenance-tagged.** Every rule names its evidence class and points to the
  incident record or other evidence that supports it. `[VERIFIED — internal
  operating record, 2026-09-15]`
- **No self-approval.** The author of a rule never verifies that rule. A
  different member checks the wording, the evidence, and the enforcement
  surface before the rule becomes canonical. `[VERIFIED — internal operating
  record, 2026-09-15]`
- **No scope creep.** One incident produces one rule. Reopen the rule only when
  new evidence shows that it is incomplete or wrong. Do not use a new failure
  mode as a reason to expand the old rule silently. `[VERIFIED — internal
  operating record, 2026-09-15]`

## Rejection test

A proposed rule is **REJECTED** when it cannot name all three items below:

1. **Incident** — the observable event or failure that caused the rule.
2. **Failure prevented** — the root failure the rule is designed to stop, not
   only the visible symptom.
3. **Enforcement surface** — the canonical contract, router, gate, or other
   surface that enforces the rule.

A vague lesson, a preference, or a rule with no enforcement surface stays a
proposal. It does not enter the shared layer.

## Worked examples

### 1. Repeated redundant operation

- **Incident:** A missing stop condition caused repeated redundant operations
  in one turn.
- **Root cause:** The procedure had no test for whether the next operation could
  return new information.
- **Smallest rule:** Before repeating an operation, require a stated new
  information target. If there is none, stop.
- **Canonical home:** The shared orchestration contract, where turn-level
  execution rules are inherited by every role.
- **Verification:** A different member checks the rule against the incident and
  runs a case where the repeated operation returns no new information.
- **Re-test:** The next similar operation must stop at the condition instead of
  retrying.

`[VERIFIED — internal operating record, 2026-09-15]`

### 2. Read-back applied to a local receipt

- **Incident:** A verification rule was applied to local operations that had
  already returned a receipt, causing needless repeated read-back.
- **Root cause:** The rule did not distinguish local operations with a trusted
  tool receipt from externally visible state that can differ from local state.
- **Smallest rule:** Trust the successful receipt for a local operation; require
  read-back only for externally visible state.
- **Canonical home:** The shared verification contract, where local and
  external verification boundaries are defined once.
- **Verification:** A different member checks one local receipt and one external
  state change, and confirms that only the external change needs read-back.
- **Re-test:** A later local operation must not trigger a duplicate verification
  pass solely because the general verification rule exists.

`[VERIFIED — internal operating record, 2026-09-15]`

### 3. Concurrent stage-and-commit work

- **Incident:** Concurrent writers in one shared worktree contaminated commit
  attribution because the stage-and-commit step was not serialized.
- **Root cause:** Multiple writers could change the shared index between staging
  and commit, so the commit boundary did not belong to one known change set.
- **Smallest rule:** Serialize stage and commit for a declared file set under a
  single lock, then verify the resulting identity and file scope.
- **Canonical home:** The shared contribution and orchestration contract, where
  repository write boundaries are enforced for every role.
- **Verification:** A different member checks the lock boundary and confirms
  that a concurrent attempt cannot enter the stage-and-commit section together.
- **Re-test:** The next shared-worktree commit has only its declared files and
  the intended author identity.

`[VERIFIED — internal operating record, 2026-09-15]`

### 4. Stale-tree hazard

- **Incident:** A stale branch was used as a PR base. An agent created a PR
  branch from a local branch that was 14 commits behind origin/main. The diff
  would have deleted content that had already shipped on main.
- **Root cause:** No rule required verifying branch freshness before creating a
  PR branch.
- **Smallest rule:** Before creating a contribution branch, fetch the target
  repository's default branch and create the branch from the fetched head, not
  from a local branch that may be stale.
- **Canonical home:** The shared contribution contract (choreography/open-source-contribution.md).
- **Verification:** A different member checks that the PR base commit matches
  the fetched head of the target default branch.
- **Re-test:** The next contribution PR must show a base commit that is at or
  ahead of the target repository's default branch head at the time of branch
  creation.

`[VERIFIED — internal operating record, 2026-09-15]`

### 5. Mirror forks

- **Incident:** When a repository has mirror forks, a change to one mirror does not automatically propagate to the others.
- **Root cause:** No rule required that a change reach every mirror with consistent author identity and file set.
- **Smallest rule:** When one repository has mirror forks, one change must reach every mirror; extract the diff once, apply it to each mirror, rebuild each commit with its own file set and its own author identity, and confirm the rebuilt trees are identical across mirrors.
- **Canonical home:** The shared choreography contract, where multi-repo synchronization rules are inherited by every role.
- **Verification:** A different member checks that each mirror has the same tree and the commit author identity matches the mirror's convention.
- **Re-test:** The next change applied to mirrors confirms identical trees across all mirrors.

`[VERIFIED — internal operating record, 2026-09-15]`

## Operating record

The flywheel is complete only when the rule is canonical, independently
verified, and available for the next run. A later incident may falsify the rule.
If it does, return to the loop with the new evidence. Do not hide the failure by
adding a second copy or retrying the old procedure.
