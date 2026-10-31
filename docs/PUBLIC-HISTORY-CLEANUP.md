# Prepare Exo history for public sharing

Issue #49 tracks private material deleted from the working tree but still reachable through earlier
commits. This runbook prepares a reviewable cleanup. It does not authorize a rewrite, a force push,
a visibility change or publishing a mirror. Keep every original and rehearsal copy private.

## Read-only inventory

Run the content-free audit against a full local clone (including a bare mirror). Shallow and partial/promisor clones
and graft-altered repositories are refused before object traversal and produce exit 2
with incomplete coverage. Traversal pins the initial named-ref IDs and HEAD; named-ref
or HEAD motion marks the report incomplete:

```sh
python scripts/audit_public_history.py --repo /path/to/private/clone
```

It checks all local refs, including branches and tags, without applying replacement objects. The
report contains only the four target paths already named in #49, counts and completeness flags.
It never prints matched text, commit contents, local repository paths or object identifiers. A
private `--literal-file /path/to/private/patterns` can count known private repository names or other
exact byte strings, one per line. Neither those strings nor the pattern-file path enter the report.
When literals are supplied, `all_objects_literals` counts them once per reachable blob,
commit object (including messages and identities) and annotated tag object. Per-target counts
cover only those four paths; use the aggregate for whole reachable-history verification.
Trees, reflogs, unreachable objects, Git LFS payloads and remote-only objects are not scanned.
`complete` means the requested local scan finished, never complete GitHub coverage.
Do not commit the private pattern file or put its values in an issue, PR, command argument or log.

The targets are `inventory/`, `docs/BRIEF.md`, `docs/STORE-V2.md` and `scripts/deploy-site.sh`.
The first two are removal candidates whenever they have historical blobs. Email/home-path matches
in the other two are review signals, including possible harmless examples; they are not an
automatic deletion rule. Large blobs are reported as unscanned and cause exit 2. Exit 0 means the
bounded scan finished, not that history is safe to publish. These patterns do not detect every kind
of private material. Reflogs, unreachable objects, remote pull-request refs, caches, forks and copies
are outside this local reachable-ref report.

## Private rehearsal

1. Freeze changes and record every branch/tag head privately. Coordinate the rewrite window and
   re-clone plan with all owners. Make an offline private backup that will never be published.
2. Make a fresh private mirror for rehearsal, retaining the original clone unchanged. Verify it has
   every intended branch and tag. Audit that mirror and review the findings locally. Do not copy
   historical file contents into public review materials.
3. Prepare a private replacement map for the earlier store/deploy documents and any other confirmed
   private strings. Review the exact removal/replacement scope before executing it. `git filter-repo`
   must be installed separately; this repository's audit does not install or invoke it.
4. Only after explicit principal approval of the exact local rewrite scope, rehearse the following command in the disposable mirror:

   ```sh
   git filter-repo --path inventory/ --path docs/BRIEF.md --invert-paths --replace-text /path/to/private/replacements
   ```

   The replacement file uses filter-repo's replacement-expression format. It is separate from the
   audit's newline-delimited literal file. Keep both files outside the repository and review them
   locally. The command above is a proposed operator step; no script here executes it.
5. Audit the result with the same private literals. The removal candidates must have zero historical
   blobs. Every confirmed private literal must have zero matches in `all_objects_literals`;
   per-target zero counts alone are insufficient. No literal file means no literal coverage. Inspect the rewritten versions of
   the two retained documents locally. Re-run the source and documentation checks from the rewritten
   default branch. Compare intended branch/tag coverage and confirm only approved text/path changes.
6. Obtain independent review of the count-only before/after evidence, replacement scope, branch/tag
   coverage and source checks. A clean pattern report alone cannot approve publication.

## Final approval and cutover

Only the principal can grant action-time approval of the exact reviewed mirror and remote ref
replacement. Independent technical review and formal GitHub approval are evidence, never
principal cutover authorization. Only the principal can authorize any visibility change;
this work leaves the repository public. Request that separate final approval. Name
which branches and tags will be replaced and how collaborators will re-clone. Rehearsal approval
is not remote-push or visibility-change approval. Do not combine the rewrite with unrelated work.

After that approval, the operator replaces the approved remote refs, checks remote branch/tag heads
against the reviewed mirror and has all collaborators re-clone. Do not merge old branches back in;
that restores the removed history. Preserve the private backup offline for recovery only.

Use a private old/new ref manifest, explicit per-ref leases and an atomic approved update;
never use a blanket mirror push or delete refs implicitly. Re-inventory remote heads and tags
immediately before cutover, abort on any new, removed or changed ref, and repeat rehearsal
against the new snapshot. Preserve contributor work; agree the write pause and re-clone plan
through separately authorized coordination. Keep rollback refs and backup offline.

Git hosting can retain pull-request refs and cached objects after a force push. Confirm the hosting
provider's sensitive-data removal process and retained-ref behavior before any visibility change.
GitHub `refs/pull/*` are read-only and cannot be replaced by a normal push. Direct-SHA
caches, forks and other clones can retain history. GitHub Support assistance is conditional
on their sensitive-data criteria, not guaranteed for these categories; contacting Support
or fork owners requires separate authorization. Follow the [official GitHub guidance](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).
Track those checks and every known fork/copy separately. Issue #49 remains open until the remote
rewrite, retained-copy review and independent verification are complete. The audit/runbook draft
covers preparation only.
