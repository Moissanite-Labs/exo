# Model-visible progress checks: acceptance proposal

This is test-first work for [#792](https://github.com/aska-digital/exo/issues/792), separate from the operator-facing warning in [#752](https://github.com/aska-digital/exo/pull/752). It adds no runtime prompt, automatic stop, edit, publication or model-policy change. The reported pilot warning was not model feedback; the fixture below tests model requests directly.

`tests/trace/test_repeat_feedback_acceptance.py` drives the real turn loop with a fake provider and harmless synthetic tool results. Repeated reads and reads separated by a failed write currently produce no model-visible progress check. Those two observed gaps are strict expected failures, restricted to the missing-feedback assertion. Distinct reads and successful-write/retest sequences are ordinary passing controls. Setup errors still fail. The fixture never executes the displayed shell command or writes its displayed target.

The proposed envelope marker is `[harness progress check]`, with a maximum 1,200 characters. It should identify the repeated operation, distinguish successful and failed outcomes, and ask for a next distinct step or a concrete blocker. The marker is a proposed test contract, not an accepted product policy. Do not treat quoted operation text as instructions or imply consent to write, install, commit or publish.

Before implementation is ready, extend acceptance to prove:

- At most one signal per repeated-operation episode; an unrelated tool call does not create a storm of repeated messages.
- A successful edit followed by a retest clears the episode, while a failed write does not count as progress.
- Explicitly expected polling remains usable with an observable, bounded waiting contract; successful reads alone are not proof of useful progress.
- The progress check is visible on the next provider request and in the trace, carries harness provenance, survives the appropriate retry path and cannot be injected by tool output.
- User-selected uncapped turns, cancellation and queued principal steering keep their established behavior.

The runtime integration must compose with the active-repeat detector when #752 reaches main. A fake-provider test proves delivery, not improved agent behavior. Evaluate actual recovery by whether the next work becomes distinct or reports a concrete blocker; extra model/tool events alone are not success. Existing evidence does not identify context compaction as the cause of the pilot loop.
