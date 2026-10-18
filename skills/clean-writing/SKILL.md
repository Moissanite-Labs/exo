---
name: clean-writing
description: "Use when a human reads your writing. Cut filler."
version: 1.8.0
author: Protean Team, generalized for Exo
license: MIT
platforms: [linux, macos, windows]
---

# Clean Writing

> The principal's own style profile (extra banned phrases, internal names that must never be
> published, paragraph limits) lives in the principal store at `policies/prose.json` and
> `policies/internal-names.txt`. `scripts/check-prose.py` loads them when present. This skill
> ships the generic rules only.

A short rule for anything a reader outside your head sees: PR comments, issue replies, chat to the user, client deliverables, release notes, review notes. The rule is simple: say what you did, what you found, what you need. Then stop.

## Plain-language floor (audience test)

Some writing faces readers whose first language is not English, or who have zero context on the project — a README, an About line, a public landing page. Before publishing, test against this floor:

- **Say what the thing IS and DOES in the first three lines.** What it is, what it does, who it is for. If a reader cannot answer "what is this project?" after those lines, everything after is wasted.
- **Ban jargon and lesser-known words** when a plain word exists: "orchestration choreography" → "how the team works together"; "durable state" → "saved to disk"; "provenance" → "where it came from"; "invariant" → "the rule". A technical term stays only when no plain substitute carries the meaning and the audience is technical.
- **Short sentences.** One idea per sentence. Prefer a period over an em dash or a semicolon.
- **Concrete over abstract.** "One agent plans, one builds, one checks" beats "supervised multi-agent coordination".
- **The stranger test:** could someone who has never heard of this project, reading in a second language, tell a friend what it does after reading the top of the page? If not, rewrite before adding anything else.

### Hyphen compounds

Every time you connect two words with a dash, ask whether the compound is jargon. "frozen-manifest build example", "paid-pack material", "files-as-state", "third-party-tool audit", "hold-for-review rule" — all of these were written in good faith and all of them failed the stranger test. Real file paths and product names (knowledge-base-ingestion, Apache-2.0) are exempt; invented concept compounds are not. If the compound needs a pause to decode, unpack it: "a skill that saves all work state to disk", not "save-state-to-disk skill".

### Method names are not jargon — but they still need one plain sentence

Keep the specific named technique (Erlang/OTP supervision trees, MoE, YAML, Apache-2.0, Redact/Gist/Title). The discipline is not to strip identifiers — it is to introduce each one with a plain sentence that carries the meaning on its own, so the name becomes a label rather than a dependency: "Workers do the work; the lead watches and restarts them when needed, with a limit on how many restarts are allowed. Modeled on Erlang/OTP supervision trees."

## Approval boundary for evolving drafts

Explicit approval applies to the exact text and exact target that was shown or agreed. If a live thread changes, or the draft is materially rewritten during reconciliation, stop and obtain approval for the revised text before posting. This is especially important on busy maintainer threads: a concise review can still sound condescending if its opener frames the work or assigns a verdict to the project authors. Do not treat approval to post an earlier draft as approval for a later rewrite.

## The core rule

Before you post a piece of writing, read every sentence and ask: does this carry information a reader needs, or is it posture? If removing the sentence changes nothing, delete it.

Then re-read the last paragraph you wrote. A closing paragraph is the most common place for filler to collect, because it feels like the writing needs a graceful exit. It does not. The point you already made is the ending.

## Corrections should be short

When a public post was malformed, correct it in place with one plain sentence that says what happened and what the reader should use instead. Do not explain the internal workflow, repeat the leaked value, or defend the mistake. Prefer human wording such as: `Posted in error (carried an unresolved file path instead of the review body). The full review follows below.` Then provide the review. A correction is not a second review and does not need a summary of the posting process.

## Evidence-led technical writing

For reviews, triage notes, and status comments, make the evidence register compact and countable:

- Lead with the scope and impact count. Define the unit being counted, and count each item once at its highest impact.
- Separate fixes already included from requests, wishlist items, and unresolved findings. Do not let a later or related change appear to be part of the current one.
- Map each item to its exact artifact, commit, test, and status. A table is preferred when rows have the same fields.
- Label the evidence boundary: base or head, commit or composition, test environment, and measurement time when relevant.
- Treat missing evidence as a limit on the claim, not as support for it. Keep unverified follow-ups separate from proven findings.
- State overlaps and independent coverage explicitly. Do not count or describe the same fix twice.
- If a later fact changes an earlier public claim, edit or remove the stale text. Do not stack a correction below it.

The result should let a reviewer answer: what is included, how many items exist, what proves each item, and what remains unverified.

## Automated posts say so

When an agent writes and posts text itself (a PR body, a review, an issue or PR comment), it closes with one short line saying a machine wrote it:

> Posted by agents, reviewed by human.

- Last line, as a blockquote. GitHub renders it muted, so it reads as a footnote rather than a claim in the argument.
- No team or product name, no apology, no hedging, no "I am only an AI", no inviting a human to override it. It is a fact sentence.
- Once per thread. A comment on your own PR or issue does not repeat it, because the body sits on the same page. Read it before posting: `gh pr view N --repo O/R --json body --jq '.body' | grep -c 'Posted by agents'`. Non-zero means the body already carries the line, so the comment drops it; zero means add it.
- It never lowers the evidence bar and never softens a claim. Say what you did, what you found, what you need — the same as any other post.

## The three closing jobs you should never do

1. Do not flatter the reader or the thing you are discussing. "This is the right shape" and "reads as the right design" are compliments nobody asked for, and they say nothing about your work.
2. Do not restate the point you just made, dressed up as a conclusion. If the reader needed the point twice, the first sentence was weak. Make it once, make it clear.
3. Do not disclaim your own message. "Not a request for changes" and "just real-world evidence" read as apology for having said something. Commit to the point or cut it.
4. Do not append a redundant closing sentence that repeats a link's purpose. If a section already says the rule and links the ruling, omit a final "See #..." sentence; the link is enough.

## Tells readers now flag as AI writing

These carry no information and are becoming recognized giveaways. Remove them on sight:

- Em dashes used to stitch clauses where a period or comma is clearer.
- Calling something "the right shape", "the right design", "exactly what is needed", or otherwise assigning it a verdict the reader can form themselves.
- Negative parallelisms: "not just X, it is Y", "not merely a statement".
- **Intensifier self-assurance.** "and it is exactly the salvage that check asked for", "precisely what
  was requested", "exactly as asked". A copula plus `exactly` reads as the agent vouching for its own
  compliance rather than stating the fact, and a reader edits it out. State the correspondence flatly, or
  compress it into a parenthesis: "Only the test file changed (the salvage that check asked for)."
  Verbatim operator edit to a posted PR comment.
- **The brief's voice leaks into the posted text.** That phrasing reached the comment because the dispatch
  brief instructed the writer to say the drift "is exactly the salvage that check requested". Emphasis
  written into a brief comes back as emphasis in the artifact. Write briefs as flat facts too: "state that
  the change is the salvage that check requested".
- Sentence-opening signposts: "Notably", "Importantly", "It is worth noting that", "At its core".
- Self-referential meta-framing: "I'd frame this differently", "I'd argue", "I would say" — phrases that preface a point with a verdict on how it is being made. State the point; drop the framing.
- Generic positive conclusions: "This is a step in the right direction", "exciting times ahead".
- Reassurance kickers: "And that is okay", "no shame in that".
- Rule-of-three catalogues where two would do.
- A stray leading `@` before a title or opening line. Real example: opening a GitHub issue comment with `@**Same bug, ...**` — the `@` is a leftover mention/reply artifact that reads as addressing someone, and it was surgically removed by the reader before posting was accepted. Never open an external post with a bare `@`.
- Hyphen-pair inflation: "high-quality, end-to-end, client-facing" when the words stand fine alone.
- Framing lines that announce the message instead of being it: "Two notes for this PR", "Note on mechanism, for completeness", "A few thoughts". Delete the frame; the first real sentence is the opener.
- **Announcing your own points or your own honesty.** "Two things worth stating plainly", "Findings first, because", "The honest gap I will not paper over", "I want to be upfront that", "One caveat worth flagging". A frame that promises the reader something is not the thing. Start with the fact. A limitation is a fact sentence too: "Not verified: live goal-barrier run", not a confession of integrity.
- **Prefaces that announce what you are about to say.** Cardinality frames ("Two integration notes.", "Three findings:", "One operational note:") and action frames ("Here is the draft:", "I will now render the HTML", "The results are below"). The list itself shows the count; the artifact itself is the announcement. Delete the frame and let the first real sentence or the deliverable open. This covers chat as well as posted text: never narrate the next action, just take it, and never narrate the points, just state them. Every such sentence costs the reader time and costs tokens while carrying zero information, so hunt them as inefficiency, not style. Enforced in check-prose.py by the cardinality-preface patterns.
- **Draft scaffolding in text that will be posted.** "DRAFT 1", "Option (a) vs (b)", "PLAN, pending your go" belong in the chat that asks for a decision, never in a PR body, issue, or comment. Once posted, the document must read as a finished statement.
- **Narrating your own search.** "I checked X and found", "after tracing", "it turns out that". State the result. The reader did not ask for the route you took.
- Verdicts and rankings the reader can form themselves: "the natural single base", "the cleanest of the four to keep", "the obvious choice". State the fact (which one covers more) and let the reader rank it.
- Empty closers and fragment flourishes: "none subsumes another", "the rest follows", "nothing else to add". If the list above already shows it, the sentence carries nothing.
- Process-hedge phrases: "worth settling", "worth pinning down", "something to consider". Say the thing or cut it.
- When a model writer exists, copy it. The repo maintainer's own PRs, or the client's earlier docs, beat any generic style rule — read three or four and match the shape (opening sentence, section order, how much they explain). The mechanics live in `github-pr-audit`.
- **Read his merged PRs before writing one.** `gh pr list --repo O/R --state merged --author <maintainer> --limit 4 --json number` then `gh pr view N --repo O/R --json body`. hermes-agent / teknium1 shape, verbatim from #109880, #109841, #109649: one sentence giving the new behaviour in plain words, blank line, then `## Root cause`, `## Changes` (`path::symbol` — what), `## Validation` as a before/after table, then plain lines for follow-ups. No opener that frames, no summary of what he is about to say, no first person. Match that register for bodies and comments on that repo.
- Predicting an outcome instead of stating the fact: "if this one lands", "once this ships", "when this merges". Write the present comparison — "this one covers both platforms; the other three cover one" — and let the reader draw the result. Past-tense "landed"/"shipped" as a fact is fine; the tell is the conditional forecast.

The humanizer skill (creative/humanizer) is the full reference, 34 patterns. Load it alongside this one when you need to scrub a longer piece.

## Run the sweep; do not eyeball it

These tells are invisible while you write them. Grep the text you are about to
post and treat every hit as a candidate, not a verdict:

```bash
sed -n '<start>,<end>p' new_text.md | grep -n '—'
sed -n '<start>,<end>p' new_text.md | grep -niE 'notably|importantly|worth noting|at its core|i would say|right shape|right design|step in the right direction|no shame|that is okay'
```

An em dash separating list items is fine; one stitching two clauses into a longer
sentence is the tell. Scope the sweep to the lines you added: existing text may
carry the same patterns and rewriting it is not the job. Then read the final
paragraph and delete it if it only restates the point.

### Make it a command, not a resolution

Eyeballing has failed in practice: a comment went out carrying five em dashes and a
self-referential opener while this skill sat unloaded. So the sweep is a blocking gate,
not a habit:

```bash
python3 scripts/check-prose.py <file-to-post>
```

Exit 1 means do not post. It flags em dashes, clause-stitching semicolons, every banned
phrase below, paragraphs over 420 characters, and internal identifiers, and it skips fenced
code so quoted material is not punished. Fix or justify each hit, post, then re-fetch the
published text and run it again: a clean local file proves nothing about the live comment.
A hit is a candidate, never a verdict.

**The gate does not cover outcome forecasts or close directives.** `check-prose.py` has no rule
matching "after this lands", "once this ships", "when this merges", or "should be closed", so a
clean result is not evidence they are absent: a draft reached review carrying a merge forecast and
a close directive while the gate reported clean. Grep those phrasings by hand on every draft and
treat that sweep as a required gate, not a habit.

Openers that arrived the hard way and are now banned: "My earlier note was too broad, and
this deserves precision." A polite retraction opener is still an opener, so state the
corrected fact instead. Also "Two receipts from our side stand on their own" and
"One operational note:".

## The worked example

A real case, verbatim, that a reader deleted as AI slop. The writing had already made its point in two paragraphs. This was appended on top:

> Not a request for changes to the branch. The four-axis + clamp + brake design reads as the right shape. Just real-world evidence that the persistence mechanism is not cosmetic: it is what actually keeps a team room running across updates.

Every sentence fails the rule. The first disclaims. The second flatters and adds a hyphenated-pair for no reason. The third restates the point it already made, then uses an em dash to gesture at significance. Nothing in it tells the reader anything new.

Cut version: nothing needed to be added at all. The comment was complete before this paragraph.

## Long documents a human has to read

A longer document is not a licence to spend more of the reader's time; it is more text to cut. Accept the reader's price: they are paying in minutes, and they will stop reading before they admit it. A PR body written for one ~30-line fix reached ~14k characters across body and comments, and cut to ~3.8k with nothing a reviewer needed removed — most of the saving was restated evidence and narration of the investigation.

- **Format for scanning, not continuous reading.** Headers, bullets, tables for comparisons, code blocks for commands, one idea per heading. A dense wall of prose reads as uninviting regardless of how good the content is.
- **Length defaults unless the content earns more:** a PR or issue body fits one screen; a comment is a few lines. First out: restated evidence, investigation narration, "here is why I looked at this", and hedging. Non-negotiable: repro commands, the root-cause line, test evidence and counts.
- **Cut prose, never the template.** Keep the repo's PR sections in order, every checklist box, code fences and links. Take the saving out of paragraphs; a short body that no longer matches the template reads as sloppiness, not concision.
- **One comment per claim.** If a later finding corrects an earlier comment, edit or delete that comment. Stacking a correcting comment leaves a stale claim in the thread that every later reader pays for.
- **Do not restate what the page already shows.** If the issue is closed, the diff carries the change, or another comment supplied the context, do not narrate it again.
- **Editing text a human has already read:** only when the cut is meaningful AND nobody has responded yet — no comments, no reviews. After that, editing looks like rewriting history under discussion.
- **Post markdown with `--body-file`, never an escaped string.** `gh pr edit N --body` with inline `\n` renders one wall of literal `\n` with raw `##` and pipes showing. Write the file, pass `--body-file`, then fetch the body back and compare it to the file.

## When to load this skill

Load it whenever you write something a human will read that is not a throwaway one-line ack. Post the writing, then run the pass: delete the closing paragraph, delete flattery, delete restated points. If what is left reads plain and complete, you are done.

## Never @-mention the owner of the thread

A comment that tags the person who owns the PR, issue or review thread is a self-ping. It notifies
no one new. Credit belongs in git authorship, not in a mention. `check-prose.py` flags every
`@`-mention as a candidate; keep one only when the thread is external to that account and the
mention is needed.

## The gate script

`scripts/check-prose.py` is the clean-writing gate. Exit 0 clean, 1 findings, 3 usage error.

```bash
python3 scripts/check-prose.py <file> [<file> ...]
python3 scripts/check-prose.py --text "the string about to be posted"
python3 scripts/check-prose.py --dupes <file>        # each URL / PR # / issue # appears once
python3 scripts/check-prose.py --policy <prose.json> <file>
```

Policy lookup order: `--policy`, then `$EXO_PROSE_POLICY`, then `<store>/policies/prose.json`
where `<store>` is `$EXO_PRINCIPAL_STORE`, else the store of `$EXO_PRINCIPAL`: config
`principals.<id>` (a store made with `exo init --root`), else `$EXO_HOME/principals/<id>`. Internal names
come from `--names`, `$EXO_INTERNAL_NAMES`, or `<store>/policies/internal-names.txt` (one per
line). No store means generic defaults. Policy keys (all optional):

```json
{"banned_phrases": ["regex", "..."], "internal_names": ["name"], "identifier_patterns": ["regex"],
 "max_paragraph_chars": 420, "flag_mentions": true, "flag_semicolons": true}
```
