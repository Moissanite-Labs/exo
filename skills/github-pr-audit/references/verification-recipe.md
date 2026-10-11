# Pre-merge GitHub PR/Issue Audit — command recipe

Copy-paste sequence. `O/R` = `NousResearch/hermes-agent` (or the target repo).
`N` = PR/issue number. Run from the repo checkout.

## 0. Auth (needed once per session)
```
source "${HERMES_HOME:-$HOME/.hermes}/skills/github/github-auth/scripts/gh-env.sh" 2>/dev/null
gh auth status
```

## 1. Live state — branch + main..HEAD diff
```
git branch --show-current
git status
git diff main...HEAD --stat
git diff main...HEAD -- tui_gateway/server.py        # or the changed file
gh pr diff N --repo O/R --name-only                  # remote truth
gh pr view N --repo O/R                              # title/body/labels
```

## 2. Bug premise vs main
```
# read the function on main
git show main:tui_gateway/server.py | sed -n '/^def _runtime_model_config/,/return config/p'
# OR isolated main checkout
git worktree add -q /tmp/hm-main main
git worktree remove /tmp/hm-main --force
```

## 3. Run tests in the repo venv
```
# python may be missing from PATH — use the venv
./venv/bin/python -m pytest tests/tui_gateway/test_custom_provider_session_persistence.py -q
./venv/bin/python -m pytest tests/cli/test_resume_model_restore.py -q
./venv/bin/python -m pytest tests/state/ -q
# node IDs are CLASS-qualified:
./venv/bin/python -m pytest "tests/tui_gateway/test_gui_surface_toolsets.py::TestDesktopUiToolset::test_holds_exactly_the_gui_affordances" -q
```

## 4. Verify PR-body evidence claims
Run the exact test the body cites, on main AND branch. If false/orthogonal,
edit the body (own-account PR only):
```
gh pr edit N --repo O/R --body-file /tmp/pr_body_patched.md
```

## 5. Issue scoping nits
```
gh issue comment M --repo O/R --body-file /tmp/issue_comment.md
```

## 6. Post QA pass (self-approve is BLOCKED by GitHub)
```
# gh pr review N --repo O/R --approve  -> FAILS: "Review Can not approve your own pull request"
gh pr comment N --repo O/R --body-file /tmp/review_comment.md
```

## Patch a PR-body claim safely (avoid shell quoting)
Build the patched body in Python (hermes_tools.terminal), write to
`/tmp/pr_body_patched.md`, then `gh pr edit --body-file`. Confirm with
`gh pr view N --repo O/R --json body -q .body | grep -c "claim"`.
