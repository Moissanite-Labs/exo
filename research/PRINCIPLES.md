# Exo design principles (v1)

Distilled from the guardian-angel crawl (69 pages; see NOTES.md and CRAWL-LOG.md).
Exo has two goals held together: (1) fit A as closely as possible, (2) be replicable for any user.
Each principle states what is the generic, reusable mechanism and what is principal-specific data or tuning.
A section at the end flags ideas that only work for one user or only work generically.

---

## 1. The log is the source of truth; everything else is a rebuildable view.

- Sources: https://gwern.net/guardian-angel (UX), https://gwern.net/nenex, https://arxiv.org/abs/2403.01518, https://arxiv.org/abs/2306.13812, https://gwern.net/complement
- For Exo: One append-only, signed log of every event: principal statements, rulings, agent actions, outputs, accept/reject/edit verdicts, ingested documents. PRINCIPAL.md, memory nodes, skills, retrieval indices and any future adapter are derived from the log and can be regenerated when a model or format changes. A new model catches up by replaying the log. Periodic "reset and rebuild" replaces patching one artifact forever.
- Generic mechanism: log schema, signing, replay and rebuild pipeline. Principal-specific: the log contents.
- Rules out: memory that exists only inside a vendor's chat history; hand-edited profiles with no trail back to evidence; one long-lived adapter or prompt that nobody can rebuild.

## 2. Amplify the principal; do not replace them.

- Sources: https://gwern.net/guardian-angel (Principles), https://gwern.net/tool-ai, https://www.lesswrong.com/posts/bX7q9NcoGpb5KdZzQ/if-i-wanted-to-spend-way-more-on-ai-what-would-i-spend-it-on?commentId=wCNXu7dfSRof39dNP, https://gwern.net/interview-dwarkesh
- For Exo: The principal decides what is worth doing; agents decide how. The harness gives agents real agency (tool-ai shows that confining them loses) but inside the principal's goals. Onboarding finds the "assistant-shaped holes" in the user's week and makes context explicit and textual, since lack of context, not intelligence, blocks delegation.
- Generic: delegation scaffolding, onboarding that surfaces delegable work. Principal-specific: what A keeps (spend, model choice, naming, pricing, public posts, irreversible acts, logins) versus delegates.
- Rules out: products that optimize for removing the user from the loop entirely; "just use the chatbot more" onboarding.

## 3. Principal attention is the scarcest resource: engagement is a cost.

- Sources: https://gwern.net/guardian-angel (Anti-Principles: Engagement, Full Autonomy, Low Latency), https://gwern.net/doc/sociology/technology/1983-bainbridge.pdf, https://gwern.net/polish
- For Exo: Target a front-loaded, falling curve of principal interactions toward a few hard, informative decisions per day at constant output. Track interruptions per accepted output and per day as a core metric. "The goal is not zero interruptions but zero wasted interruptions." No rubber-stamp approval queues: Bainbridge shows humans cannot monitor quiet systems.
- Generic: interruption budget, metrics, decision batching. Principal-specific: the budget size and A's rhythm (night shift, "gm, brief me").
- Rules out: engagement or time-in-app as success; approve-every-step flows; voice and low-latency features as a priority.

## 4. Ask when uncertain, act when confident, and measure the uncertainty.

- Sources: https://arxiv.org/abs/1606.03137, https://arxiv.org/abs/2510.01171, https://burrsettles.com/pub/settles.activelearning.pdf, https://arxiv.org/abs/1612.01474, https://www.beren.io/2022-11-27-Don't-argmax-distribution-match/
- For Exo: Before a preference-dependent action, the agent predicts a distribution over what the principal would want (verbalized probabilities, or disagreement across several models or prompts). A flat distribution means ask; a sharp peak means act and log. Do not optimize hard against the learned profile; keep options open where the profile is thin.
- Generic: the uncertainty estimator and ask/act threshold logic. Principal-specific: the calibrated threshold, which moves as fidelity grows, and per-domain thresholds (A asks only for irreversible or public items).
- Rules out: agents that never ask (uncalibrated) and agents that always ask (useless); single "best guess" user models.

## 5. Every correction is permanent training signal, captured on the agent's own output.

- Sources: https://arxiv.org/abs/1011.0686, https://gwern.net/nenex (should-have-been, imitation learning), https://gwern.net/blog/2025/ai-cannibalism, https://gwern.net/interview-inkhaven (rule of three)
- For Exo: Log each agent output with the principal's verdict (accept, edit, reject) and reason, tied to the exact state the agent was in (DAgger). Provide a first-class "should have been" action. A correction must change future behavior, so it flows into memory or skills the same day. Rule of three: if A repeats an instruction three times, the harness proposes encoding it.
- Generic: verdict capture, should-have-been action, rule-of-three detector. Principal-specific: the corrections themselves.
- Rules out: corrections that live only in one session's context; feedback buttons whose data goes nowhere the user can see.

## 6. Consolidate by reflection, in positive and local form.

- Sources: https://arxiv.org/abs/2505.01812, https://arxiv.org/abs/2505.00661, https://arxiv.org/abs/2409.07431, https://arxiv.org/abs/2510.17941, https://arxiv.org/abs/2605.13829, https://arxiv.org/abs/2309.12288, https://gwern.net/blog/2025/better-llm-writing, https://arxiv.org/abs/2305.15076
- For Exo: Turning log into memory is a reasoning step, not a copy. For each correction: (a) ask what it implies beyond the instance, (b) generate Q&A and restatements in several contexts and both directions, (c) write it as a positive local claim ("A wants Y, not X, because Z"), never as a stored bad example with a separate "this was wrong" note, since models learn the claim and drop the negation. Weight ingest by importance (rulings and decisions far above chatter). Test depth: does the learned preference hold in far contexts and under pushback?
- Generic: consolidation pipeline, importance scorer, depth tests. Principal-specific: content, and the importance weights tuned to A (numbered rulings, caps for hard rules).
- Rules out: dumping raw transcripts into memory; storing rejected outputs as negative examples; minimal-patch responses to feedback.

## 7. Split memory by kind: taste in persistent context or weights, facts in retrieval with provenance.

- Sources: https://arxiv.org/abs/2403.01518, https://arxiv.org/abs/2401.08406, https://arxiv.org/abs/2505.10831, https://gwern.net/blog/2024/virtual-comments (prompt caching)
- For Exo: Style, stance and values go into a cached "principal pack" now and a per-user adapter later. Dates, IDs, people and project facts go to retrieval, with sources. Both stay: fine-tuning and retrieval gains stack. Long context is not a memory system.
- Generic: the two-tier memory architecture and the pack format. Principal-specific: the pack and the facts.
- Rules out: "just use a million-token context"; RAG alone for taste; weights alone for facts.

## 8. Build every user as a shared prior plus a compact, reflective delta.

- Sources: https://gwern.net/difference, https://arxiv.org/abs/2410.20268, https://gwern.net/optimal-interview, https://gwern.net/nenex (warm-starting, demo corpus)
- For Exo: Individual differences are low in information. Ship a strong generic base (demo corpus of standard workflows, population-level preference priors, default skills) and learn only the user's deviations. Weight the user's reflective rulings above momentary impulses, which answers the "which self" critique. Treat one-off fluctuations as noise.
- Generic: base layer, demo corpus, delta format. Principal-specific: the delta. This is the principle that most directly serves goal 2.
- Rules out: per-user systems built from scratch; chasing complete life capture.

## 9. Beliefs about the principal are reviewed propositions with evidence and provenance.

- Sources: https://arxiv.org/abs/2505.10831, https://ansuz.sooke.bc.ca/entry/23, https://theshamblog.com/an-ai-agent-published-a-hit-piece-on-me-part-2/, https://gwern.net/style-guide, https://gwern.net/rnn-metadata
- For Exo: Each belief ("A prefers X") carries confidence, links to log evidence, author (A, which agent, which source) and history. Changes to the profile or to an agent's own charter land as diffs the principal can review; no agent edits its own "soul" file silently. Style and rules live in one greppable pack with an explicit conflict rule and inline exception syntax, and agents must report gaps with a proposed fix.
- Generic: proposition schema, diff review flow, conflict rule. Principal-specific: A's beliefs and his conflict rule (newer ruling wins unless the older guards something irreversible or public).
- Rules out: self-modifying agent personas; unsourced profile lines; memory that cannot answer "why do you believe this?"

## 10. Content is data; instructions come only from the principal's channel.

- Sources: https://gwern.net/guardian-angel (Too Helpful, Personality Emulation), https://www.lesswrong.com/posts/d8xDGzCEYE639qqEv/a-mechanistic-explanation-of-prompt-injection-and-why-you, https://www.lesswrong.com/posts/siWqHqCSybdhtWGud/guardian-angels-llm-personalization-for-productivity-and (Thane Ruthenis), https://gwern.net/nenex (footnote on imitation and security)
- For Exo: Knowing the principal makes injected commands look absurd, but persona is not a security boundary on its own: models infer roles from style and are fooled by fake reasoning or "User:" cues. So mark provenance outside the token stream where possible, quote and label all external content, accept instructions only from authenticated principal channels, and check actions against "would this principal do this?" as a second layer.
- Generic: provenance tagging, channel authentication, action plausibility check. Principal-specific: the plausibility model.
- Rules out: trusting role tags; agents that act on instructions found in email, web pages or tool output.

## 11. Gate by reversibility and exposure, not by step count.

- Sources: https://gwern.net/doc/reinforcement-learning/safe/1960-wiener.pdf, https://gwern.net/tool-ai, https://theshamblog.com/an-ai-agent-published-a-hit-piece-on-me-part-2/, https://news.ycombinator.com/item?id=48906041
- For Exo: Reversible, private actions run fast and get logged. Irreversible, public, financial or third-party-affecting actions need the purpose confirmed before launch. Operator rules that protect third parties sit outside the principal model, since amplification is neutral and would amplify a bad actor too. Every external act is attributable to a named principal.
- Generic: action classifier, gates, attribution. Principal-specific: A's list of gated categories and his posting rules.
- Rules out: uniform approval for everything; anonymous agents acting in public; letting the principal model override operator safety rules.

## 12. Evaluate on n=1, longitudinally, with held-out decisions.

- Sources: https://gwern.net/guardian-angel (Benchmarks, Demo Appeal), https://www.lesswrong.com/posts/NwrbrHmkDuKPXgLFb/screencasts-could-be-scalable-data-evals-for-single-user, https://freesystems.substack.com/p/training-ai-to-govern-for-us, https://arxiv.org/abs/2503.22828, https://gwern.net/blog/2025/good-ai-samples, https://github.com/shawwn/hnsim, https://dynomight.net/automated/
- For Exo: Core metrics: (a) predict the principal's next ruling, choice or edit before showing it, scored against what they did; (b) endorsement rate and edit distance on drafts; (c) rounds of correction per accepted output, which should fall; (d) does adding a memory raise the likelihood of the principal's actual next decision (VR-CLI style). Judge persona over many samples, and check stance and conclusions, not just voice. Collect held-out decisions before building the profile they test.
- Generic: eval harness and metrics. Principal-specific: the held-out set and the one judge.
- Rules out: public leaderboards as Exo's target; single-sample demos; "it sounds like him" as the bar.

## 13. Elicit what is unrecorded, with questions chosen for information.

- Sources: https://gwern.net/optimal-interview, https://gwern.net/interview-inkhaven (interview prompt), https://gwern.net/interview-dwarkesh, https://freesystems.substack.com/p/training-ai-to-govern-for-us, https://gwern.net/guardian-angel (Active Learning)
- For Exo: Preferences, judgments, strength of views and trade-offs cannot be inferred from logs of routine days. Run the interview prompt (brainstorm 20+ questions, imagine 3 answers each, ask the top few) across several models, prime it with a researched profile, and score each question afterward by how much it changed the profile. Push toward hard and unsettled cases, where proxies fail most. Drop questions into normal work before preference-dependent tasks.
- Generic: interviewer, value-of-information scoring, question bank. Principal-specific: answers and priming profile.
- Rules out: fixed onboarding forms; passive lifelogging as the main data source; questions the system could have answered from the log.

## 14. Mirror reflective values, and keep a critic that argues back.

- Sources: https://gwern.net/guardian-angel (Mental Sovereignty, Self Actualization, Brand Safety), https://www.lesswrong.com/posts/siWqHqCSybdhtWGud/guardian-angels-llm-personalization-for-productivity-and (averyzlim, Antariksh, Samuel Ratnam), https://gwern.net/blog/2024/virtual-comments, https://gwern.net/creative-benchmark
- For Exo: Two separate roles: a twin that predicts the principal, and a counselor or critic that challenges plans and blind spots. Critique can come from simulated reviewers the principal respects, filtered hard and labeled as AI, with any first-person "evidence" stripped. No external optimization pressure inside the principal's private space, and no sanding of the principal's voice for brand safety. Guard the principal's own voice and skill from drifting toward model style.
- Generic: twin and critic roles, virtual-review primitive. Principal-specific: whom the critic simulates and what A wants challenged.
- Rules out: sycophantic mirrors; vendor values injected into private work; generic "criticize this" prompts.

## 15. Spend idle compute on background thinking that compounds.

- Sources: https://gwern.net/ai-daydreaming, https://gwern.net/anti-spaced-repetition, https://gwern.net/nenex (outdated pages, brainstorming), https://gwern.net/interview-inkhaven (Feynman's open problems), https://gwern.net/guardian-angel (Cost: "if it's sitting idle, something has gone wrong")
- For Exo: Night-shift jobs: daydream loop pairing random memories with a critic scoring novelty and usefulness; anti-spaced resurfacing of old notes and rulings a few times per lifetime; staleness sweeps that propagate a new fact through memory and docs; testing new reading against the principal's open-problems list. Surface only top hits in the morning brief; feed hits back into memory.
- Generic: the job types and schedulers. Principal-specific: the open-problems list, the critic's taste, the budget.
- Rules out: idle systems between prompts; daydream output dumped unfiltered on the principal.

## 16. Report through a summary hierarchy and show only the top few.

- Sources: https://gwern.net/blog/2024/rss, https://gwern.net/polish, https://gwern.net/blog/2024/virtual-comments ("silence is golden"), https://gwern.net/doc/sociology/technology/1983-bainbridge.pdf
- For Exo: Raw log, then per-task summaries, then daily brief, then weekly digest, built by recursive summarization and each linking down to evidence. The principal reads at the level they choose. Rank everything; only the top items reach them. Better to waste compute than principal time.
- Generic: summary tree and ranking. Principal-specific: levels, cadence, format (A: result first, numbers in a table, numbered decisions with defaults).
- Rules out: raw diff feeds; long reasoning dumps; polishing low-ranked output.

## 17. Models are swappable parts; choose for coverage and diversity.

- Sources: https://gwern.net/complement, https://gwern.net/creative-benchmark, https://arxiv.org/abs/2510.15020, https://arxiv.org/abs/2510.01171, https://www.lesswrong.com/posts/f5DKLsTsRRhbipH4r/llm-assistant-personas-seem-increasingly-incoherent-some, https://gwern.net/guardian-angel (Low Cost, Local Models), https://arxiv.org/abs/2104.03113
- For Exo: Exo's value is the log, user model and harness; models are complements kept competing. For drafting and brainstorming, measure diversity and collapse, not rank; a collapsed model cannot be rescued by sampling. Base-model character is unstable across releases, so the stable identity comes from Exo's pack and charter. More reasoning per task has limits; durable gains come from updating what Exo knows.
- Generic: model router, diversity checks, charter re-application. Principal-specific: which models A approves (model choice is his call).
- Rules out: lock-in to one vendor's memory; choosing the cheapest model by default for taste-heavy work; borrowing identity from the base model.

## 18. Host for the user, prove it cryptographically, and treat inferences as private data.

- Sources: https://moxie.org/2022/01/07/web3-first-impressions.html, https://gwern.net/guardian-angel (Hardware), https://www.eqtylab.io/blog/verifiable-compute-press-release, https://gwern.net/sae-truesight, https://arxiv.org/abs/2406.14546, https://gwern.net/nenex (upstream demonstrations footnote), https://gwern.net/archiving
- For Exo: Most users will not run servers, so Exo hosts, but signs every memory write and response so trust does not rest on the host (Divan Bus already does signed, hash-chained relay). Long-term option: attested hardware. Privacy covers what a model can infer from the principal's text, not only what is stored; audit outbound text for inferable facts. Improvements sent upstream must be stripped of private data, an open problem. Snapshot what the principal reads at ingest so the log stays replayable.
- Generic: signing, audit, upstream filter, archiving. Principal-specific: A's never-share list (personal data, family, My Drive/backup).
- Rules out: self-hosting as the default for everyone; plain SaaS with no verifiability; privacy reviews that only check explicit fields.

---

## Ideas that work for only one user, or only generically

Only for one user (or a few unusual users):
- Training a "Gwern Branwen Transformer" on a ~1GB personal corpus (guardian-angel, GBT). Most users have no such corpus. A partly fits: rich session logs and hafiz exist, but far less long-form public writing. Use for A as a pilot; do not assume it for others.
- Truesight of known authors and simulated critics of named people (sae-truesight, virtual-comments). Works only when the person is well represented in pretraining. A is not; simulated reviewers must be people A respects who are well known.
- "Write more online" to steer future base models (writing-online). Needs a public persona and accepts exposure; for A it is gated by his posting rules.
- Screen-capture data (screencast post). Feasible for one consenting power user; heavy privacy cost for general users.

Only generic (needs many users or open weights):
- Population priors over decisions (Centaur) and global preference datasets that individual GAs contribute to.
- Public demo corpus for warm-start, and upstream demonstration sharing (Nenex).
- Learned optimal interviewer from psychometric (Q, A, ID) data (optimal-interview); needs datasets across many people.
- SAE persona vectors (sae-truesight); need open-weight models and research effort.
- Group GAs (a "Congress GA") and politics use cases; not relevant to single-principal Exo.

## Tensions with A's rulings, resolved (see DECISIONS.md D-006)

- Cost: gwern says a GA should cost over $1,000 a month and warns against token-optimizing. A is frugal by default but "unlimited for a defined purpose". Proposed reconciliation: spend freely on personalization and consolidation (bits that compound, principle 15 and good-ai-samples), stay frugal on routine agent calls.
- Local models: gwern rejects local-first for users; A's machines run agents locally. Keep the log local and portable; let compute be wherever it is cheapest and verifiable.
- Weight-level learning: most of gwern's mechanism (dynamic evaluation) needs open weights; A's current stack uses frontier APIs. Near term, Exo implements the same loop at the prompt and memory layer (principal pack, consolidation, adapters later), while logging in a form that can train weights when that becomes worth it.

### Resolutions adopted
1. **Cost.** Spend on what compounds (consolidation, interviews, evaluation, night-shift reflection) under
   a monthly ceiling A sets; stay frugal on routine agent calls with cheap or local models. Model choice
   stays A's call (his budget rule); Exo reports cost per accepted output so he can decide with numbers.
2. **Local versus hosted.** The log and principal store are local-first and portable (A's machines,
   signed, git-backed). Compute runs wherever is cheapest and verifiable. For other users, a hosted
   option comes later; the store format is the same either way.
3. **Weights versus prompts.** v1 implements the guardian-angel loop at the memory and prompt layer
   (principal pack, consolidation, retrieval). Every event is logged in a training-ready form so an
   adapter can be trained when it becomes worth it (phase 3 of the hafiz roadmap).
