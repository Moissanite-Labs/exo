# Decision models for Exo

Research for D-015. Date: 2026-10-11. Author: lead seat, for A.

A's ask: "for mechanisms like this that consist of straightforward decisions and tool calls, we could use
one of the many cost effective decision models that are being released recently (jev by typesafe first,
then perplexity and openai released their versions, plus there's the local version laya). we could let
users choose between these options. I think it would be cool if it shipped with a custom version/mod or
even special post-train of the local version of jev, laya".

"Mechanisms like this" means model-summoned UI panels (D-014), routing, gating, the learning loop's
classifications (D-012) and tool selection.

Marking used below:
- **[primary]** read on the vendor's own docs, model card, repo or terms.
- **[secondary]** from press, blogs or aggregators only. Treat as unverified.
- **[unverified]** not found, or my estimate.

## 0. Short version

- A decision model takes some text (the "state") and a set of typed questions. It returns probabilities,
  not prose. Three question types are shared by all four: **choice** (pick one of up to N options),
  **score** (a level on an ordered scale) and **noul** / **predicate** (probability of yes).
- None of them write text, so none of them fill free-text tool arguments. They pick *which* tool, panel,
  model or label. A generative model still writes the arguments, drafts and plans.
- One correction to the framing: **Laya is not TypeSafe's.** It is an independent open-weight model by
  Convai Innovations that speaks Jev's wire protocol. So it is "the local Jev" in practice, not in origin.
  Perplexity's model is *also* open weights (27B, Apache 2.0), but too big for a laptop or device.
- Prices are tiny. A 1,500-token decision costs about $0.00006 on Jev, $0.00003 on Perplexity, $0.00015
  on OpenAI, and nothing on local Laya.
- Base Laya is near chance zero-shot on mixed decisions. It needs fine-tuning to be good. That is exactly
  the opening for a shipped "exo-laya" checkpoint, which Apache 2.0 allows.
- A hard constraint: TypeSafe's terms forbid using Jev outputs to train another model. So Jev cannot be
  the teacher for exo-laya. Labels must come from users' own verdicts, rules, and open-weight teachers.

## 1. The models

### 1.1 Comparison

| | TypeSafe Jev | Laya (Convai) | Perplexity pplx-decider | OpenAI Decisions API |
|---|---|---|---|---|
| Model id | `jev-1.13.0` (`jev-latest`) [primary] | `laya`, `laya-multilingual`, `laya-typed-decisions` [primary] | `pplx-decider-v1.1-27b` (v1 still accepted) [primary pricing page; rest secondary] | `gpt-6-luna` only [primary] |
| Released | 2026-09-15, early access [secondary] | Repo 2026-09-18; v0.4.3 now [primary] | v1 2026-10-01, v1.1 2026-10-06 [secondary] | DevDay 2026-09-29; public beta 2026-10-06, GA "in the coming weeks" [primary] |
| Where it runs | Hosted only. Also on Vercel AI Gateway and Runware [primary/secondary] | Local (CUDA, MPS, XPU, CPU). Also hosted on Runware as `runware:laya@1` [primary/secondary] | Hosted API, plus open weights [primary HF card] | Hosted only [primary] |
| Endpoint | `POST https://api.typesafe.ai/v1/systemone` [primary] | `laya-serve` exposes the same `POST /v1/systemone` [primary] | `POST https://api.perplexity.ai/v1/decisions` [secondary] | `POST /v1/decisions` [primary] |
| Question types | choice, score, noul [primary] | choice, score, noul [primary] | choice, score, noul [secondary] | choice, score, predicate; answers can be `refusal` [primary] |
| Limits | 255 options per choice; 2 to 10 score levels; 64k tokens per request; 100k tok/s, 80 req/s [primary] | About 20 options at default budget (100 max over HTTP); 512 or 1,024 token context [primary] | 128 questions per call; 255 options; 10 levels; 262k tokens; 10 req/s [secondary]. Self-hosted: 8,192 tokens per decision [primary] | Not stated [primary] |
| Input | Text or JSON only [primary] | Text, email dict, ticket or JSON [primary] | Text, JSON and images [secondary] | Text and base64 images [primary] |
| Price (input) | $0.042 per 1M tokens, output free [primary] | Free self-hosted. Runware: free until 2026-10-12, then $0.020 per 1M [secondary] | $0.02 per 1M, output free, no per-request fee [primary] | $0.10 per 1M, no output or cache charges; regional and long-context premiums [primary] |
| Latency | Vendor: 70 to 500 ms [secondary]. Third party: 236 to 276 ms p50; 588 ms mean in one lab test [secondary] | 32.8 to 39.5 ms per question on a T4; 7.6 ms on M4 Max (MLX port); 193 to 464 ms on CPU [primary/secondary] | Not published [secondary] | "About 10x faster than the Responses API" [primary]; about 150 ms [secondary] |
| Licence | Proprietary. Master Customer Agreement 2.3(b) bans distillation and training a model to imitate the output [primary] | Apache 2.0, weights and code [primary] | Apache 2.0 weights, fine-tune of Qwen3.8-27B [primary] | Proprietary [primary] |
| Data | Not trained on customer data; ZDR for enterprise [primary] | Never leaves the machine | Not checked [unverified] | ZDR and HIPAA for eligible customers; US and EU residency [primary] |
| Benchmarks | Vendor workflow set: 67.8% accuracy, 0% schema errors, 0.4 s mean [secondary, vendor-run] | Fine-tuned 0.766 vs Jev 0.727 on typed-decisions; base zero-shot 0.362 (random 0.318). Jev far ahead on 77-label Banking77 (0.870 vs 0.425) [primary, Laya's own card] | Decision Index overall 61.56 vs Jev 57.9; Tools 78.88 vs 75.1 [primary HF card, vendor-run] | None from OpenAI. One third-party test found Luna right 68% of the time when it said 99%+ [secondary] |

### 1.2 Notes per model

**Jev (TypeSafe).** The category starter. A non-autoregressive model trained with "RLCD"
(reinforcement learning for calibrated decisions). Mature docs, many cookbooks (function calling, skill
suggestion, guardrails, reranking, confidence-gated routing). Known weaknesses from its own
"jaggedness" page [primary]: literal reading, numbers and counting, dates, double negatives, large
irrelevant state, prompt injection, and a bias toward the first choice option. Its confidence for choice
is `(n * p_max - 1) / (n - 1)` [primary, per Laya's README]. A's Hermes skill `typesafe-ai` (leo profile)
already wraps it with an advisory-only runner, a kill switch, five decision classes, and a label-free
ledger allowlist. That design carries over well (section 3).

**Laya (Convai Innovations).** Encoders with a decision head: ModernBERT-large (421M, English) and
mmBERT-base (322M, 100+ languages). The `Router` picks the checkpoint by script and language. Python
`pip install laya`; extras for an HTTP server, an MCP server, ONNX Runtime, LangChain and others. A
TypeScript SDK and a JS-native ONNX runtime (`laya-ts`) exist. Formats: safetensors on Hugging Face,
ONNX through the extra; MLX via a community port (`laya-mlx`, about 700 MB); no GGUF [primary]. Honest
limits from its own README [primary]:
- Base checkpoints are near chance on mixed typed decisions. "A fast base to specialise, not a zero-shot
  decision engine."
- Ships over-confident. Per-type temperature fitting cut ECE from 0.466 to 0.081. Refit on your own data.
- Options share a small token budget. Past about 20 options accuracy falls; shortlist first.
- Avoid `true`/`false` or `yes`/`no` as choice labels. Negated requests can still pick the wrong action
  (issue #377). Score questions are its weakest type.
- Its `confidence` is 1 minus normalised entropy, not Jev's formula. Thresholds do not transfer.

**Perplexity pplx-decider.** A 27B Qwen fine-tune with a separate decision head. Strong on its own
"Decision Index". Takes images. Cheapest hosted price. The open weights need about 49 GiB of GPU memory
[primary], so self-hosting means a rented GPU. Note the model card says the repo needs authenticated
Hugging Face access; whether it is fully public is unclear [unverified].

**OpenAI Decisions API.** Same three primitives under different names (`predicate`, `name` instead of
question id, `input` instead of `state`). Takes images. Hosted only. Most expensive of the four but still
cheap. Strong data controls (ZDR, EU residency). The independent calibration test above is a warning:
gate on fitted thresholds, not on the raw number.

**Others seen, not researched.** A social post mentions a Cloudflare decision model called "Clef" on the
same day as Perplexity's launch [secondary, unverified]. Not covered here.

### 1.3 A's prior Jev work (what it teaches)

- **`typesafe-ai` skill** (`~/.hermes/profiles/leo/skills/typesafe-ai/SKILL.md`): advisory only
  (`chosen_decision: null`, a named owner decides), kill switch env var, exit codes (`3` = escalated is
  normal), label-free input contract, ledger rows of hashes and probabilities only, Wilson intervals,
  ECE and Brier in its eval, and "do not adopt upstream thresholds; fit our own". All of this is reusable.
- **`<principal>/hermes-jev-curator`** (the principal's fork of anpicasso's Jev-based skill curator): A's PR #1 fixes a
  resolved-path test on macOS; issue #2 shows `use_jev: false` ignored on two of three code paths; issue
  #3 shows a malformed 200 response retried despite a "terminal" policy. Lessons for Exo: the "use the
  model" switch must hold on every path, and the decision transport must sort errors into terminal and
  retryable on purpose, with tests.
- **`aska-digital/docu-sort`** (fork of `kyotofin/tax-doc-classifier`): Jev classifies tax pages over
  261 forms at $0.00115 per page, 34x cheaper and 6x faster than the Sonnet classifier it replaced, with
  0 wrong on 314 filled pages. Patterns worth copying: one `Backend.ask(state, questions)` method so any
  model can plug in; a strict gate (wrong *or* below 0.95 counts as an error); a hierarchy where the
  option list is too big.
- **`protean-ordaprompt`** (rules, not Jev): route / ask first / abstain, receipts with hashes only, and
  the auto-route answer ships locked until calibration on real feedback passes. Same discipline applies.

## 2. Which Exo mechanisms fit a decision model

Rule of thumb: if the answer is a pick from a known list, a yes or no, or a level, it fits. If the answer
is text, a plan, or an argument for a tool, it needs a generative model. Code still owns exact rules
(permissions, budgets, guarded lists).

| Mechanism | Fits a decision model? | Question shape | Notes |
|---|---|---|---|
| Panel summoning (D-014) | **Yes** | noul "would a panel help now?" plus choice over panel types + `none` | Classic case. Dismissals and manual opens are free labels. |
| Model routing (which model for this turn or task) | **Yes** | choice over task classes or tiers; noul "needs deep reasoning?" | Routes within models the principal approved; model choice stays A's call. |
| Session and topic routing (ordaprompt-style) | **Yes** | choice over candidate sessions + `new` | Abstain on low confidence. |
| Gate assist (`exo.gate`) | **Yes, tighten only** | noul per risk: public? irreversible? spends money? third party? | Rules decide first. The model may only move an action toward "ask", never toward "allow". Guarded items never depend on it. |
| Learning loop stage 1 classify (D-012, 5.4) | **Yes** | noul signal vs noise; choice type (taste, fact, procedure, question); choice domain; choice agree / conflict / new | LEARNING-LOOP 5.9 already plans a small classifier here. |
| Node targeting for a lesson | **Yes, after shortlist** | choice over top-k nodes from `exo.retrieve` + `none` | Laya needs k under about 20. |
| Capture: is this a correction or a ruling? | **Yes** | noul per pattern | Replaces or backs up `exo.capture` heuristics. |
| Retrieval rerank | **Yes** | score per candidate node | Jev has a rerank cookbook. |
| Tool selection | **Partly** | choice over toolsets; noul per tool "needed this turn?" | Use it to shrink the tool list sent to the main model. Arguments stay with the main model. |
| Ask or act, interruption budget (`exo.ask`) | **Yes** | score of value of information; noul "would the principal want to be asked?" | |
| `/goal` done check (D-013) | **First pass** | noul "goal met?" | Escalate when unsure. |
| Preference-pair choice items in the eval (6.2) | **Yes, as a responder** | choice chosen vs rejected | Useful to test decision providers on the principal's own pairs. |
| Stage 2 reflect, critic, lesson wording | **No** | text | Generative. |
| Eval judge (D-012) | **No** | text plus reasoning | Stays a generative judge from a different family. |
| Summaries, compaction, drafts, code, plans | **No** | text | Main or aux generative model. |
| Tool arguments with free text | **No** | text | Main model. |

## 3. A `decide` role in Exo's router

### 3.1 Interface

The router (`src/exo/core/models/router.py`, being written in wave 1) gains a second kind of role.
Generative roles return a stream. The `decide` role returns typed answers.

```python
class DecisionProvider(Protocol):
    name: str
    async def ask(self, state: str | dict, questions: dict[str, Question], *,
                  model: str | None = None, timeout: float = 2.0) -> DecisionResult: ...

# Question: {type: "choice"|"score"|"noul", instructions, criteria}
# DecisionResult: {answers: {qid: Answer}, model, usage, latency_ms, provider}
# Answer: {type, value, probabilities, p_answer, confidence_raw}

async def decide(mechanism: str, state, questions, *, policy) -> Decision
```

- **Canonical wire shape is Jev's `/v1/systemone`.** Laya's server already speaks it, so Jev and Laya
  share one adapter with a different base URL. Thin adapters map Perplexity (`/v1/decisions`, same three
  types) and OpenAI (`predicate` to `noul`, `input` to `state`, questions as a named list).
- **Two extra providers** close the gaps: `rules` (today's `exo.capture`, `exo.taste` and gate rules,
  always available, no model) and `llm` (any generative model forced to pick from the options; slow and
  uncalibrated, used only as a last resort).
- **One confidence number.** Each provider's own `confidence` uses a different formula. Exo gates on
  `p_answer` (the probability of the chosen answer) after per-provider, per-mechanism temperature
  calibration fitted on the user's data. Raw vendor confidence is kept in the log but not used.
- **Question specs are files**, not inline strings: `decisions/<mechanism>.toml` with instructions,
  criteria, option keys (no `true`/`false` labels), shortlist size, default threshold, and a spec hash.
  This makes them a "prompt pack" the hill-climb can vary (LEARNING-LOOP 6.3).
- **Small state by design.** Laya reads 512 to 1,024 tokens. Each mechanism builds a short digest (the
  request, the last turn, the relevant option text), not the transcript. This also helps Jev, whose
  accuracy drops with irrelevant state.

### 3.2 User choice of provider

Config, in the seat folder:

```toml
[roles.decide]
providers = ["laya-local", "jev", "perplexity", "openai"]   # order = preference and fallback
hosted_allowed = false        # may personal state leave the machine?
kill_switch_env = "EXO_DECIDE_DISABLED"

[roles.decide.per_mechanism]
gate = { providers = ["rules", "laya-local"], tighten_only = true }
panels = { threshold = "fitted" }
```

- `exo init` asks once: **Local** (Laya: free, private, offline), **TypeSafe Jev**, **Perplexity**,
  **OpenAI**, or **Rules only**. Default is Local. This follows D-011, which rejected sending personal
  text to remote embeddings.
- Hosted providers are opt-in, globally and per mechanism. A redaction pass strips secrets and anything
  matching the store's private patterns before any hosted call (same contract as the `typesafe-ai` skill).
- `exo models list` shows each decision provider with price, measured latency, and the user's own
  accuracy on their held-out decisions, so the choice is informed by the scoreboard, not the vendor.

### 3.3 Fallbacks and escalation

Per call, in order:

1. Exact rules in code first. If a rule decides, no model is called.
2. First reachable provider in the list. Timeout per call (default 2 s hosted, 0.5 s local).
3. On 429, 529, timeout or network error: next provider. Malformed responses are terminal for that
   provider and logged (lesson from curator issue #3). Cooldown per provider, as in the generative router.
4. If `p_answer` is below the mechanism's threshold: **escalate**, by mechanism policy, to the main
   generative model, to the principal (`exo.ask`), or to a safe default (no panel, keep the current
   model, ask before acting). Abstaining is a normal outcome, not an error.
5. If nothing is reachable: rules only, or the safe default. The turn never blocks on a decision model.

A cascade is a valid setting: local Laya first, a hosted model only on low confidence. One independent
test matched Jev's accuracy at 1.8x speed this way with 55% escalation [secondary]; Exo measures its own.

### 3.4 Logging to the ledger

Add one kind to `exo.ledger.KINDS`: `decision`. Its body holds no raw personal text, so the signed ledger
can travel and sync. The state itself goes to a local table.

```
decision body:
  mechanism, spec_id, spec_hash, provider, model, adapter (exo-laya version or none),
  input_hash, state_ref (row id in sessions.sqlite decisions table, local only),
  questions: {qid: {type, options}},
  answers: {qid: {value, probabilities, p_answer, confidence_raw}},
  threshold, outcome: acted | escalated | asked | defaulted, escalated_to,
  fallback_chain, latency_ms, cost_usd
refs: [turn event id]
```

Labels arrive later as ordinary events that `refs` the decision id:

| Signal | Label |
|---|---|
| Principal overrides (opens a different panel, switches model, changes the gate answer) | `verdict: should_have_been` with the right option. Strongest. |
| Principal dismisses a summoned panel quickly, or undoes the action | `verdict: reject` |
| Principal uses the result (panel interacted with, routed model's answer accepted) | `verdict: accept` (weak) |
| Escalated case: what the bigger model or the principal chose | soft label (the principal's choice is hard) |
| Outcome (tool ran fine, task not reopened) | `action` with outcome, weak |
| Ruling ("never pop the board while I type") | `ruling`, which becomes a rule in code or a spec change |

This feeds both loops:
- **Learning loop.** Decision verdicts are signals like any other. Stage 1 proposes spec changes or
  threshold changes; a repeated override proposes a rule.
- **Hill-climbing eval.** `exo.eval.build_replays` grows a `decision` item type: (state, questions,
  label). Metrics per mechanism and per provider: accuracy, Brier, ECE, escalation rate at the fitted
  threshold, cost and latency. Candidates are a new spec wording, a new threshold, a different provider,
  or a new exo-laya adapter. Accept only if better on held out (LEARNING-LOOP 6.4). Guarded gate cases
  are pinned must-pass items.

## 4. Shipping a custom Laya ("exo-laya")

### 4.1 Options

| Option | What it is | Cost | Quality | Verdict |
|---|---|---|---|---|
| **A. Prompt pack only** | Ship tuned question specs, option keys, shortlist rules and per-mechanism thresholds. Works on any provider. | None | Good on Jev, Perplexity and OpenAI. Weak on base Laya, which is near chance zero-shot. | Ship first. Needed by every other option anyway. |
| **B1. Full fine-tune of a small Laya checkpoint** | RLCD fine-tune of `laya-multilingual` (322M) or `laya` (421M) on Exo's mechanisms, plus calibration temperatures. | Free to low. 4 to 5 h on 2x T4 for 30k questions; also runs on a 16 GB Mac [primary]. | Laya's own result: 0.36 to 0.77 after fine-tuning on a domain. A browser-agent fine-tune went from 0 to 62% task success [primary]. | **Recommended as the shipped base.** At this size a full fine-tune is cheaper and simpler than an adapter. |
| **B2. Per-user LoRA adapter or per-user calibration** | A small delta trained nightly on one user's ledger decisions, kept in their store. | Local compute only | Fits the user. Must beat B1 on their held-out set to be used. | Phase 2. Start with per-user temperatures and thresholds (tiny, safe); add LoRA only if the eval shows a gain. LoRA support on Laya's custom head is [unverified]. |
| **C. Full post-train from scratch** | New RLCD run on a new backbone. | High (data, GPUs, research time) | Unknown | Rejected for now. Nothing suggests it beats B1 for Exo's narrow mechanisms. |

### 4.2 What the licences allow

- **Laya (Apache 2.0, weights and code):** modify, fine-tune, redistribute and sell, including the weights.
  Keep the licence and any NOTICE text, mark changed files, and do not use the "Laya" or Convai names as
  if endorsed (Apache 2.0 grants no trademark rights). Name it e.g. "exo-decide, fine-tuned from Laya" and
  record it in `PROVENANCE.toml` and `LICENSES/`.
- **Perplexity pplx-decider (Apache 2.0 weights):** a self-hosted copy may be used as a teacher to label
  synthetic data. API terms for using hosted outputs as training data were not checked [unverified].
- **TypeSafe Jev:** MCA 2.3(b) forbids "model distillation" and training "a model to imitate the output
  of the Services" [primary]. **Jev outputs must never be training labels.** Jev can still be a user's
  runtime provider and an eval comparison.
- **OpenAI:** its general terms forbid using output to develop competing models [from memory of OpenAI's
  Terms of Use, re-check before relying]. Treat it like Jev: runtime and comparison only.
- **Claude or other frontier models as synthetic-data writers:** Anthropic's terms restrict using outputs
  to build competing AI models. A narrow harness classifier is probably not that, but it needs a check
  before any seed set is written by Claude [unverified]. Safe teachers: users' own verdicts, rules,
  open-weight models.

### 4.3 Data sources

1. **Generic seed set (ships with Exo, no personal data).** Synthetic scenarios per mechanism (panel
   moments, routing classes, gate risks, signal vs noise, correction detection) written from Exo's specs,
   labelled by rules and by an open-weight teacher (self-hosted pplx-decider or another Apache or MIT
   model), plus public sets with compatible licences for intent and tool choice. Spot-checked by hand.
   Must pass the existing leak check (no A paths, names or accounts; D-004).
2. **A's corrections.** Ledger verdicts and rulings, hafiz `training/preference-pairs.jsonl` (25 today)
   and `eval-qa.jsonl` (28), and labels mined from A's Hermes `state.db` history at import (wave 4):
   which tools a turn actually needed, which model handled what, which corrections followed. These train
   A's per-user layer and form A's held-out set. They never go into the shipped base.
3. **Per-user decisions from the ledger.** Every `decision` event plus its later verdict (section 3.4).
   Stays on the user's machine. Training runs locally.

### 4.4 Training and eval pipeline

```
ledger decisions + verdicts ──► exo learn decisions export   (jsonl, dedupe, label rules, time split)
generic seed set ─────────────┘          │
                                         ▼
                       train (RLCD, Laya's MPS script or a GPU box for the base)
                                         ▼
                       calibrate temperatures on DEV, not train
                       (Laya's notebook calibrates on training items; README warns about this)
                                         ▼
                       eval on HELD OUT: accuracy, Brier, ECE, escalation rate at threshold,
                       latency on target hardware; probes for negation, boolean labels,
                       option order; pinned guarded gate cases
                                         ▼
                       compare vs incumbent (current checkpoint, base Laya, user's hosted provider)
                       accept only if better and no gate regresses (LEARNING-LOOP 6.4, 6.5)
                                         ▼
          base: versioned release, hash in PROVENANCE.toml      user layer: <store>/models/decide/, signed
```

- Cadence: the base is retrained per Exo release. User layers are retrained in the nightly slow loop once
  enough new labelled decisions exist (start at 200 per mechanism [unverified threshold, to be fitted]).
- Reuse `exo.eval` (`split`, `compare`, `gate`, `scoreboard`) as planned in LEARNING-LOOP 6.8.
- Cold start: until a mechanism has labels, run it on rules or the user's hosted choice and log
  everything. Auto-acting stays locked per mechanism until calibration passes (ordaprompt's rule).

### 4.5 Size for a future portable device

| Form | Size | Notes |
|---|---|---|
| `laya-multilingual` fp16 | about 650 MB download [primary] | Laptop and phone class. |
| `laya` (English) fp16 | about 810 MB [primary] | |
| int8 ONNX (322M params) | about 330 MB [unverified, arithmetic] | ONNX path exists (`laya[onnx]`, `laya-ts`, a Rust OpenVINO port). |
| 1.58-bit ternary | about 9x smaller, 81% agreement with full model [secondary, community repo] | Too lossy today without retraining. |
| Smaller backbone (e.g. ModernBERT-base, about 150M) distilled from exo-laya | about 150 MB int8 [unverified] | Option for wave 5 if the board is tight. Distilling our own Apache model is allowed. |

CPU latency is 193 to 464 ms per question on a desktop CPU [primary]. On a Pi-class board expect slower,
likely under 1 s per question at int8 [unverified estimate]. That is fine for panels, routing and stage 1,
which are not on the typing path. The device carries the store and one small checkpoint; hosted
providers are an optional boost when online.

## 5. Proposed D-015

> ## D-015 (2026-10-12) A `decide` role: small decision models for bounded choices, Laya local by default
> - **Decision:** Exo's router gets a `decide` role with one typed interface: a short state plus choice,
>   score and yes/no questions in, calibrated probabilities out (Jev's `/v1/systemone` shape as canonical).
>   Providers are pluggable and chosen by the user at `exo init`: local Laya (default), TypeSafe Jev,
>   Perplexity pplx-decider, OpenAI Decisions, rules only. Fallback order: rules in code, then providers
>   in the user's order, then escalate on low confidence to the main model, the principal or a safe
>   default. Mechanisms on `decide`: panel summoning (D-014), model and session routing, learning-loop
>   stage 1 (D-012), correction capture, retrieval rerank, tool shortlists, ask-or-act, `/goal` first pass,
>   and gate assist (tighten only; guarded items never depend on a model). Text work (reflect, critic,
>   judge, drafts, tool arguments) stays generative. Every call writes a `decision` ledger event (hashes
>   and probabilities, no raw text) and later verdicts link to it, so the learning loop and the
>   hill-climbing eval score each mechanism and provider on the user's own data. Question specs are
>   files the climb can vary. Exo ships a prompt pack first, then "exo-decide", a full RLCD fine-tune of
>   Laya on a generic Exo seed set (Apache 2.0, provenance recorded), then per-user calibration and,
>   if the eval shows a gain, per-user adapters trained locally from the ledger.
> - **Why:** these mechanisms are picks from known lists. Decision models answer them in tens to
>   hundreds of milliseconds for fractions of a cent, with no parsing and a usable probability for gating.
>   Local Laya keeps personal text on the machine (as D-011 did for embeddings), works offline and fits
>   the device horizon (D-009). Base Laya is weak zero-shot, so a shipped fine-tune is where the value is,
>   and its licence allows it. A's own work (docu-sort: 34x cheaper, 6x faster than Sonnet; the
>   typesafe-ai skill's advisory contract) shows the pattern works and how to keep it safe.
> - **Rejected:** Jev only (hosted, closed, personal text leaves the machine, no device path, and its
>   terms ban distillation); a generative model with JSON output for every decision (slower, costlier,
>   uncalibrated, schema errors); a full post-train from scratch (cost with no evidence of gain); using
>   Jev or OpenAI outputs as training labels (their terms forbid it); letting a decision model relax a gate.
> - **Calls made by the lead (reversible):** Local is the default; hosted providers are opt-in and get
>   redacted state only; auto-acting stays locked per mechanism until its calibration passes on held-out
>   decisions; the `decision` ledger kind is added in wave 2 with the gate re-home.
> - **Waiting on A:**
>   1. Default decision provider for A's own seats. Default: local Laya, Jev as the low-confidence step
>      for non-private mechanisms (panels, routing).
>   2. API spend: whether to fund Jev, Perplexity and OpenAI keys for the provider comparison, and a
>      monthly cap. Default: a small cap; at current prices 10k decisions a day cost under $1 a day.
>   3. GPU spend for the exo-decide base: free notebook GPUs or A's Mac for the fine-tune; a rented GPU
>      for some hours only if a self-hosted pplx-decider teacher is wanted (needs about 49 GiB). Default:
>      no rented GPU until the free path is measured.
>   4. Whether Claude-written synthetic seed data is acceptable after a terms check. Default: no, use
>      rules, open-weight teachers and hand review.
>   5. Publishing exo-decide weights under aska-digital on Hugging Face. Default: private until it beats
>      base Laya and Jev on the generic held-out set.
> - **Revisit if:** a decision model ships a small open checkpoint that beats a fine-tuned Laya zero-shot;
>   TypeSafe changes its terms or ships local weights; or the scoreboard shows decision-model errors
>   costing more corrections than they save.

## Sources

Primary:
- TypeSafe docs index, models, API, legal, Jev 1.13 jaggedness: https://docs.typesafe.ai/llms.txt,
  https://docs.typesafe.ai/models.md, https://docs.typesafe.ai/api.md, https://docs.typesafe.ai/legal.md,
  https://docs.typesafe.ai/model-jaggedness/jev-1.13.md
- TypeSafe Master Customer Agreement (2.3(b), 4.2): https://typesafe.ai/legal/mca
- Laya repo and model card: https://github.com/NandhaKishorM/laya, https://huggingface.co/convaiinnovations/laya
- Perplexity pricing: https://docs.perplexity.ai/getting-started/pricing
- Perplexity model card: https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b
- OpenAI Decisions guide: https://developers.openai.com/api/docs/guides/decisions
- A's repos: https://github.com/anpicasso/hermes-jev-curator (PR #1, issues #2, #3),
  https://github.com/aska-digital/docu-sort, https://github.com/aska-digital/protean-ordaprompt
- Hermes skill: `~/.hermes/profiles/leo/skills/typesafe-ai/SKILL.md`

Secondary (unverified):
- DataCamp on Jev: https://www.datacamp.com/blog/system-one-models-jev
- Runware on Jev and Laya: https://runware.ai/blog/jev-laya-and-the-emerging-role-of-decision-models
- Laya vs Jev lab test: https://github.com/yibie/laya-jev-lab
- Perplexity Decisions API summary: https://systemonemodels.org/models/pplx-decider/
- OpenAI Decisions API coverage: https://www.eesel.ai/blog/openai-decisions-api,
  https://anth.us/articles/openai-decisions-api-preview/
- Laya ternary quantization: https://github.com/xixi3548942758-design/laya-ternary-lite
