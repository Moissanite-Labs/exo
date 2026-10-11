# Clean Writing — short form

## Mandate
Rule (one line): say what you did / found / need, then stop. Delete posture sentences.
Three closing jobs never do: (1) flattery / verdict ("right shape", "right design"); (2) restate point as conclusion; (3) disclaim message ("not a request", "just evidence").
Tells to remove: em dashes stitching clauses; signposts ("Notably","Importantly","At its core"); negative parallelisms ("not just X, it is Y"); rule-of-three over two; hyphen-pair inflation; generic positive conclusions; reassurance kickers ("and that's okay"); verdict phrases.
Don't restate what the thread/page already makes obvious. A PR/issue comment must carry only genuinely new info: if the page already shows a PR is closed and can't be reopened, don't narrate the reopen mechanics. Cut context another post on the page already supplied.
Format for scanning, not for continuous reading. Long-form text to a human (PR bodies, client deliverables) must be broken into sections with headers, bullets, or tables — a dense wall of prose reads as uninviting regardless of content quality. Use subheadings for each distinct feature, a comparison table for guarantees/options, code blocks for commands, and let each heading carry one idea.
Apply markdown bodies with `--body-file`, never by passing a shell/JSON-escaped string to `gh --body`. A string of literal `\n` renders as one unbroken wall with `##`, `|` and pipes visible as raw text — always `gh pr edit N --body-file file.md` and verify afterwards (fetch back, assert zero literal `\n`, headers/fences/table lines present).
Full reference (34 patterns): the humanizer skill at skills/creative/humanizer/SKILL.md in the maintainer tree, plus this skill (`clean-writing`).
Before posting >1 line to any human (user, agent, external): delete closing paragraph; delete flattery; delete restated point. If plain and complete, done.
