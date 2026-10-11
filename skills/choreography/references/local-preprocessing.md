# Local Preprocessing Adapter Contract

> A bounded, local preprocessing layer that a Protean Team-installed agent *may*
> apply around approved outbound work. It is **not** a provider/router
> replacement and it **never** handles high-stakes judgment. This document
> states the contract and the automatic decision policy in a vendor-neutral
> way; the public kit documents the contract, it does not bundle an
> implementation.

## What this is (and is not)

- **Is:** a small adapter that runs cheap local preprocessing **before**
  approved text leaves the device for a remote model, external service,
  shared project room, or durable external log — when doing so improves
  privacy, cost, routing, or artifact quality.
- **Is not:** a replacement for a reasoning model, a new Hermes provider, a
  change to the routing/llama layer, or a mechanism for high-stakes judgment.
- **Boundary:** this layer runs *locally and guarded*. It is enabled only as
  an explicit policy in a generated installation — the public kit does not
  silently edit any user's Hermes profile or force this on anyone.

## Why it exists

A generated team already sends approved work outward (remote models,
shared rooms, durable logs). A bounded preprocessing step can reduce what is
exposed, cut cost, and improve metadata **before** anything is transmitted —
without changing the reasoning model or the routing topology. The value is
that the step is *automatic when the policy matches the task*, not a manual
ritual an agent has to remember.

## Automatic decision policy

Apply the appropriate local operation when the task matches — without asking
the operator for a conscious per-call opt-in — subject to the exclusions in
"Never applied" below.

| When | Operation | Status of result |
|---|---|---|
| Sensitive text is leaving the device for a remote/external/durable target | **Redact** | First-pass filter; **hold for review** on address / numeric / uncertain findings |
| Bulk notes, transcripts, or multi-item intake need cheap topic grouping | **Gist** | Routing **hint only** — never the sole authority for a decision |
| An artifact, transcript, or deliverable needs a title / short description | **Title** | **Draft** metadata only; read before exposing to a user |
| Media-related workflow, explicitly requested, model already available | **Media ops** | **Opt-in only**; no automatic weight pulls during active work |

### Never applied

- Ordinary conversation.
- Final synthesis of a deliverable.
- Legal, financial, security, architecture, or other **high-stakes judgment** —
  unless the local call is only a bounded preprocessing step feeding a human
  or full reasoning review.
- Automated audio/video processing unless the task is explicitly media-related
  and the model is already available. Weights are **never** pulled
  automatically during active work.

### Hold-for-review rule

If a **Redact** pass reports address, numeric, or **uncertain** findings, the
outbound step is **held for review** and escalated to the operator/user before
anything is transmitted. Redact is a *first-pass filter*, **not** a legal
anonymization or de-identification step; it must not be relied on for
compliance (GDPR, HIPAA, or any regulatory redaction). It produces false
positives on bare numbers (dates, order IDs, phone-like sequences) that carry
no PII meaning — those need human review before trusting or discarding output.

## Adapter requirements (the contract)

Any implementation that claims to satisfy this contract MUST:

1. **Run locally.** No outbound transmission of the *original* input to the
   preprocessor's own cloud; the preprocessing itself stays on-device.
2. **Be guarded.** Use guarded wrappers, not raw CLI invocations in hot
   paths. Update checks are disabled; the adapter never alters an active
   session, the routing layer, or a Hermes profile to activate itself.
3. **Log no raw PII.** The original input, PII, and any original→placeholder
   map never go into logs. An original→placeholder map is kept locally only
   when restoration is explicitly required, and is never placed in a durable
   log.
4. **Check availability before use.** Confirm the model/service is present and
   runnable before invoking it.
5. **Check exit + parse after use.** Verify exit code 0 and that the returned
   JSON parses and carries the required fields before trusting any output.
6. **Record provenance when a result becomes durable.** Capture model id,
   version, and revision so any durable artifact that used the output can be
   attributed.
7. **Stay bounded.** This is preprocessing, never a decision authority. The
   reasoning model and the routing topology are untouched.

## Desert Ant as one implementation

Desert Ant is a *possible* implementation of this contract on macOS, not a
required dependency. Nothing in the Protean Team kit requires it, and the public repo
does not bundle Desert Ant code, model files, or license text.

- The **Protean Team kit layer** is Apache-2.0 (see `../LICENSE`).
- The **Desert Ant models** carry a **separate vendor license** (source-available,
  not an OSI open-source license) that governs those models and does **not**
  extend to the kit. See the model vendor's public documentation for the
  source-available terms, attribution, and the monthly-active-device threshold
  that governs production distribution of the models. Do not use model outputs
  or logs to train a competing model.

## Safe provenance and claims

- **No invented numbers.** This document states expected benefits as *intended
  outcomes* (reduced exposure, cost, better metadata). It does not cite
  fabricated performance, cost, or accuracy figures.
- **Link, don't copy.** Where a public implementation is referenced, we link
  to its public documentation/repo as an optional reference and never copy its
  code, prompts, or license text into this repo.

## Enabling it in a generated installation

This contract is documentation, not an automatic behavior of the public kit.
A generated installation **implements the contract** by:

1. Installing/enabling a local adapter (e.g. an available local CLI or guarded
   wrappers) as an **explicit operator policy** — never silently.
2. Wiring the decision policy above into the installed agent's skill set.
3. Confirming the adapter satisfies every requirement in "Adapter requirements".

Nothing in the Protean Team open-core build turns this on by default or edits a
user's Hermes profile to do so.
