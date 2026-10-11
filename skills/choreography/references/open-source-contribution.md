# Open-source contribution contract

> When the team pulls from, ingests, or depends on an open-source repository,
> it looks for a substantiated improvement and moves it through a visible,
> reviewable contribution path.

## Standing obligation

A repository is not only a source to consume. During ingestion, capability
review, build, and QA, record bugs, missing documentation, improvements, and
missing features that have enough evidence for a contribution. Keep the
contribution generic and safe for a public surface. Do not publish private
context, credentials, internal paths, or client data.

## Defect bar before writing

Do not write a patch because a design choice looks different from the team's
preference. First prove one of these conditions:

- **User-visible failure** — a user sees duplicate execution, wrong or lost data,
  a crash, a hang, or another concrete failure.
- **Written invariant violated** — quote the governing document and give its
  exact `file:line` location.
- **Unintended behaviour proven** — show that a surface requests, emits, or
  retains something that no approved requirement needs.

Read the project's `AGENTS.md` and relevant documentation before proposing a
change. Include any explicit "do not fix this" list in the evidence review.
A change that rests on a design decision is a proposal for the maintainers, not
a defect fix.

The finding must still be live on upstream `main` before a patch is written.
Check the current upstream state and record the exact revision or observation
used. A stale reproduction is not proof that a new patch is needed.

`[REQUIRED — repository evidence]`

## The four steps

### 1. Identify

State the failure or improvement in one sentence. Cite every relevant location
with `file:line` evidence. Name the user-visible effect, violated invariant, or
proof of unintended behaviour. Record the upstream revision checked and the
smallest plausible fix.

`[REQUIRED — repository evidence]`

## Branch freshness requirement

Every contribution branch must be created from a fresh fetch of the target
repository's default branch. An agent created a PR branch from a local branch
that was 14 commits behind origin/main. The diff would have deleted content
that had already shipped on main. A stale base produces a PR that reverts or
duplicates existing work. Before creating a contribution branch, fetch the
target repository's default branch and create the branch from the fetched head,
not from a local branch that may be stale. This is enforced in the `The four
steps` > `1. Identify` step, which already requires checking "the current
upstream state."

`[VERIFIED — internal operating record]`

### 2. Draft

Choose a pull request or an issue by using the project's own contribution model:

- Read its contribution guide, `AGENTS.md`, issue forms, and pull request
  templates.
- Follow its labels, commit style, and required checks.
- Check and satisfy its CLA, DCO, sign-off, and attribution requirements.
- Keep the draft limited to the identified defect or improvement. Separate
  feature proposals from defect fixes.

A draft is not submitted work. It must preserve the evidence register: root
cause, changed paths, validation, and known limits.

`[REQUIRED — project contribution policy]`

### 3. Review

The team never self-approves an external contribution. A human sees the draft
before submission. The human review covers the evidence, scope, public-safety
scan, project conventions, and the proposed validation. If the draft changes a
design decision rather than fixing a proven defect, present it as a proposal and
wait for maintainer direction.

`[REQUIRED — independent human review]`

### 4. Track

Record the contribution in the project record with its evidence, target
repository, revision checked, artifact or draft link, and current state:

`proposed → submitted → merged/rejected`

Do not mark a contribution as submitted until the project accepts the actual
submission. Do not mark it as merged until upstream read-back shows the merge.
A rejected contribution remains recorded with the reason and the next allowed
step, if any.

`[REQUIRED — project record and upstream read-back]`

## Stop conditions

Stop before drafting when the defect bar is not met, the behavior is not live on
upstream `main`, the project's contribution rules are unavailable, or the draft
would expose private information. Record the blocker. Do not turn uncertainty
into a patch or fill missing evidence with assumptions.

## Evidence classes

- `[REQUIRED — repository evidence]` — observed in the target repository or its
  current upstream state.
- `[REQUIRED — project contribution policy]` — required by the target project's
  documented process.
- `[REQUIRED — independent human review]` — approval boundary before submission.
- `[REQUIRED — project record and upstream read-back]` — state and outcome
  evidence after submission.

This contract adopts the four-step obligation as a generic Protean operating rule.
It does not copy source code, prompts, templates, or license text from any
external repository.
