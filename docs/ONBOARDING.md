# Onboarding

Two different things share this page: teaching a new user how Exo works (section 1, D-030), and giving a new
principal a store (sections 2 and 3).

## 1. Learning Exo: `/intro` and tutorial mode

Exo has unfamiliar parts (one memory for all windows, no manual compaction, an approval gate, peers). Two
tools teach them, both in the terminal client (`exo`) and neither adds a protocol command.

### `/intro`
Sends a normal user turn (it shows as `/intro`). The agent loads the `exo-intro` skill
(`skills/exo-intro/SKILL.md`, the facts it teaches from) and gives a short, staged orientation: an overview,
a question about what you are used to (Claude Code, ChatGPT, Hermes), then topics you pick. It ends by
mentioning `/tutorial`. It is a conversation, so ask anything. (`/handoff` also uses its own skill, `exo-handoff`, for the note's template.) A test checks every slash command in `/help`
appears in the skill, so the two cannot drift.

### First run
`$EXO_HOME/state/onboarding.json` holds `{"onboarded": <time>, "hints_seen": [...]}`. When `onboarded` is
missing, `exo` runs `/intro` at launch and turns tutorial mode on. The marker is written when the intro
turn actually starts, so a crash before that shows the intro again next time. It does not run when you resume
a session (`--resume`, `--session`), and never in tests, `exo serve` or headless runs, because only the
`exo` entry point asks for it.
- `exo --no-intro` skips it and records the home as onboarded.
- `EXO_NO_INTRO=1` skips it for that launch and writes nothing.
- Delete the file (or run `/tutorial reset` for tips only) to see it again.

### Tutorial mode
`/tutorial on|off|reset`; bare `/tutorial` shows the state. On or off is saved as `tutorial.enabled` in
`$EXO_HOME/config.json`.
- **Tips** come from `clients/tui/exo_tui/hints.toml`, one per moment, each shown once, at most one per turn.
  They are dim "tip:" lines in the terminal only: the client draws them, so they cost no model tokens and
  are never in the conversation the model sees. Seen tips are kept in `onboarding.json`; `/tutorial reset`
  shows them again. A tip that loses its turn to another is not used up and appears at its next moment.
- **Moments:** first message sent, first memory summary, first approval, first message typed while a turn runs,
  first stop (ctrl+c), first model fallback, first peer message, first `/new`, first finding, first slow turn (a minute
  or more not counting approvals), first `code_find`. Each tip names the command that goes deeper.
- **The agent** also gets one line while the mode is on: the user is new, explain an unfamiliar Exo concept
  in a sentence. It is appended last in the stable system text (after the prompt and memory block, before the
  summary tree), so with the mode off the text is byte-identical to before and the prompt cache is untouched;
  switching it on or off costs one cache miss.

## 2. New store and interview

Two ways in. A new user starts from an empty store and an interview. Someone with an existing
hafiz-format store adopts it with `exo import-store`.


```sh
exo init sam --interview                       # ask on the terminal
exo init sam --interview --answers answers.yaml  # scripted, for setup and tests
exo init sam --interview --plan                # print the question order and stop
exo interview --principal sam                  # more questions later, on an existing store
```

`exo init` copies `templates/principal-store`, creates the ledger (0600), and makes the id the
active principal in `$EXO_HOME/config.json` when none is set.

### How questions are chosen (principle 13)

1. **Draft.** Candidates come from the bank: `templates/question-bank/*.yaml` (identity, work,
   decision rights, communication, quality bar, gates, rhythm, privacy; 32 questions with
   follow-ups), plus `<store>/method/question-bank/*.yaml` if the store adds its own.
2. **Score** each by value of information, given what is known:

   ```
   voi = weight/5 x unknown x spread x 1/(1 + asked_in_section)
   ```

   `unknown` is the share of the question's facets (`covers`) not yet known. Facets are known
   from `facets:` on nodes and from earlier `answer` events. A fully known question scores 0
   and is never asked. `spread` is `1 - 1/k` for `k` distinct imagined answers: the more ways
   the answer could go, the more it is worth asking. The section term spreads questions across
   topics.
3. **Ask** the best one, update what is known, add follow-ups, rescore. Stop at
   `--max-questions` (default 12; 0 = all). Enter skips, `q` stops.
4. **Write** each answer twice:
   - a `question` event (author `exo-onboard`) and an `answer` event (author = the principal,
     signed with `$EXO_HOME/keys/<id>.key`, `refs` = the question). The ledger is the truth.
   - a belief node per topic, rebuilt from the ledger: `confidence: low`, `kind: a-said`,
     `facets: [...]`, one `a-said` source per answer (`event:<id>`). A newer answer to the same
     question replaces the older one in the node.
5. **Queue the rest.** The next best unasked questions (`--seed-queue`, default 8) go to the
   `exo.ask` queue as `onboard-<id>`, so they reach the principal later inside the
   interruption budget.

Details:

- A template example node at the target id (tag `example` or a `template:` source) is replaced.
  A curated node is never overwritten; answers go to `<id>-said` with an `evidence-for` link.
- An answer that looks like a secret or contact detail (the store's leak patterns) stays in the
  ledger only; the node says it was withheld.
- `PRINCIPAL.md` and `policies/` are not edited. They are curated from the beliefs afterwards.
- The result lints with zero errors.

### No model needed

The default order is deterministic. `--generator MOD:ATTR` plugs in an `exo.onboard.Generator`
(class or instance) with three hooks, any of which a model can back:

| Hook | Job |
|---|---|
| `draft(bank, known)` | extra candidate questions |
| `imagine(question, known)` | plausible answers; their spread feeds the score |
| `follow_ups(question, answer, known)` | questions to add after an answer |

### Answers file

```yaml
answers:
  identity.name: Sam
  work.projects: The studio website and a client rebrand.
```

Keys are question ids (`exo init x --interview --plan` lists them). A missing answer is a skip
and costs nothing, so a file can answer more than `--max-questions` covers.

### Bank file schema

```yaml
section: work                  # required
node: work/projects            # node for this section's answers
title: ...                     # node title, type, summary, load_when, stability
type: fact
order: 20                      # section order (tie-break)
extra_nodes:                   # nodes that single questions can target
  - {id: systems/environment, title: ..., type: fact, summary: ..., load_when: ...}
questions:
  - id: work.projects          # stable, unique across the bank
    ask: What are the two or three projects that matter most to you right now?
    why: Agents rank work and attention by these.
    covers: [work.projects]    # facets this answer settles (default: the id)
    weight: 5                  # 1 to 5
    imagined: ["One big project", "Several small ones"]
    node: systems/environment  # optional: send this answer to another node
    followups:
      - id: work.projects-done
        ask: For the most important one, what does done look like this month?
        when: answered         # or: always; or when_matches: "<regex on the answer>"
```

The bank is generic. It names no person, account or machine (D-004).

## 3. Adopt an existing store

```sh
exo import-store ~/hafiz --id a --activate
exo import-store ~/hafiz --id a --include-private --root /path/to/fresh/dir
```

- Copies, never moves. The source is only read.
- Needs `INDEX.md` and `nodes/` in the source.
- Skips `.git`, caches, `_drafts`, `*.key` files and symlinks (listed, not followed). Skips
  `corpus/private` and `ledger.sqlite` unless `--include-private`.
- Fills in what the template has and the source lacks (`policies/`, `agents/`, ...) and merges
  `.gitignore`.
- Creates `corpus/private/` (0700) and the ledger, and appends one `ingest` event that records
  the source, counts and skips.
- Refuses a destination that exists and is not empty.
- Prints the lint summary. This is how A's `~/hafiz` becomes principal store #1 (migration M1).

## 4. Config

`exo.config` owns `$EXO_HOME/config.json`: `principal`, `hermes_dir`, `hermes_home`, `venv`,
`plugins`, `plugin_config.<plugin>`, `principals.<id>` (explicit store roots). Each value
resolves from the environment (`EXO_PRINCIPAL`, `EXO_HERMES_DIR`, `EXO_HERMES_HOME`,
`EXO_VENV`), then the file, then the default. Environment values are never written back.

```sh
exo config show
exo config set plugin_config.exo-memory '{"pack_budget": 1200}'
```

```python
from exo.config import Config
cfg = Config.load()
cfg.principal, cfg.hermes_home, cfg.plugin_config('exo-memory')
```

## 5. Wiring into `exo.cli` (for the lead)

Until this lands, `python -m exo.onboard init|interview|import-store|config ...` works on its
own (the installer uses it). The patch to `src/exo/cli.py`:

```diff
 from .consolidate import register as register_consolidate, cmd_consolidate, cmd_proposals  # noqa: F401
+from .onboard import (register as register_onboard, add_init_args, cmd_init as onboard_init,  # noqa: F401
+                      cmd_interview, cmd_import_store, cmd_config)
@@ build_parser
     p.add_argument('--from-template', nargs='?', const='', default='', metavar='PATH',
                    help='template directory (default: the bundled empty store)')
+    add_init_args(p)
@@
     register_consolidate(sp, common)
+    register_onboard(sp, common)
     return ap
@@ main
     if a.cmd == 'init':
-        return cmd_init(a)
+        return onboard_init(a)
+    if a.cmd == 'import-store':
+        return cmd_import_store(a)
+    if a.cmd == 'config':
+        return cmd_config(a)
```

`onboard_init` without `--interview` does what `cmd_init` did, and also sets the active principal
in `config.json` when none is set.

## 6. One `/exo` command: `exo.dispatcher`

Hermes gives each plugin its own slash commands. Exo plugins share one `/exo` instead.

```python
from exo.dispatcher import register_subcommand, register_root

register_subcommand('branch', handler, 'Write a handoff and start a fresh session on it.',
                    plugin='exo-context', args_hint='<topic>')
register_root(ctx)   # registers /exo with Hermes once per process; later calls are no-ops
```

- A handler gets the rest of the line as one string and returns text, None, or an awaitable.
- `/exo` and `/exo help` list subcommands with their plugin. `/exo help <sub>` and
  `/exo <sub> --help` show the full help. Unknown names get a "did you mean".
- A name owned by another plugin raises `ValueError` unless `replace=True`. The same plugin
  re-registering (a reload) replaces quietly. `unregister_plugin(name)` drops a plugin's names.
- A handler that raises returns an error line; it cannot break `/exo` for other plugins.

### Migration for exo-memory (the always-on plugin)

```python
def register(ctx) -> None:
    ctx.register_memory_provider(ExoMemoryProvider())
    from exo.dispatcher import register_root
    register_root(ctx)
```

### Migration for exo-context

Replace the direct `ctx.register_command('exo', handle, ...)` in `plugins/exo-context/__init__.py`:

```python
def register(ctx) -> None:
    ctx.register_context_engine(ExoContextEngine())
    from exo.dispatcher import register_subcommand, register_root
    from exo_context.commands import cmd_branch, cmd_handoff, cmd_resume  # same import style as today
    P = 'exo-context'
    register_subcommand('branch', lambda rest: cmd_branch(rest),
                        'Write a handoff and print the line that starts a fresh session on it.',
                        plugin=P, args_hint='<topic>')
    register_subcommand('handoff', lambda rest: cmd_handoff(rest),
                        'Write a handoff for this session.', plugin=P, args_hint='[topic]')
    register_subcommand('resume', lambda rest: cmd_resume(rest),
                        'Load the newest handoff for a topic (or a path) into this session.',
                        plugin=P, args_hint='<topic>')
    register_root(ctx)   # keeps /exo working when exo-memory is not enabled; no-op otherwise
```

`commands.handle` and `USAGE` can stay for direct use; the dispatcher now owns help. The smoke
test `ctx.commands["exo"]("help").startswith("Usage:")` still holds.
