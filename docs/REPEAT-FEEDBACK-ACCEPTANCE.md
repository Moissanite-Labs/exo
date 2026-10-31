# Model-visible progress checks

Refs #792. The turn loop now supplies a soft, model-visible progress check after three successful completions
of the same recognized inspection call with the same bounded result fingerprint. This closes the missing
feedback path demonstrated by the original #806 acceptance fixture. It is separate from #752's proposed
operator-facing normalized-command detector: this recognizer does not normalize away folders or shell syntax.

A recognized call is a small `read_file`/listing/search operation or a simple `bash` invocation of `cat`, `head`,
`grep`, `rg`, `ls`, `wc` or `stat`. Shell operators, substitutions, redirects, `rg --pre`, `sed` and polling tools
are excluded. There is no command execution in the observer. It fingerprints exact serialized arguments (at
most 8,192 characters), result length and the first 4,096 result characters. This is a heuristic, not proof
that full outputs are identical or the work is useless. Results changed beyond that prefix with unchanged
length can still produce an advisory. Equivalent calls with different arguments may be missed.

The next provider request receives at most one transient `[harness progress check]` note, labelled as harness
observation rather than a principal request. It names a bounded, scrubbed operation, success/failure counts
and asks for a distinct next step or a concrete blocker. Result content never enters the note. The note does
not grant permission, require a write/post, select a tool, change provider retries, interrupt execution or
alter the user's finite/uncapped turn limit. It also reaches an existing finite-cap wrap-up request.

The active note is replaced in one slot as evidence changes, never appended to message history, captured as
principal text or carried into another session/turn. Its metadata is `author: system`, `note: progress_check`;
pinned-block tags are defused. Successful other operations, distinct inspection, changed result fingerprints
and queued steering clear an episode. Failed writes do not count as progress. A cancelled unfinished read
never counts as completed. Wait/status tools and tail polling remain usable; ad hoc polling using identical
`cat` calls may receive an advisory but is never blocked by it.

One warning `notice` event records each episode, with at most four per turn; repeated reads do not create a
notice storm. The model's active note is still available after that trace-notice quota. The note text stays
below 1,200 characters and contains no raw tool output.

## Verification and limits

`tests/trace/test_repeat_feedback_acceptance.py` drives the real CoreEngine with fake providers and harmless
handlers. Its two former strict expected failures now pass; copied onto main 080e87ba without the runtime
change they fail while both distinct-work/write-retest controls pass. No displayed command executes.

`test_progress_feedback.py` covers one-slot/quota bounds, failed and successful writes, changed evidence,
polling exclusions, output-injection resistance, next-request delivery, trace notice, retry preservation,
harness provenance, no history persistence, session/turn isolation, steering, finite wrap-up and cancellation.
The uncapped fixture uses `sys.maxsize` and finishes by its provider's choice, with no added cap.

These fixtures establish delivery and boundaries. They do not establish improved behavior on real tasks:
their final blocker reply is scripted. A later evaluation should measure a distinct action or useful blocker
after the signal, rather than more model/tool events. Broader semantic operation recognition, explicit polling
intent/configuration and composition with #752 remain follow-ups. No live model, canary, installation or
permission change was used to validate this draft.
