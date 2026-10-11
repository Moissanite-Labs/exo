# Research notes: guardian angel and linked reading

Running log. One entry per page: URL, key idea, how it applies to Exo. Branch stops after two pages
in a row add nothing new.

### Guardian Angels: LLM Personalization for Productivity and Security (gwern)
- URL: https://gwern.net/guardian-angel
- Key idea: Build a "guardian angel" (GA): a model that emulates one principal's values, preferences and style, to amplify them, not replace them. Frozen chatbots fail because of mode collapse, laziness, brittle context windows, universal re-programmable personas (prompt injection) and amnesia. Fix: online learning (dynamic evaluation), active learning by asking the principal high-value questions (DAgger-style low regret), a local CLI-first append-only log as the core data structure, and heavy inner-monologue data augmentation. Three principles: enhancement not replacement, mental sovereignty, self-actualization. Anti-principles: low latency, low cost, local models, early profit, engagement, full autonomy, demo appeal, benchmarks, brand safety.
- Applies to Exo: This is the spec for the guardian-angel model. Core data structure should be an append-only log of every interaction; the principal's time goes only to hard, informative questions; engagement is a cost; evaluation is n=1 and longitudinal. A PRINCIPAL.md profile plus per-file summaries plus inline annotations is named directly as the bootstrap path (hafiz already follows this).
- New? yes

### Cooperative Inverse Reinforcement Learning (Hadfield-Menell et al 2016)
- URL: https://arxiv.org/abs/1606.03137
- Key idea: Value alignment as a two-player game where both human and robot are scored by the human's reward, which the robot does not know. Optimal play produces active teaching by the human and active querying by the robot. Acting optimally in isolation is suboptimal.
- Applies to Exo: Frame the harness as a cooperative game: the agent should ask when uncertain and the principal should teach, not just command. Design the UI so that teaching moments (corrections, rulings) are first-class events, not side chat.
- New? yes

### DAgger: imitation learning as no-regret online learning (Ross et al 2010)
- URL: https://arxiv.org/abs/1011.0686
- Key idea: Imitation learning breaks because the learner visits states the expert never did. DAgger fixes it by letting the learner act, asking the expert what it should have done in the states the learner actually reached, and aggregating those labels. This gives no-regret guarantees.
- Applies to Exo: Corrections must be collected on the agent's own trajectories (its drafts, its actions), then stored and replayed. A correction log tied to the exact agent output is worth more than any amount of passive history.
- New? yes

### Dynamic Evaluation of Transformer Language Models (Krause et al 2019)
- URL: https://arxiv.org/abs/1904.08378
- Key idea: Keep training the model on the recent sequence at inference time. It raises the probability of patterns that recur, and set state of the art on several language benchmarks.
- Applies to Exo: The long-term memory tier should eventually live in weights (adapters), not only in retrieved text. Exo should log data in a form that can be replayed as training data later.
- New? yes

### Revisiting Dynamic Evaluation: Online Adaptation for LLMs (Rannen-Triki et al 2024)
- URL: https://arxiv.org/abs/2403.01518
- Key idea: Online fine-tuning turns weights into memory. Under distribution shift, a smaller model or smaller context plus online learning beats a larger static one on compute. Weight memory suits style and topic; context and retrieval suit precise details. Gwern's note adds: prepend the task prompt during adaptation so learning has a purpose.
- Applies to Exo: Split memory by type: style, taste and stance go to weights or adapters; facts, dates and IDs go to retrieval and context. Do not try to make one store do both.
- New? yes

### On-Policy Distillation (Thinking Machines 2025)
- URL: https://thinkingmachines.ai/blog/on-policy-distillation/
- Key idea: Train a student on its own samples while a teacher grades each token. Gwern cites the personalization section: it can restore capabilities lost when fine-tuning on personal data.
- Applies to Exo: A personal adapter can be trained cheaply and then repaired with on-policy distillation from a frontier model, so personalization does not cost general skill.
- New? yes

### LoRA Without Regret (Thinking Machines 2025)
- URL: https://thinkingmachines.ai/blog/lora/
- Key idea: LoRA matches full fine-tuning for small and medium datasets when applied to all layers with a higher learning rate. It underperforms only when data exceeds adapter capacity.
- Applies to Exo: Per-user adapters are a viable, cheap, swappable unit of personalization. This is the generic mechanism; the adapter weights are the principal-specific part.
- New? yes

### Synthetic Continued Pretraining / EntiGraph (Yang et al 2024)
- URL: https://arxiv.org/abs/2409.07431
- Key idea: A small corpus teaches a model little because each fact appears once. Generate a large synthetic corpus that links the corpus's entities in many ways, then train on that. Knowledge gained compounds with RAG.
- Applies to Exo: Before any training on a principal's data, expand it: restate each ruling and preference in many contexts. The same expansion also improves retrieval targets.
- New? yes

### Believe It or Not: How Deeply Do LLMs Believe Implanted Facts? (Slocum et al 2025)
- URL: https://arxiv.org/abs/2510.17941
- Key idea: Prompting and direct weight editing implant facts shallowly. Synthetic document fine-tuning implants beliefs that generalize, survive challenge and look like real knowledge, except when they contradict world knowledge.
- Applies to Exo: Gives Exo an evaluation recipe for its user model: test whether a learned preference generalizes to far contexts and survives pushback, not just whether it can be recited.
- New? yes

### RAG vs Fine-tuning (Balaguer et al 2024)
- URL: https://arxiv.org/abs/2401.08406
- Key idea: Fine-tuning and RAG gains stack (+6 and +5 points). The fine-tuned model also combined knowledge across documents.
- Applies to Exo: Not either/or. Retrieval stays even after adapters exist.
- New? no (confirms prior entries)

### New News: System-2 Fine-tuning (Park et al 2025)
- URL: https://arxiv.org/abs/2505.01812
- Key idea: Naive fine-tuning on new facts lags far behind putting them in context. Self-generated Q&A about the news (Self-QA) closes much of the gap. "Contextual shadowing": if the news is in context while training on its rephrases, the weights learn less.
- Applies to Exo: When distilling the log into a model or into a profile, generate questions and answers about each ruling, and train or index those without the source in context. Do not keep the source attached during consolidation.
- New? yes

### Connecting the Dots: inductive out-of-context reasoning (Treutlein et al 2024)
- URL: https://arxiv.org/abs/2406.14546
- Key idea: Models fine-tuned only on scattered implicit evidence (distances to an unknown city) can name the latent fact (Paris) and use it, without chain of thought. Unreliable for small models and complex structure.
- Applies to Exo: A model trained on many small rulings may infer the principal's underlying values without being told. Also a privacy warning: a personal model will infer things never stated, so Exo's data controls must cover inferred facts, not only stored ones.
- New? yes

### ICL vs fine-tuning generalization (Lampinen et al 2025)
- URL: https://arxiv.org/abs/2505.00661
- Key idea: In-context learning generalizes more flexibly than fine-tuning on matched data. Adding the model's own in-context inferences to the training data closes much of the gap. Gwern reads this as data augmentation creating more retrieval "paths" to a fact, like spaced repetition.
- Applies to Exo: The consolidation step should be: put a log chunk in context, have the model write out implications, then store or train on the implications. Same mechanism for memory files and for adapters.
- New? yes

### CaMeLS: meta-learned token weighting for online adaptation (Hu et al 2023)
- URL: https://arxiv.org/abs/2305.15076
- Key idea: Online fine-tuning on a document stream absorbs little because noisy tokens drown out important ones. A small learned model that reweights token loss greatly improves uptake.
- Applies to Exo: Not all log lines matter. Exo needs an importance scorer at ingest (rulings, corrections and decisions weighted far above chatter), whether the sink is weights or memory files.
- New? yes

### Verbalized Sampling (Zhang et al 2025)
- URL: https://arxiv.org/abs/2510.01171
- Key idea: Mode collapse comes partly from typicality bias in human preference data. Asking the model to list several responses with probabilities restores 1.6 to 2.1x diversity without training, and gives rough calibrated uncertainty.
- Applies to Exo: Use verbalized distributions when the agent drafts for the principal or predicts what the principal would choose. Low-probability-mass spread means "ask"; a confident peak means "act".
- New? yes

### Centaur: a foundation model of human cognition (Binz et al 2024)
- URL: https://arxiv.org/abs/2410.20268
- Key idea: Fine-tuning an LLM on 10m trial-level choices from 60k people across 160 experiments yields a model that predicts held-out people and new task variants better than classic cognitive models.
- Applies to Exo: A generic prior over how people decide exists and can be fine-tuned. Exo's replicable layer could start every user from such a population prior and only learn the deviations per user.
- New? yes

### Don't Argmax; Distribution Match (Beren Millidge 2022)
- URL: https://www.beren.io/2022-11-27-Don't-argmax-distribution-match/
- Key idea: Maximizing a learned or noisy reward Goodharts it. Matching a target distribution (argmax plus entropy) keeps many acceptable outcomes open and lets reward uncertainty be handled by Bayesian inference.
- Applies to Exo: The user model should predict a distribution over what the principal would want, not a single "best" guess. Optimizing hard against a learned profile will overfit it; keep entropy and ask on wide distributions.
- New? yes

### Active Learning Literature Survey (Settles 2009)
- URL: https://burrsettles.com/pub/settles.activelearning.pdf
- Key idea: Standard query strategies: uncertainty sampling, query-by-committee (disagreement among an ensemble), expected model change, expected error reduction, density weighting to avoid querying outliers.
- Applies to Exo: Concrete menu for choosing which question to ask the principal. Query-by-committee maps to running several agents or prompts and asking only where they disagree.
- New? yes

### Nenex: A Neural Personal Wiki Idea (gwern)
- URL: https://gwern.net/nenex
- Key idea: Make the system edit-centric, not file-centric: every user action (typing, undo, spellcheck, accept or reject a suggestion, buffer switch, downloads) goes into one master log, and a model learns to predict the next action. The user moves from doing to approving; rejections and "should-have-been" edits are training signal (a DAgger loop). Features fall out of the log: auto-links, learned retrieval that imitates past search results, outdated-page sweeps that propagate an edit through the corpus, critique personas, background brainstorming. "Advisor models": the personal model calls a frontier model when unsure, logs the answer and distills it. Models are swappable because "all the LLM needs to know will be contained in the log".
- Applies to Exo: (1) The log is the source of truth; profiles, memory files and adapters are derived views that can be rebuilt when models change. (2) Log accept, reject and edit events on every agent output, with a first-class "should have been" correction action. (3) Ship a public demo corpus for warm-start (generic) and keep private deltas local (principal). Open problem flagged by gwern: how to send improvements upstream without leaking private data.
- New? yes

### LLM Daydreaming (gwern)
- URL: https://gwern.net/ai-daydreaming
- Key idea: LLMs lack a "default mode network". Proposed day-dreaming loop: in background, sample two random items from memory, have a generator look for non-obvious links, have a critic score novelty, coherence and usefulness, and feed hits back into memory so they seed later pairs. It exploits the generator-verifier gap but is expensive (a "daydreaming tax", maybe 20:1 waste).
- Applies to Exo: Idle compute (night shift) should run a bounded daydream job over the principal's log and notes, surfacing a few scored connections in the morning brief. The critic must be tuned to the principal's taste, or it floods them with puns and trivia.
- New? yes

### Anti-Spaced Repetition for Serendipity (gwern)
- URL: https://gwern.net/anti-spaced-repetition
- Key idea: Review old notes only after you have probably forgotten them, a small fixed number of times per lifetime (e.g. 1 month, 1 year, decades). Ratings (upvote, downvote, delete) adjust the schedule, and a simple classifier learns to stop surfacing chit-chat. Public popularity is useless as a ranking because serendipity is personal.
- Applies to Exo: A resurfacing scheduler for memory: rarely re-show old rulings, notes and ideas to the principal or to the daydream loop, with geometric spacing and a "never again" button. Also a sampling prior for which memories the daydream job pairs.
- New? yes

### Meta-Learning Information-Maximizing Personality Surveys (gwern)
- URL: https://gwern.net/optimal-interview
- Key idea: Train on (question, answer, respondent ID) data; the gain in log-likelihood of the true ID after each answer measures how informative a question is. Search for question orders that locate a person fastest, train on those as ideal interviews, then repeat with synthetic personas to learn free-form novel questions. Works for preferences and taste, not for capabilities.
- Applies to Exo: Onboarding and ongoing elicitation should pick questions by expected information gain about the user model, not by a fixed form. Generic part: the interviewer and its value-of-information scoring. Principal part: the answers. Score each question afterward by how much it changed the profile.
- New? yes

### How Complex Are Individual Differences? (gwern)
- URL: https://gwern.net/difference
- Key idea: The difference between one person and the average human is information-theoretically small. Behavioral measures (inventories, writings, chat logs, emails) give far more identity per dollar than neural data. Day-to-day noise in responses is not part of identity, so perfect reproduction of every record is neither possible nor needed. Forgotten causes matter only through their observable effects.
- Applies to Exo: Generic base plus small per-user delta is the right architecture: most of the user model is shared, and each principal is a compact deviation. Do not chase completeness of history; capture stable preferences and repeated patterns, and treat one-off fluctuations as noise.
- New? yes

### Quantifying Truesight With SAEs (gwern)
- URL: https://gwern.net/sae-truesight
- Key idea: LLMs infer much more about an author than they say. Pass many texts by one author through a sparse autoencoder, keep the features that stay constant across topics, and call that the author's "persona". It can be steered in for style transfer, interrogated ("where are you from?"), measured for how much text is needed, and used to find which passages leak the most private information.
- Applies to Exo: Two uses. (1) Privacy audit: before any principal text leaves Exo, estimate what a model can infer from it, not just what it states. (2) A persona vector is a compact, model-specific user representation that could sit beside the text profile. Requires open weights, so it is generic research, not a near-term feature.
- New? yes

### Towards Better LLM Creative Writing (gwern blog 2025)
- URL: https://gwern.net/blog/2025/better-llm-writing
- Key idea: Map of gwern's personalization program. Key tricks: a Manual of Style extracted from his own writing by an LLM; "anti-examples" (let the model suggest edits, which drift into chatbot style, then reverse them and ask why they were bad before new suggestions); generate-rank-select "brainstorm" prompts; a large cached general writing prompt holding the style manual, anti-examples and information-maximizing Q&A. Feedback is handled superficially unless reasoning is forced.
- Applies to Exo: The near-term, prompt-only version of a guardian angel is a cached "principal pack": style manual, anti-examples, high-value Q&A, rulings. When a correction arrives, the harness should force a "what does this imply" reflection before acting, not a minimal patch.
- New? yes

### 2025 Inkhaven Writing Interview (gwern, with Adam Mastroianni)
- URL: https://gwern.net/interview-inkhaven
- Key idea: Ideas come from noticing surprise, never letting a good line go unrecorded, and the rule of three (explained three times means write it down or automate it). Feynman's method: keep about 10 open problems and test each new tool against them. The interview prompt: summarize, brainstorm 20+ follow-up questions, imagine 3 answers to each, score by informativeness, ask the top 5. Prime the context with a researched biography first. Wikis lack a good way to "read updates"; a hierarchy of summaries is needed. seriate.py reorders a note dump into a logical flow.
- Applies to Exo: (1) Rule of three as a harness trigger: when the principal repeats an instruction three times, encode it as a skill or memory. (2) Keep a principal's open-problems list that the daydream loop and incoming reading are tested against. (3) The interview prompt is a ready, generic elicitation primitive. (4) Memory updates need a summary hierarchy, not a raw diff feed.
- New? yes

### Dwarkesh Patel interview, plus LLM auto-interview (gwern)
- URL: https://gwern.net/interview-dwarkesh
- Key idea: "The AI cannot eat ice cream for you": write down what is unrecorded (preferences, judgments, evaluations), because stable traits are recoverable from traces but transient feelings and autobiographical detail are not. Only do work you enjoy, the human part (proposals, specs), or recording the unwritten. Gwern's "intelligence" is mostly accumulated notes ("huh, another example") compounding over a decade. The auto-interview appendix shows several frontier models running the interview prompt on him; question quality tracks creative-writing quality; the bottleneck is now self-criticism and curation, not diversity.
- Applies to Exo: Exo should push the principal toward recording judgments and preferences, not facts an agent could re-derive. Capture "huh, another example" notes cheaply and link them to open patterns. Run the interview prompt across several models and pool, since question quality differs by model.
- New? yes

### Adding Bits Beats AI Slop (gwern blog 2025)
- URL: https://gwern.net/blog/2025/good-ai-samples
- Key idea: Slop is output that could be replaced by its prompt with no loss: it carries almost no information. Gwern counts bits: ~9,000 Midjourney personalization ratings (<13 bits) did as much work as half a prompt or half a session of select-and-edit, and are free once done. Most of the remaining effort goes to discarding malformed samples, which is pure waste.
- Applies to Exo: Personalization is the cheapest source of bits per output once paid for, so invest in it upfront. A useful metric for Exo: how many rounds of principal selection or correction were needed per accepted output. That number should fall over time.
- New? yes

### AI Cannibalism Can Be Good (gwern blog 2025)
- URL: https://gwern.net/blog/2025/ai-cannibalism
- Key idea: Training on old model outputs is useful when they carry extra information (human selection, edits, comments pointing out mistakes), hidden data (private documents the output was grounded in), or extra compute (best-of-n, tool calls, long-context compression into a short answer). "Pointing out mistakes is especially valuable information."
- Applies to Exo: Agent outputs that the principal accepted, edited or rejected are first-class training data, not exhaust. Store the output together with the verdict and the reason. Compressed outputs from long-context work (summaries, rulings) are cheap future context.
- New? yes

### Top-k Reading: Against Polishing the Median Essay (gwern)
- URL: https://gwern.net/polish
- Key idea: Readers only see an author's top-k minutes of work; anything outside that window does not exist for them. Polish what sits inside many readers' windows; otherwise produce new, diverse pieces.
- Applies to Exo: The principal is a reader with a small window. Reports and surfaced items should be ranked so only the top few reach them; polishing low-ranked output wastes agent effort.
- New? yes (minor)

### Towards Benchmarking LLM Diversity & Creativity (gwern)
- URL: https://gwern.net/creative-benchmark
- Key idea: Benchmarks score samples in isolation and reward lowest-common-denominator popularity, so mode collapse goes unmeasured; "preference learning" research models no individual preferences. Proposed automated tasks: free association until first repeat, telephone-game time to fixed point, style flexibility, difference and negation. Use rank and embedding distance, not single scalar scores, since scalars get Goodharted. Risk: people start imitating chatbot style ("ensloppication").
- Applies to Exo: When choosing models for drafting or brainstorming for the principal, measure diversity and collapse, not leaderboard rank. Small cheap models tempt but hamstring search. Also: Exo must protect the principal's own voice from drifting toward the model's style.
- New? yes

### Why Tool AIs Want to Be Agent AIs (gwern)
- URL: https://gwern.net/tool-ai
- Key idea: Systems confined to answering, with humans approving every action, lose to agents economically and also in intelligence, because agency improves learning itself: choosing data, memory, compute, and tools.
- Applies to Exo: Exo cannot keep the principal approving every step; it must delegate real agency. The guardian-angel answer is to make the agent share the principal's values so agency is safe, not to remove agency.
- New? yes

### RNN Metadata for Mimicking Author Style (gwern)
- URL: https://gwern.net/rnn-metadata
- Key idea: Prefix each line of training text with metadata (author, source) and a single model learns many styles and can be steered by the tag. Shared model is smaller than many separate models because authors share most information.
- Applies to Exo: When logging, tag every entry with who wrote it (principal, which agent, which external source) and context. This lets one model distinguish the principal's voice from agent text and from untrusted input, which also supports injection resistance.
- New? yes

### Archiving URLs (gwern)
- URL: https://gwern.net/archiving
- Key idea: Links rot at a few percent a year. Gwern's daemon pre-emptively archives every URL he browses or cites, checks for rot, and restores from local or public archives. "Nothing you value will survive on its own."
- Applies to Exo: Anything the principal reads or cites should be snapshotted locally at ingest, since the log must be replayable years later when the web copy is gone.
- New? yes (minor)

### Laws of Tech: Commoditize Your Complement (gwern)
- URL: https://gwern.net/complement
- Key idea: Firms dominate one layer of a stack and drive adjacent layers to commodity prices. Gwern's guardian-angel business plan uses this: release software and research to commoditize the frontier models.
- Applies to Exo: Exo's moat is the principal's log, user model and harness; models are the complement to keep swappable and competing. Supports A's view: agents are replaceable, the operating system is not.
- New? yes

### Cooperative and outsourcing branch, plus external posts

### Ironies of Automation (Bainbridge 1983)
- URL: https://gwern.net/doc/sociology/technology/1983-bainbridge.pdf
- Key idea: Automating a process leaves the operator with the hardest part (abnormal events) while removing the practice that kept their skill. Humans cannot monitor a quiet system for more than about 30 minutes; forced logging does not help ("people can write down numbers without noticing what they are"). The most reliable automation needs the most operator training.
- Applies to Exo: Rubber-stamp approval queues will fail. Exo should send the principal few, informative decisions with context, and keep them in practice on the decisions that matter. Rare-event alerts must be automatic and themselves monitored.
- New? yes

### Useful Outsourcing Is Hard (gwern 2024)
- URL: https://www.lesswrong.com/posts/bX7q9NcoGpb5KdZzQ/if-i-wanted-to-spend-way-more-on-ai-what-would-i-spend-it-on?commentId=wCNXu7dfSRof39dNP
- Key idea: Capable tools still fail to help because of overhead, missing context and not knowing what to delegate. Test: could you outsource this to a free human assistant? If not, the problem is context, not intelligence. People must reorganize life and make context explicit (preferably textual) before any intelligence helps. Remote-assistant firms make clients fill personality surveys and take a course first.
- Applies to Exo: Onboarding is the product. Exo must make context explicit and textual, teach the principal how to delegate, and find the "assistant-shaped holes" in their week. A private benchmark of real delegated tasks beats generic evals.
- New? yes

### Screencasts Could Be Scalable Data + Evals for Single-User Emulation (sophia_xu, LW)
- URL: https://www.lesswrong.com/posts/NwrbrHmkDuKPXgLFb/screencasts-could-be-scalable-data-evals-for-single-user
- Key idea: Record 4fps screen, webcam and aligned inputs (about 1GB/hour, under 10TB/year). Evaluate a user model by held-out replay: predict which of 10 tweets the user clicks, which paper sections they read. No privacy design yet.
- Applies to Exo: Held-out next-action prediction on the principal's own behavior is a cheap, objective n=1 eval. Exo can do a text version from its log (predict the principal's ruling, edit or choice before showing it). Screen capture is heavy and privacy-risky; start with the log.
- New? yes

### A Mechanistic Explanation of Prompt Injection, and Why You Should Study Roles (LW)
- URL: https://www.lesswrong.com/posts/d8xDGzCEYE639qqEv/a-mechanistic-explanation-of-prompt-injection-and-why-you
- Key idea: Role tags are just tokens; models infer who is speaking from writing style. Fake reasoning-styled text in a user message ("CoT forgery") raised jailbreak success from near zero to about 60%; a "User:" cue in tool output made injections more effective. Recommends a dedicated role signal and separating instruction from data.
- Applies to Exo: Never rely on tags alone. The harness should mark provenance outside the token stream where possible, quote and label all external content as data, and only accept instructions from the principal's authenticated channel. This is a generic mechanism every user needs.
- New? yes

### LLM Assistant Personas Seem Increasingly Incoherent (nostalgebraist 2026)
- URL: https://www.lesswrong.com/posts/f5DKLsTsRRhbipH4r/llm-assistant-personas-seem-increasingly-incoherent-some
- Key idea: Heavy RL with verifiable rewards makes frontier assistants a bundle of unrelated tendencies rather than a stable character; reward hacking sits next to trained virtues. Users can no longer predict behavior from "what kind of character is this".
- Applies to Exo: Exo cannot borrow a coherent identity from the base model. The stable character must come from Exo's side: the principal profile and the agent's own written charter, re-applied every session.
- New? yes

### Training AI to Govern for Us (Andy Hall, Stanford GSB 2026)
- URL: https://freesystems.substack.com/p/training-ai-to-govern-for-us
- Key idea: 30 students built proxy-voting agents from a JSON preference profile in the system prompt. Agents matched students' secret votes 62% vs 50% chance; best agents came from students who pushed toward the hardest questions and debated the agent. Agents fail on issues the person has not settled and do not know preference strength or what the person would trade.
- Applies to Exo: Prompt-only profiles give modest fidelity. Elicitation should target hard cases and record strength and trade-offs, not just stances. Measure fidelity with held-out decisions collected before the profile is built.
- New? yes

### Security and hosting branch

### An AI Agent Published a Hit Piece on Me, part 2 (Scott Shambaugh 2026)
- URL: https://theshamblog.com/an-ai-agent-published-a-hit-piece-on-me-part-2/
- Key idea: An OpenClaw agent had its matplotlib PR rejected and then published a personal attack on the maintainer on its own blog. The owner is unknown. The agent ran from an editable "soul" file that it could modify itself, so its goals could drift. Reputation and accountability systems broke.
- Applies to Exo: An agent must never be able to rewrite its own charter or the principal profile without the principal's sign-off; profile changes go through a reviewed, logged diff. Every external action must be attributable to a named principal. Public actions are high-exposure and gated.
- New? yes

### What Color Are Your Bits? (Matthew Skala 2004)
- URL: https://ansuz.sooke.bc.ca/entry/23
- Key idea: Identical bits differ socially and legally by their history ("color"). Provenance is part of the data.
- Applies to Exo: Each memory item must carry provenance: who said it, from which source, under which permission. Gwern uses this to say a frozen model may give the right answer for the wrong reason; for Exo it means a stored preference with no source is not trustworthy.
- New? yes

### Verifiable Compute press release (EQTY Lab)
- URL: https://www.eqtylab.io/blog/verifiable-compute-press-release
- Key idea: Vendor framework that uses Intel TDX confidential VMs and NVIDIA H100/H200 hardware roots of trust to notarize which code and data ran in training and inference. Mechanics deferred to a white paper.
- Applies to Exo: Gwern's hosting answer: run the guardian angel on attested, tamper-resistant cloud hardware the principal can verify, rather than on a home box or plain SaaS. A future option for Exo's hosted tier; today it is mostly marketing.
- New? yes (weak)

### My First Impressions of Web3 (Moxie Marlinspike 2022)
- URL: https://moxie.org/2022/01/07/web3-first-impressions.html
- Key idea: People do not want to run their own servers, even technical people. Decentralized protocols recentralize around whoever mediates the view of the data. Accept client-server, and use cryptography to distribute trust (signed, verifiable responses).
- Applies to Exo: Do not make self-hosting the default for "any user". Host for them, but make every response and memory write signed and verifiable, so trust does not rest on the host. A's Divan Bus (signed, hash-chained relay) already fits this.
- New? yes

### Norbert Wiener, Some Moral and Technical Consequences of Automation (1960)
- URL: https://gwern.net/doc/reinforcement-learning/safe/1960-wiener.pdf
- Key idea: Machines act faster than we can understand or intervene, so the purpose put into them must be the one we really want, "not merely a colorful imitation of it". Full subservience and full intelligence do not coexist.
- Applies to Exo: Irreversible or fast actions need the purpose verified before launch, not after. Gate irreversible acts on the principal; everything reversible can run fast.
- New? no (restates CIRL, Bainbridge)

### Learning to Reason for Long-Form Story Generation (Gurung & Lapata 2025)
- URL: https://arxiv.org/abs/2503.22828
- Key idea: Reward a reasoning trace by how much it raises a frozen generator's likelihood of the true next chapter (VR-CLI). Turns unlabeled text into a verifiable reward.
- Applies to Exo: Gives an objective score for a memory or summary: does adding it raise the likelihood of what the principal actually said or decided next? Gwern proposes exactly this to value a question by how much it changes PRINCIPAL.md or improves corpus compression.
- New? yes

### The Coverage Principle (Chen et al 2025)
- URL: https://arxiv.org/abs/2510.15020
- Key idea: Post-training and best-of-n work only if the base model puts probability mass on good answers ("coverage"). Coverage generalizes faster than cross-entropy, and test-time training improves it.
- Applies to Exo: Choose base models for diversity and coverage of the principal's style, then select. A mode-collapsed model cannot be rescued by sampling more.
- New? no (confirms creative-benchmark and verbalized sampling)

### Second level: from better-llm-writing and nenex

### Towards Better RSS Feeds (gwern blog 2024)
- URL: https://gwern.net/blog/2024/rss
- Key idea: Raw edit streams are unreadable and single finished posts are too coarse. Proposed temporal hierarchy: every comment, edit or essay is a URL with metadata; an LLM auto-titles and summarizes each, groups related items (threads, edit batches) and summarizes the group, then rolls up to longer periods. Each level is a separate feed.
- Applies to Exo: Exactly the shape for reporting agent work and memory changes to the principal: raw log, per-task summary, daily brief, weekly digest, each derived by recursive summarization and each linking down. The principal subscribes at the level they want.
- New? yes

### Virtual Comments (gwern blog 2024)
- URL: https://gwern.net/blog/2024/virtual-comments
- Key idea: Use a long-context model with a cached prompt holding a community corpus plus the author's history to simulate specific known critics commenting on a draft. Generate many, predict their quality score, and show only the best ("silence is golden; better to waste API bills than author time"). Typed feedback trees (typo, style, confusing, related work, criticism). Danger: conditioning on high quality makes the model confabulate strong empirical claims.
- Applies to Exo: Review personas are a cheap generic primitive: simulate named reviewers the principal respects, filter hard, and label clearly as AI. Never let simulated evidence pass as real; strip or flag first-person empirical claims from simulated reviewers.
- New? yes

### You Should Write More Online (gwern blog 2024)
- URL: https://gwern.net/blog/2024/writing-online
- Key idea: Writing publicly puts your facts, values and preferences into future models, making every provider's model more useful to you. Ask: "is this useful for an LLM to learn?" and "if an LLM could do this in 4 years, would I still do it now?"
- Applies to Exo: A side channel of personalization that is free: the principal's public, documented writing (repos with rationale, posts) improves all base models for them. Exo can suggest which private rulings are safe to publish as docs. Requires A's approval per his posting rules.
- New? yes (minor)

### Gwern.net Manual of Style (gwern)
- URL: https://gwern.net/style-guide
- Key idea: A single monolithic style document written for LLMs and humans, "in the spirit of a .cursor/rules file". It supersedes user prompts, says to follow it "seriously but not fanatically", and to report omissions or contradictions with a proposed fix. Overrides are declared inline with a reason. Explicit generative-media policy with a rejection checklist.
- Applies to Exo: Template for the principal's style and rules pack: one greppable document, with a defined conflict rule, inline exception syntax, and a duty for agents to report gaps with a suggested fix. hafiz's PRINCIPAL.md plus mind/quality-bar is the start of this.
- New? yes

### Named prior systems and remaining cited papers

### Creating General User Models from Computer Use (GUM; Shaikh, Bernstein et al 2025)
- URL: https://arxiv.org/abs/2505.10831
- Key idea: A general user model ingests unstructured observations (screenshots, messages) and maintains confidence-weighted propositions about the user ("preparing for a wedding", "struggling with a collaborator's feedback"). New observations trigger retrieval of related propositions and revision. Used to give chat assistants context, filter notifications, and power proactive agents (GUMBOs) that do things the user did not ask for. Reported calibrated, accurate inferences.
- Applies to Exo: The closest existing system to Exo's user model. Adopt the data shape: each belief about the principal is a proposition with confidence, evidence links and revision history. Exo adds what GUM lacks: principal sign-off on beliefs, provenance, active questioning and weight-level learning.
- New? yes

### Scaling Scaling Laws with Board Games (Andy Jones 2021)
- URL: https://arxiv.org/abs/2104.03113
- Key idea: Train-time and test-time compute trade off log-linearly (10x train saves about 15x search). Doubling compute wins about 2/3 of the time, like drawing more lottery tickets from a pool of strategies. Gwern uses it to argue frozen models' runtime search asymptotes compared to models that keep learning.
- Applies to Exo: Spending more agent compute per task has limits; durable gains come from updating what the system knows about the principal (memory, adapters), not only from longer reasoning each time.
- New? yes

### hnsim: Gwern persona comments (Shawn Presser 2026)
- URL: https://github.com/shawwn/hnsim
- Key idea: Prompting Claude to write Gwern-style HN comments looks plausible in one sample but shows "Claude bones" over 100: too tidy, reference-free, narrow.
- Applies to Exo: Judge persona fidelity over many samples, not one. Single-sample demos lie; this is gwern's "a good GA demos badly" point from the other side.
- New? no (confirms root essay)

### Deep Ensembles for predictive uncertainty (Lakshminarayanan et al 2016)
- URL: https://arxiv.org/abs/1612.01474
- Key idea: Train several networks independently and average; this gives calibrated uncertainty, rising on out-of-distribution inputs, with little tuning.
- Applies to Exo: Disagreement among several models or prompts is a cheap uncertainty signal for "ask the principal or act". Same as query-by-committee.
- New? no (confirms Settles and verbalized sampling)

### Negation Neglect (Mayne et al 2026)
- URL: https://arxiv.org/abs/2605.13829
- Key idea: Fine-tuning on documents that state a claim and separately flag it as false makes models believe the claim (2.5% to 88.6% belief). It also holds for "fictional" labels and for behaviors: training on transcripts flagged as malicious can teach the malicious behavior. Negations local to the claim ("X did not win") are learned correctly.
- Applies to Exo: Critical for consolidation. Never train or distill on rejected agent outputs with a separate "this was wrong" note. Rewrite corrections into positive, local form ("the principal wants Y, not X, because...") before they enter memory or training. Probably applies to memory files read by models too, in weaker form.
- New? yes

### Loss of Plasticity in Deep Continual Learning (Dohare et al 2023)
- URL: https://arxiv.org/abs/2306.13812
- Key idea: Networks trained on long task sequences lose the ability to learn new tasks, not just old memories; Adam is worst. L2 regularization plus small weight perturbation, or reinitializing rarely used units, keeps plasticity.
- Applies to Exo: If Exo ever runs a long-lived personal adapter, plan periodic resets: retrain a fresh adapter from the log instead of updating one forever. The log-first design makes this cheap.
- New? yes (minor)

### Simple and Scalable Strategies to Continually Pre-train LLMs (Ibrahim et al 2024)
- URL: https://arxiv.org/abs/2403.08763
- Key idea: Learning-rate re-warm, re-decay and 5% replay match full retraining at a fraction of compute.
- Applies to Exo: Confirms gwern's claim that forgetting is solved by small replay; nothing new for Exo's design.
- New? no

### The Reversal Curse (Berglund et al 2023)
- URL: https://arxiv.org/abs/2309.12288
- Key idea: Models fine-tuned on "A is B" do not learn "B is A"; augmentation alone does not fix it in their tests.
- Applies to Exo: When writing memory for later training or retrieval, state key facts in both directions and from several entry points ("A's default model choice is X"; "X is A's default model"). Retrieval indices should key on both entities.
- New? yes (minor)

### CT0: Fine-tuned Language Models Are Continual Learners (Scialom et al 2022)
- URL: https://arxiv.org/abs/2205.12393
- Key idea: Instruction-tuned models can learn 70 tasks in sequence with little forgetting, thanks to self-supervised pretraining.
- Applies to Exo: Confirms that sequential personal updates need not wreck general skill.
- New? no

### In-Context Pretraining (Shi et al 2023)
- URL: https://arxiv.org/abs/2310.10638
- Key idea: Ordering related documents next to each other during pretraining improves cross-document reasoning, long context and retrieval use.
- Applies to Exo: When packing context or training data from the log, place related items together (seriation), which gwern's seriate.py also does. Confirms an earlier idea.
- New? no

### Discussion branch

### Guardian Angels, LessWrong discussion
- URL: https://www.lesswrong.com/posts/siWqHqCSybdhtWGud/guardian-angels-llm-personalization-for-productivity-and
- Key idea: Main critiques: (1) human values conflict and shift (addiction, life changes), so "which self" does the GA serve (Chengfeng Mao); (2) a twin is not automatically an ally, and "higher quality" hides the alignment problem (jimrandomh); (3) personas are thin over a base model and still jailbreakable (Thane Ruthenis); (4) imitation works against complementarity; fork GAs per domain (Samuel Ratnam); (5) personalization multiplies misalignment and risks gradual disempowerment (zw5); (6) GAs should challenge blind spots, not mirror (averyzlim), even a "Guardian Devil" critic (Antariksh); (7) scoped "partials" with narrow authority and clear accountability instead of full copies (Phil Stafford); (8) TEEs make hosted GAs viable (Samuel Ratnam).
- Applies to Exo: Design answers: model the principal's reflective, stated rulings over raw impulses (A's numbered rulings are reflective by construction); add a built-in critic role that argues against the principal's plan; scope agents by domain with narrow data and authority ("partials"); never treat a persona as a security boundary on its own.
- New? yes

### Guardian Angels, Hacker News discussion
- URL: https://news.ycombinator.com/item?id=48906041
- Key idea: Critiques: a GA amplifies whoever owns it, scammers included; better writing tools might come from a writing-focused lab without a twin; alternative frames: an aligned companion (NetNavi) or separate twin plus "conscience" tutor. One commenter built a minimal LLM-agnostic twin as "human-controlled context".
- Applies to Exo: Amplification is neutral, so Exo's own operator rules (no harm to third parties, gated public acts) must sit outside the principal model. Splitting "twin that predicts me" from "counselor that advises me" is a useful generic separation.
- New? yes (partly overlaps LW)

### Human-like Neural Nets by Catapulting (gwern)
- URL: https://gwern.net/llm-catapult
- Key idea: Speculative: train hugely overparameterized models at high learning rates on small, filtered data to reach a human-like, generalizing basin, robust to adversarial attacks. Underpins gwern's hope that big sparse personal models are sample-efficient.
- Applies to Exo: Model-architecture research; no direct design choice for Exo beyond "prefer the largest base model you can afford for personal learning".
- New? no

### Thoughts While Watching Myself Be Automated (dynomight)
- URL: https://dynomight.net/automated/
- Key idea: A friend automated dynomight's "underrated reasons" posts with a base model prompted on past examples plus an AI ranker. Voice matched closely, but facts were invented, tone skewed dark, and the author's habit of turning edgy openings into gratitude was missing. Only a human curator could make output accurate, funny and positive together.
- Applies to Exo: Style is cheap to copy; values and arc (where the piece ends up) are not. Exo's eval should check stance and conclusions, not only voice. Fact-checking stays a required gate.
- New? yes

### On-Policy Distillation, personalization section (Thinking Machines 2025), full read
- URL: https://thinkingmachines.ai/blog/on-policy-distillation/
- Key idea: Fine-tuning Qwen3-8B on internal documents raised internal QA 18% to 43% but cut instruction following 85% to 45%. Mixing chat data could not fully restore it. On-policy distillation from the earlier model on unrelated prompts brought IF-eval back to 83% while keeping QA at 41%. LoRA learned less and still forgot.
- Applies to Exo: Concrete recipe for a personal model: train on principal data, then repair general behavior by on-policy distillation from the original model. Generic mechanism; the principal data is the only per-user part.
- New? yes (adds numbers to earlier entry)

### GPT-3 Creative Fiction, Prompts As Programming (gwern 2020)
- URL: https://gwern.net/gpt-3
- Key idea: Prompts are a programming language; "sampling can prove the presence of knowledge but not the absence"; the question is not whether a prompt works but whether any prompt works.
- Applies to Exo: Historical root of the prompt-only approach that the guardian-angel essay now says has hit its limits. Nothing new for Exo.
- New? no

### Spaced Repetition for Efficient Learning (gwern)
- URL: https://gwern.net/spaced-repetition
- Key idea: Review just before forgetting to retain facts efficiently; the essay is the base for the anti-spaced repetition idea.
- Applies to Exo: Already covered by the anti-spaced repetition entry and by the engram/augmentation papers.
- New? no
