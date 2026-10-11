# Crawl log

Branch tree. Format: page -> children followed; stop reason. Stop rule: two pages in a row that teach nothing new for Exo ends a branch.

## Root: https://gwern.net/guardian-angel
Children grouped into branches below. Skipped: Wikipedia links (literature, law, generic concepts), news anecdotes on scams and slop (motivation only), fiction pages, neuroimaging scaling papers (out of Exo scope), corporate-structure links.

### Branch A: learning theory (CIRL, DAgger)
- cirl (1606.03137) -> new
- dagger (1011.0686) -> new

### Branch B: memory in weights (dynamic evaluation, continual learning)
- dynamic evaluation 1904.08378 -> new
- revisiting dynamic evaluation 2403.01518 -> new
- on-policy distillation -> new
- LoRA without regret -> new
- synthetic continued pretraining 2409.07431 -> new
- believe it or not 2510.17941 -> new
- RAG vs fine-tuning 2401.08406 -> not new (1)
- new news 2505.01812 -> new (resets count)
- connecting the dots 2406.14546 -> new
- ICL vs FT 2505.00661 -> new
- CaMeLS 2305.15076 -> new
- (continued below after essays)

### Branch C: uncertainty, diversity and active learning
- verbalized sampling 2510.01171 -> new
- centaur 2410.20268 -> new
- don't argmax (beren) -> new
- Settles active learning survey -> new

### Branch D: gwern essays linked from root (each is its own sub-branch)
- nenex -> new; children followed: AUNN, advisor models idea (see below)
- ai-daydreaming -> new
- anti-spaced-repetition -> new
- optimal-interview -> new
- difference -> new
- sae-truesight -> new
- blog/2025/better-llm-writing -> new
- interview-inkhaven -> new
- interview-dwarkesh -> new
- blog/2025/good-ai-samples -> new
- blog/2025/ai-cannibalism -> new
- polish -> new (minor)
- creative-benchmark -> new
- tool-ai -> new
- rnn-metadata -> new
- archiving -> new (minor)
- complement -> new

### Branch E: human factors, delegation and outside reactions
- Bainbridge, Ironies of Automation -> new
- Useful Outsourcing Is Hard -> new
- Screencasts for single-user emulation (LW) -> new
- Mechanistic prompt injection / roles (LW) -> new
- Assistant personas incoherent (LW) -> new
- Training AI to govern for us -> new

### Branch F: security and hosting
- AI agent hit piece (shamblog) -> new
- What color are your bits -> new
- Verifiable compute -> new (weak)
- Moxie web3 -> new
- Wired OpenClaw story -> fetch blocked (wired.com), skipped
- Wiener 1960 -> not new (1)
- (branch continued: see Branch G for remaining children)

### Branch B continued (memory in weights)
- Gurung & Lapata 2503.22828 -> new
- Coverage principle 2510.15020 -> not new (1)

### Branch G: second level from better-llm-writing and nenex
- blog/2024/rss -> new
- blog/2024/virtual-comments -> new
- blog/2024/writing-online -> new (minor)
- style-guide -> new
- aunn -> skimmed; memorization research already covered by dynamic evaluation entries; not logged as a page
- blog/2022/active-learning -> fetch returned a stub page, skipped

### Branch H: named prior systems and remaining papers
- GUM (2505.10831), reached via screencast post -> new
- Jones 2021 (2104.03113) -> new
- hnsim -> not new (1)
- deep ensembles (1612.01474) -> not new (2) -> branch C (uncertainty) STOPPED here; remaining uncertainty papers (2002.08791, 2002.06470, 2006.13570, 2104.14421, 2606.03938) skipped
- influence functions (2308.03296) -> skimmed abstract; same lesson as reversal curse and ICL vs FT; not logged as a page

### Branch B continued
- negation neglect (2605.13829) -> new (resets count)
- loss of plasticity (2306.13812) -> new (minor)
- Ibrahim continual pretraining (2403.08763) -> not new (1)
- reversal curse (2309.12288) -> new (minor)
- CT0 (2205.12393) -> not new (1)
- in-context pretraining (2310.10638) -> not new (2) -> branch B STOPPED. Skipped: Net2Net, ladder side nets, Pre-training under infinite compute, Slowrun, q0, smooth adversarial training (architecture details beyond Exo's scope).

### Branch I: discussion of the root essay
- LW discussion -> new
- HN discussion -> new (partial overlap)

### Branch D continued (second-level gwern and cited posts)
- llm-catapult -> not new (1)
- dynomight automated (from better-llm-writing) -> new (resets)
- on-policy distillation full read (curl/WebFetch, annotation was empty) -> new (numbers)
