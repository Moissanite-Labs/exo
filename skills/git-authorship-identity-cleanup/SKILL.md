---
name: git-authorship-identity-cleanup
description: "Use when internal names leak into public git commits."
version: 1.1.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [git, GitHub, History-Rewrite, Identity, Privacy, CI]
    related_skills: [github-pr-workflow, github-repo-management]
---

# Public commit identity cleanup

Removing an internal agent/tool name (or a private mail domain) from a public repository's commit metadata:
rewrite the offending commits, prove the rewrite changed nothing but identity, and install a guard so it cannot
recur. Work in an isolated mirror; never in a contributor's dirty checkout.

## 1. Establish the live state by read-back, not by report

- `git clone --mirror <url> tmp.git` — the mirror includes `refs/pull/*`, which a normal clone hides and which is
  usually where the *unfixable* copies live.
- Scan every reachable commit's author name/email, committer name/email, subject **and full message** for the
  offending strings and any `Co-authored-by:` trailer. One script, run before and after (same script, so the two
  numbers are comparable).
- Read the offending commits back through the API (`/repos/O/R/commits/<sha>`): it reports the identity GitHub
  *displays*, which can differ from the local view you expected.
- Record which refs contain the offending objects (`git merge-base --is-ancestor <bad> <ref>` per ref). Those refs
  define the blast radius: an offending commit is reachable from every branch based on it.

## 2. Rewrite with plumbing, not rebase

`git rebase` re-stamps committer dates and `filter-branch` is slow and blunt. When the offending set is small
(a handful of commits), rebuild exactly those objects:

- Read `git cat-file commit <sha>`, split header/message at the blank line, and rewrite only the `author`,
  `committer` and `parent` lines. Replace the name/email but keep the ` <epoch> <tz>` tail verbatim.
- Write with `git hash-object -t commit -w --stdin`. Rebuild in dependency order so each commit's new parent is
  already known; rebuild descendants too, even when their own identity is fine (their parent SHA moved).
- Drop `gpgsig` headers (a rewritten commit cannot keep a valid signature) and say so in the receipt.
- Preserve `author` **and** `committer` bytes on cascade commits; change only what is offending, and match how a
  previous repair of the same repo left its commits — consistency beats minimalism for the reviewer.

## 3. Prove equivalence per commit, mechanically

For every old/new pair, assert and print PASS/FAIL: tree hash equal; message bytes equal by sha256; author date+tz
and committer date+tz equal; parent remap equals the expected mapping; commit-local `git diff --binary` identical;
`git patch-id --stable` identical; header-line diff contains **only** author/committer/parent lines.
Then a whole-range diff (`<root-before-bad>..<new tip>` vs the old equivalent) must be byte-identical, and the
`%H %s` list of the branch must differ in exactly the rewritten SHAs — no subject drift.

## 4. Push with lease, read back, then re-audit all refs

- `git push origin <new>:refs/heads/<b> --force-with-lease=refs/heads/<b>:<old>` — always the exact expected head.
  On protected branches the remote prints which rules were bypassed; keep that output as receipt evidence.
- A bare mirror has `remote.origin.mirror=true`, so a plain `git push origin <sha>:refs/heads/main` fails with
  "--mirror can't be combined with refspecs": use `git -c remote.origin.mirror=false push …`.
- Rewriting a branch head is what removes the identity from the *branch* view; a branch whose own commits are
  clean still needs rewriting when it is based on a rewritten commit, because the branch ref makes the old
  commits reachable again.
- Re-clone fresh and re-run the scan. Report the residual by ref class, not as one number: `refs/heads/*` must be
  clean, and anything left must be attributed to `refs/pull/*` or a stored PR record.

## 5. Residuals: `refs/pull/*` and stored records are GitHub-managed

- `refs/pull/N/head` is pinned for a closed PR (rewriting the branch does not move it); `refs/pull/N/merge` is
  regenerated only when the head branch is next pushed; PR records keep `head_sha`/`merge_commit_sha` forever;
  GitHub serves unreferenced objects by SHA (`git fetch --depth=1 <url> <sha>` succeeds).
- None of that is reachable by a repo-side action. Only a GitHub Support purge clears it. Do not attempt to write
  or delete `refs/pull/*`; document it and hand the decision to the user.
- Rewriting a branch does remove the identity from the branch view, and it can also drop the old objects from
  other PRs' merge refs for free (GitHub recomputes them for the PRs whose branch moved).

## 6. Squash merges take the author name from the account profile

A leak that looks impossible — the branch commits are clean, yet `main` has an internal name — is usually
GitHub's squash attribution: the author name comes from the merging account's **profile display name** at merge
time (falling back to the login when it is empty), and GitHub adds `Co-authored-by: <author>` when the squasher
differs from the commit author. Evidence to cite: sibling squash commits from the same account whose author name
matches the current profile state. So the durable control is the profile name plus a server-side check, not local
git config.

## 7. The guard: positive rules in code, names out of the repository

- Ship `.githooks/{commit-msg,pre-push}` (opt-in per clone via `git config core.hooksPath .githooks`) plus a CI job.
  Hooks are skippable with `--no-verify`, so the CI job is the half with teeth — and it runs on the pull-request
  merge commit, which is the identity a squash would publish.
- Never commit the internal name list: read it from an env var, a `<git-dir>/` file (untracked by construction), or
  a repository Actions **variable** (verify it is not anonymously readable: `GET /repos/O/R/actions/variables`
  should be 401). Only generic rules belong in code (private mail domain, empty address, bad trailer shape).
- The `commit-msg` hook sees `GIT_AUTHOR_NAME/EMAIL` in its environment, so it catches `git commit --author=…`
  before the object exists. The `pre-push` hook gets `<local ref> <local sha> <remote ref> <remote sha>` on stdin;
  use `git merge-base --is-ancestor` to decide between a range and a `--not --remotes` fallback.

## Rebuild procedure — re-attributing a branch without changing content

Use when commits already carry the wrong identity (an internal name, or the user's personal identity) on an
unreviewed branch. Proven end-to-end on a 10-commit branch with one merge commit.

1. **Map identities explicitly and refuse to guess.** `{old_email: (New Name, new@email)}`. Include the target
   identities themselves in the map so a second run is idempotent. Abort with a loud error on any author email that
   is not in the map — a silent default is how a personal identity survives a "cleanup".
2. **Rebuild with `git commit-tree`, never `filter-branch`.** `filter-branch` linearizes merge commits and can drop
   a side branch from the log. For each commit in `git rev-list --topo-order --reverse <base>..<branch>`: read tree,
   parents, `%B`, `%aD`, `%cD`; remap each parent through the new-SHA dict (parents outside the range pass through);
   then `GIT_AUTHOR_NAME/EMAIL`, `GIT_COMMITTER_NAME/EMAIL`, `GIT_AUTHOR_DATE`, `GIT_COMMITTER_DATE` + `git commit-tree
   <tree> -p <parent>… -m <msg>`. `--topo-order --reverse` guarantees parents are rewritten before children. For a
   merge, pass both parents in order (`-p p1 -p p2`) and it stays a merge.
3. **Prove the content is untouched before pushing.** Assert: new tip tree == old tip tree; `git diff <old> <new>`
   empty; `git diff --stat <base> <new>` == the old stat line; merge commits still present
   (`git rev-list --merges <base>..<new>` non-empty). Screenshot-grade evidence, not a claim.
4. **Sweep the messages too.** An internal name in a commit subject leaks exactly as an email does. Run the
   substitution over `%B` in the same pass, then assert `git log --format=%B <range>` holds no denylisted token.
5. **Assert the end state, not just the change.** `git log --format='%ae%n%ce' <range>` must contain only the allowed
   addresses.
6. **Push with `--force-with-lease`, and only on an unreviewed branch.** Report the old and new head SHAs, the
   count of rewrites, and the fact that CI re-ran. An identity rewrite re-triggers every workflow on those paths.

## Pitfalls

- **`git log <sha>` walks ancestors; `git rev-list <sha>` does not.** Passing a materialized SHA list to `git log`
  silently scans the whole history and produces spectacular false positives. Pass the *range* (`"$@"`) to `git log`,
  or `--no-walk`, and keep `git rev-list --count` for the count.
- **`IFS=$'\001'` does not split in every `/bin/sh`** (bash 3.2 on macOS, for one). Parse fields with parameter
  expansion (`v=${line%%"$SOH"*}; rest=${line#*"$SOH"}`), which is portable and needs no `set -f` dance.
- **Match denylist tokens on token boundaries, never as substrings.** A substring rule flags the owner's own domain
  when an internal name is a prefix of it (`acme` vs `acmecorp.com`). Test that false-positive case explicitly.
- **A finding appended inside a `while` loop fed by a pipe is lost in the subshell.** Accumulate findings in a temp
  file and check its size after the loop; a helper must use `if …; then return 0; fi`, not `cmd && return 0`.
- **Amend the commit you mean to amend.** A stray test commit (`--author=<bad>`) left at HEAD gets picked up by the
  next `--amend`, and the amend inherits *its* author. Re-check `git log --oneline origin/main..HEAD` before push.
- **Do not verify with the mirror you rewrote in.** A push from a mirror updates the mirror's own
  `refs/heads/main`, so the "before" state is gone — take `git bundle create --all` first and compare against that.
- **An identity rewrite re-triggers path-filtered workflows** (Pages deploys, package releases) because the
  re-pushed commits carry the same paths. Prove no content changed (tree hash of the filtered directory old vs
  new) and say plainly that the deploy ran.
