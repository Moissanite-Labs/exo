# Verify the feature merges tracked in #741

A merged PR can target a feature branch, or its merge can omit source changes. An ancestry check
alone cannot establish that the implementation exists on today's main. This read-only audit checks
the five PRs named in #741: #508, #510, #630, #686 and #723.

```sh
python scripts/audit_feature_landings.py --repo /path/to/clone --base origin/main --live
```

`--live` reads GitHub PR metadata only. It does not fetch objects, change refs, merge, push, run source
code or inspect file contents. The repository must already have the relevant source and merge
objects. `--snapshot FILE` accepts a saved response from the same GraphQL query, exposed as `QUERY`
in the script. The report pins the compared base, source head and merge commit for every PR.

For each source-listed path, the audit compares Git object kind, mode and hash against the selected
base. It distinguishes missing paths, exact matches, changed paths and source deletions. It checks
merge ancestry separately. A merge committed with the `ours` strategy, for example, can be an
ancestor while its source file is missing; a cherry-picked restoration can match without ancestry.
Missing objects, shallow history and truncated PR-file pagination make the report incomplete and
produce exit 2. Query/access/shape failures also exit 2 without echoing server details or local paths.

Exit 0 means the metadata/path audit finished, not that every feature is integrated. Changed files
can reflect later approved work. Missing paths can reflect an intentional removal. Deletions and
behavioral composition need review. Even exact listed-path matches do not establish full feature
behavior, independent approval, desktop integration or native acceptance. The script changes no
issue status and never claims that #741 is fully resolved.

## Review and repair

1. Refresh the intended base explicitly, save its exact commit and run the audit against that ref.
2. For every missing/changed source path, inspect the source PR's final reviewed head and its target
   branch. Find the active author/integration branch before proposing a restoration.
3. Preserve original authorship and any newer approved behavior. Do not copy whole old files over
   current source merely because their hashes differ.
4. Prepare a real restoration/composition PR for missing behavior, run its affected suites and obtain
   exact-head independent review. A feature-branch merge and a default-branch landing remain separate.
5. Re-run this audit and behavioral checks on the final combined tree. Keep #741 open until the full
   source inventory and intended behavior are accounted for.

A dated source-path report must retain the exact five IDs above. Refresh the base and save the
result when reviewing the restoration queue; source presence and later path differences remain
review signals, not blanket lost-feature or restoration claims. The source implementations remain
with their owners; this draft supplies the repeatable remaining-scope audit.
