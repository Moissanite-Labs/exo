---
name: github-draft-review-html
description: "Use when a GitHub issue/PR draft must be shown for review. Render it as a self-contained GitHub-dark single-file HTML."
version: 1.4.0
author: Hermes Agent (Protean Team), generalized for Exo
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [GitHub, Draft, Review, HTML, Markdown, SOP]
    related_skills: [github-pr-audit, clean-writing, check-prose, operating-doctrine, charts-in-posts]
---

# GitHub Draft Review — HTML SOP

## Purpose

When a team member (any role) or the principal asks for a draft that will be posted
on GitHub (issue body, PR body, release note, comment) and a human must review it first, the draft
must be shown as a **self-contained, GitHub-dark-styled single-file HTML page** in the browser —
not raw markdown pasted in chat. Reviewing the exact rendered artifact prevents whitespace,
no-light-flash and formatting surprises at posting time.

This is the standing standard (operator directive): *"every time a draft of something is to be
posted on GitHub so I can review it, make it like this — from any team member. Make it an SOP."*

## When to use

- Any GitHub-posted text is drafted and needs operator review before `gh issue create` / `gh pr create`.
- The user explicitly asks for a draft to review.

## Role ownership (mandate)

- **External PR and issue review wording:** Researcher owns the prose. Orchestrator routes the work and verifies the resulting artifact. Reviewer checks the evidence and style. The operator reviews the rendered HTML before posting.
- Project-specific ownership matrices may assign presentation or UX to Designer; those assignments govern presentation, not review prose, unless the operator explicitly redirects ownership.
- The renderer script and page structure below are Builder-owned once created; improvements go through Builder.
- A placeholder that only a post-push fact can fill (a head SHA, a diff count) is filled by the orchestrator after the push, before posting, and the substituted source is re-gated. Render substituted text to a `*-final.html` beside the approved file so the approved artifact is not overwritten.

## Deliverable

One `.html` file per draft, HTML ONLY, placed in a new dated batch folder under the drafts root
(`--drafts-root`, default `$EXO_DRAFTS_ROOT`). A principal may name a required destination per
repository in the principal store (`policies/`); that destination overrides the default. Do not leave the review copy only in scratch, a profile cache, or chat markdown.

Use the templates in this skill's `templates/` folder as the reference for the final
GitHub-style rendered draft. Use the canonical `draft_pipeline.py` to create the HTML; do
not hand-build or paste raw Markdown as the review artifact. The final review copy is the
`.html` file at the requested local destination. The Markdown source may remain in scratch
for pipeline use, but is not a substitute for delivering the rendered HTML. Files are named
`<slug>.html`. Create one dated batch folder per review delivery. The batch label uses the
local delivery time unless the operator specifies another timezone. Text in the rendered
body must preserve the source Markdown prose, with no added or dropped content. Read the
saved HTML back and verify it exists before reporting its path.

For other destinations or repositories, follow any explicit operator destination first;
otherwise use the repo-local workflow and the optional shared-folder flow below. Verify each required
folder and file by read-back before reporting delivery.

## Build (use Builder's scaffolding; never rebuild the page by hand)

The canonical scaffolding format is Builder's reusable pipeline:

`<skills-root>/github-draft-review-html/scripts/draft_pipeline.py`

It calls the canonical renderer:

`<skills-root>/github-draft-review-html/scripts/render_draft_html.py`

Every draft must be produced by `draft_pipeline.py`. Do not hand-copy an old HTML
file, write a new HTML wrapper, reconstruct the GitHub chrome, or run the renderer
alone for a normal draft. The pipeline owns the gate order, output path, baseline
handling, fidelity checks, palette checks, self-containment checks, and change banner.
Use the renderer directly only when maintaining or testing the scaffolding itself.

## Build command

Use the scaffolding pipeline once per draft:

    venv/bin/python <skills-root>/github-draft-review-html/scripts/draft_pipeline.py \
      --src <draft.md> --slug <slug> --tab "pull request draft" \
      --title "<draft title>" --owners {{github_owner}} \
      --repo "<owner>/<repo>" \
      --drafts-root <drafts-root> --batch "YYYY-MM-DD - HHhMMm" \
      --verify-against "origin/main:<path>" --open

On every re-render, visibly state and highlight the changes. Pass `--diff-base <previous.md>`
when the previous markdown exists, or `--from-html-baseline <previous.html>` when only the
previous HTML exists. The renderer must include (1) a prominent added/removed count banner,
(2) a readable change-detail panel, and (3) green in-place marks on added rendered blocks.
Never claim "no change" without a baseline. For an unchanged draft, show an explicit
"No content change" banner. Preserve the approved markdown text exactly; highlighting is a
view-layer annotation, not a rewrite. Keep the previous render until the new render has been
compared and the fidelity gate passes.

If either canonical script is missing or a member is on a profile without it, stop and report the missing scaffolding. Do not reconstruct a different renderer or page format. Restore or repair the canonical Builder-built scaffolding first.

## Visuals in the draft (tables, Mermaid, images)

If the draft carries a table, Mermaid diagram, or image, build it per the
`charts-in-posts` skill, then render and gate via this pipeline unchanged.

## Visual value gate (automatic, advisory)

Every pipeline run passes the draft through
`charts-in-posts/scripts/visual-value-gate.py` right after the prose
gate. The gate is conservative by design: it fires only when the
content genuinely benefits from a visual (3+ rows of same-field
numbers, a 3+ step sequence, a before/after or option comparison, a
data flow, 2+ code blocks presenting alternatives). NOT-WARRANTED is
the common case, and prose staying prose is a pass, not a failure.

A WARRANTED verdict is a warning, never a hard fail: the pipeline
prints an `ADVISORY visual missing` note and continues. The suggested
visual type is a recommendation, not a mandate. Authors who disagree
pass `--skip-visual-gate`. When the draft already holds a visual, the
gate reports VALIDATION instead of suggesting one.

Mermaid fences are validated automatically: whenever the draft holds
a ```mermaid fence, the pipeline runs `check-mermaid.py` and fails if
it exits non-zero. Never hand-run the gates around this sequence.

## One command per draft (scaffolding — use it, do not hand-run the gates)

    venv/bin/python <skills-root>/github-draft-review-html/scripts/draft_pipeline.py \
      --src <draft.md> --slug <slug> --tab "pull request draft" \
      --title "<draft title>" --owners {{github_owner}} \
      --verify-against "origin/main:<path>" --open

One pass runs every gate in SOP order: prose (check-prose.py) -> visual value (advisory)
+ mermaid fences (check-mermaid.py, only when a ```mermaid fence is present) ->
identifier sweep -> verbatim
fences (`--verify-against REF:PATH` or a file path, repeatable) -> oversight blockquote -> render
-> text fidelity -> palette -> self-containment -> chrome -> change banner. It writes
`<drafts-root>/<batch>/<slug>.html`, keeps the previous render as `<slug>.prev.html` and feeds it
to the renderer as the baseline so every re-render carries a change banner, and exits non-zero if
any gate fails.

Start from a template instead of a blank file: `templates/pr-draft.md` (the upstream
PULL_REQUEST_TEMPLATE.md sections) or `templates/issue-draft.md` (the bug_report.yml fields).
The templates plus the pipeline are the scaffolding: never rebuild the gate sequence by hand, and
never retype a quoted source block (slice it; the pipeline proves byte-identity).

## Attribution (external vs internal)

The header owner chain is chrome, not posted body text, and it must name whoever is actually
posting:

- **External drafts** (repos outside the protean projects): pass `--owners <poster handle>`. The
  handle is the principal's posting account (`{{github_owner}}`, set in the principal store), and
  the commit author is that account's noreply address. Never put internal protean team names
  (the renderer default chain) or agent handles on an external post.
- **Verify the commit identity before pushing** — `git log -1 --format='%an <%ae> | %cn <%ce>'`.
  A commit created by an agent carries that agent's identity, and a leaked agent name in a public
  commit cannot be undone by editing the PR. Amend it to the operator first:
  `GIT_COMMITTER_NAME=<handle> GIT_COMMITTER_EMAIL=<noreply> git commit --amend --no-edit --author="<handle> <noreply>"`.
- **Protean-internal drafts**: omit `--owners` so the default protean chain applies.
- The pipeline's identifier gate already refuses profile names, agent names, absolute home paths,
  tmp paths, hostnames and emails in the body. Use `--allow-identifier <literal>` only for a value
  the post genuinely needs.

## Page spec (GitHub dark)

Single self-contained file. Inline `<style>` only; no CDN/webfont/external fetch at view time;
`<meta name="color-scheme" content="dark">` in `<head>`; root/body `background:#0d1117` so it
opens dark with no light flash. 768px centered reading column.

Tokens (exact):
  page/gutter background  #0d1117   gutter border        #30363d
  base text               #c9d1d9   headings             #f0f6fc
  muted                    #8b949e   accent links         #58a6ff
  inline/fence background #161b22   fence border         #30363d
  table header            #161b22   table borders        #30363d
  zebra alt row           #161b22 (or #0f141b)
  blockquote border       #30363d   blockquote text      #8b949e
  DRAFT badge text        #f85149   DRAFT badge bg       #3d1d20, border #f85149
  fence language tab      #8b949e   code content         #c9d1d9

Structure:
- Header gutter: `#010409`/`#0d1117`, repo path `NousResearch/hermes-agent`, an open tab labelled
  `issue draft` or `pull request draft`, and a `DRAFT — NOT POSTED` badge with the owner chain
  (role names, or `$EXO_DRAFT_OWNERS`) and state line (awaiting review).
- Body: full draft rendered GitHub-style: headings, python + text fences with a language tab and
  syntax color on python, tables (full width, header emphasis, zebra), blockquote, trailing
  `> Automated posting by agentic team with human oversight.` blockquote exactly once as last line.
- `#12345` issue refs stay PLAIN TEXT (never auto-link).

## Verification gates (run before presenting)

1. `check-prose.py <draft.md>` exits 0; identifier sweep clean (no profile names, `/Users/` paths,
   hostnames, agent names) on the markdown that will be posted.
2. Text fidelity: extract text of source vs rendered HTML; 0 lines missing, token-diff PASS, fences
   byte-identical, no added links, self-contained (no `http` fetch in the HTML for assets).
3. Palette present: grep the seven core tokens + `color-scheme` from the table above; none missing.
4. Only after the operator approves the HTML may anyone push/`gh issue create`/`gh pr create`.

## Shared-folder delivery flow (optional, before posting)

Use this only when the principal store names a shared review folder (`policies/`).

- Create or reuse the drafts folder the principal named.
- Create a new batch subfolder named `YYYY-MM-DD - HHhMMm` for each delivery. Do not mix later drafts into an earlier batch.
- Upload every pending review HTML in the batch. Exclude `.prev.html`, source markdown, logs, and drafts already posted or withdrawn.
- Read back the batch folder and file list from the shared folder. Report the exact folder link and each HTML link.
- Keep the batch available until the owner finalizes all drafts and authorizes posting.

## Posting flow (on approval)

- Push the branch to the fork, create the issue, then the PR (`gh ... --body-file <draft.md>`).
- Read the posted body back with `gh <obj> view ... --json body -q .body`, diff against the
  approved `<draft.md>`; only a trailing newline difference is acceptable.
- **Never attempt to add labels to an external repo** (operator directive, 2026-09-23). The fork
  account has no triage permission upstream, so `gh issue edit --add-label` /
  `gh pr edit --add-label` always fails with `AddLabelsToLabelable` and burns a call for nothing.
  Post unlabeled and record the suggested labels (real ones only, from `gh label list`) in the
  posting journal for maintainers/triage, or the operator adds them.
- Journal the posting as `POSTED.md` in the batch folder (that file IS the propagation journal):
  one row per surface (issue, PR, any later edit) with the UTC timestamp, the URL, and the md5 of
  the body file posted; plus the branch/commit SHA, the fork read-back, the body-diff result, the
  label outcome, and the CI state.

## Pitfalls

- **Never retype quoted source text. Extract it programmatically.** A model writing a `Before:` block from
  memory silently normalises characters. Observed twice in one draft: the em dash `U+2014` was rewritten as
  ` : ` inside a quoted AGENTS.md line, which makes the quote non-verbatim and fails a public PR review.
  Build every quoted block by slicing the source file in Python and inserting the slice into the draft,
  then assert equality before rendering:
  `assert block in pathlib.Path(<source>).read_text(encoding="utf-8")`. Retyping is the defect path, not
  a style preference.
- **Diff quoted blocks byte-for-byte after every render, and check that expected special characters survive
  at all.** A quote that is the same length as its source with the first divergence at a single index is
  the signature of a punctuation substitution. Confirm the em dash count in the draft matches the source.
- Do not re-emit the draft markdown in chat as the "review copy"; always the HTML.
- Do not make the page fetch CDN assets; a reviewer opening a local path offline must see the full
  render.
- Do not change draft text while rendering; the HTML is a view, not a rewrite. Text edits go back
  to the researcher role, then re-render.
## Principal-specific settings

This skill ships with generic defaults. Names of the principal's own agents, accounts, hosts and
drafts destinations live in the principal store (`policies/` and `agents/`), not here. The
pipeline reads `EXO_DRAFTS_ROOT`, `EXO_INTERNAL_NAMES` (a file of names the identifier gate
must refuse), `EXO_DRAFT_OWNERS` and `EXO_DRAFT_STATE`.
