# A's brief for Project Exo (verbatim)

Given 2026-10-11 22:34 UTC by Ahraz Arifuddin. This is the charter; DECISIONS.md records how it was interpreted.

We're laying the groundwork for Project Exo, our magnum opus: one system that gives a person everything they need to work well with models. It brings together the guardian-angel concept, the harness, the memory system, context and session management, and the rest. It should pull in everything I've built this year across every agent and harness I've used, including work I started months ago with you on my personal Claude account (we're on enterprise now, so that history is only in exports or local files, if anywhere).

**Base:** I'm leaning toward forking Hermes, because we've done a huge amount of work on that codebase through issues, PRs, code reviews, plugins, mods and skills. The fork would merge all of that and build the plugins and mods in natively. Test this choice. If you find a better base, go with it and record why.

**Must be fused in:**
- sym-2p: internal optimization
- protean-ordaprompt: intuitive, frictionless UI/UX
- optchat-memory: a fluid, continuing relationship with the agent
- subagent-viewer: one view of every running process and task
- raptora / continuum: a command centre for all projects
- the Protean system and its orchestration principles
- anything else relevant from github.com/ahrazzle, the aska-digital org, or local work in ~/.hermes/, ~/Documents/team6/, ~/Documents/, and the iCloud Documents backup (skip the Drive backup/ folder, as before)

**Research:** Read https://gwern.net/guardian-angel and every essay it links to, then follow links from those pages. Keep going until new pages stop giving you relevant ideas. Two pages in a row that teach you nothing new on a branch is a good signal to stop that branch. As you go, keep a running notes file with source URL, key idea, and how it applies to Exo.

**Work through these in order, then keep going:**
1. Inventory: every project, plugin, mod, skill and Hermes contribution that's a candidate for Exo, with location, status, and keep / merge / drop.
2. Research synthesis: the guardian-angel reading distilled into design principles for Exo.
3. Architecture doc: components, how the fused projects map onto the base, the memory and context model, and the migration path from what's running today.
4. Build: set up the repo and start building toward the architecture.

You own this end to end. Make the calls yourself, including the base, the architecture and the order of work. Don't wait on me. Keep a DECISIONS.md in the repo recording each significant choice, the reason for it, and what you rejected, so I can read along and redirect if I disagree. Only stop for something irreversible or outward-facing that my standing approvals don't cover.

The repo is github.com/aska-digital/exo. Use subagents for the crawl, the inventory and parallel build work so this session's context stays lean, and write a handoff file in ~/Work/handoffs/ after each phase so the work survives compaction or a session switch.

## Added during the work

2026-10-11 22:36 UTC:

> there's two key aspects to this task, building it to fit me as well as possible and making it replicable for any user - keep this duality in consideration throughout your work

2026-10-12 00:15 UTC (session practice):

> i dont want to run compaction under youve finished capturing the entire essence of my inputs and messages. lmk when youre ready, and if you want me to do it with any specific summarization parameters
