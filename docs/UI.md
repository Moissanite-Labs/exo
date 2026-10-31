# Exo terminal UI

This is the look of `exo-tui` (D-031). The language is not new: it comes from the project site
(`site/index.html`, the "surface" section with `figure.term`, plus `site/assets/style.css`). The terminal
client was built to look like that figure, and the site stays the source of truth for the tokens. A test
(`tests/clients/tui/test_ui.py::test_tokens_match_the_site`) reads `:root` in `style.css` and fails if the
palette drifts.

## Principles

- Instruments, not chrome. Every line on screen reports something: the model that served, how full memory is,
  how much of the last call came from the cache, what it cost, how many findings are open.
- Orange is the signal. The prompt mark, the live dot, a fallback model, an approval gate, an open finding.
  Steel blue is system knowledge: memory events, taste, findings panels, peers, `/context`. Never a rainbow.
- Hairlines, not boxes. Panels are a tinted strip with a coloured left rule and a header line that runs to the
  edge and ends in a clipped-corner mark.
- Motion only reports state. A packet moves along a wire while data is in flight; a spinner turns while a tool
  runs; the live dot pulses while a turn runs; panels fade in when they arrive; the caret blinks. Nothing moves
  at rest.

## Tokens

| Role | Hex | Use |
|---|---|---|
| bg | `#08090a` | screen, brand bar, composer |
| bg-2 | `#0e1012` | panels, status strip |
| bg-3 | `#15181b` | inputs |
| deep | `#050606` | transcript (the `.term` background) |
| line / line-2 | `#24282d` / `#343a41` | hairlines, separators, unfilled gauge |
| fg / fg-2 / fg-3 | `#eceae4` / `#b4b3ad` / `#8b8b86` | text, agent prose and labels, system lines |
| accent / accent-2 | `#ff5b14` / `#ff8a4c` | signal, warm warning |
| cold | `#9fb4c4` | system knowledge |
| ok | `#4ade80` | a check that passed |
| err | `#e5534b` | the one colour not on the site, for errors |

`clients/tui/exo_tui/theme.py` holds them, a Textual `Theme` named `exo` (so `$accent`, `$cold`, `$line-2`,
`$fg-3` work in CSS) and the drawing helpers (`header_rule`, `chips`, `gauge`). Mono, uppercase labels, and
the middle dot as separator come straight from the site.

## What is on screen

1. Brand bar: `E X O`, agent, thread, and `LIVE` with a pulsing dot while a turn runs (`IDLE` otherwise). It shows
   the open thread's turn: after a switch it shows the new thread's state.
2. Transcript: `›` and your text for a prompt (`steer sending ›`, then `steer queued ›` or `steer delivered ›` when sent mid-turn); agent text in fg-2 as Markdown;
   one line per tool with state glyph, name, short args, line count and elapsed time (ctrl+o expands); system
   lines dim; tips as `tip · ...` in dim italic; `memory · log 1-16 folded · 1781 → 153 tok` as a cold line.
3. Panels: approval (orange rule, `APPROVAL · <risk>`, followed by `Ctrl+F, then [o] once [a, Enter] always [d, Enter] deny` before the request details; both `a` and `A` wait for Enter; the card never takes focus automatically, and Ctrl+F keeps the composer draft; it is removed when the core closes the request, when the turn is stopped, and by a check against `approvals.list` every few seconds), taste,
   finding, `/context` report and the short orientation `INTRO` shows when the `exo-intro` skill is missing
   (cold rule), peer message (double cold rule, sender and trust in the header,
   never styled as you). The side panel for `ui.panel` uses the same header and rule.
   The terminal waits for an approval card's removal before mounting the next queued card under the same widget id.
   Answers and close events name their request, so a late event cannot close its replacement. Thread switches and
   restoration share this transition. A request with no thread still stays across thread switches (#276, D-045).
4. Activity: a wire with a travelling packet and `waiting for <model> 12s`, `running tool`. A queued steer count leads this line (`1 steer queued (Up to edit)`), visible even with a draft in the composer.
   Beside it, a parent thread shows the delegated approval cue when an active task it handed out, or a task under
   that one, waits for approval (#996, D-045). Each line names the task's title on one line, its session and
   `/open <task>`. Past five, one more line names the other task ids, because `/tasks` lists only this thread's own
   tasks. The cue never takes focus and never answers. It sits above the activity line in the classic layout and
   below it in the inverse layout. The cue reads `approvals.list` every 3 s. It reads `tasks.list` only while
   another session has an open request, and at once when such a request arrives.
5. Composer: block caret in the signal colour, placeholder hint, `steer ›` while busy, `answer ›` for a question. A steer sent mid-turn shows `steer sending ›` while its reply is pending, `steer queued ›` after the core queues it, then `steer delivered ›` on its matching `message.completed` or a successful `unsteer` reply confirming it already left the queue. Delivery means the text reached the conversation, not that the model has acted on it. Delivery events received before the send reply are retained only for in-flight sends. A send reply alone never proves delivery. A lost reply says `steer delivery unknown`; a refused send says `steer not sent`. `Up` in an empty composer or `/unsteer` sends `command.run unsteer` and puts queued text back in the composer (D-045). Esc is not used for this.
   Under it, when you type `/`, `@` or `!`, the completion menu (D-037): a hairline above, mono rows, an orange rule on
   the selected row, a dim argument shape, up to eight rows then `N matches`. Ghost text is drawn dim after the caret.
   The placeholder doubles as the one-line guidance (`job j1 finished: ask for its job_output · tab fills it in`).
   No animation: the menu and the line change only on keys and events, so reduced motion needs no special case.
   Subagent commands (D-070): `/tasks` lists the tasks this session handed out, `/open <task>` shows the child's session (a read-only look: the child's loop is not resumed, and typing there steers the running child without a receipt note, as a queued steer that up pulls back), `/steer <task> <text>` sends a running child a note and `/stop <task>` stops it. A bare `/stop` still stops the running turn.
6. Status strip: `MODEL` (the route that served, in orange with `← requested` when it fell back), `MEM` gauge,
   `CACHE` share of the last call, `COST`, open findings.
7. Start: the site's hero motif in braille (`exo_tui/hero.py`): a shell of linked nodes around an orange core,
   turning once every 16 s. Frames are precomputed, played at 3 fps, 10 rows (7 below 38 terminal rows, hidden below
   30), one still frame under reduced motion or `NO_COLOR`, gone with the first message. Idle cost measured with
   `scripts/idle_cpu.py`: about 1.3% of one core (0.5% without it). A plain startup prints no `new thread` line; `/new` does.
   Before that, a boot sequence (under a second, any key skips it) that marks `core`, `agent`, `memory`, `model` as
   each real step finishes, then an empty-start card with the keys. The boot screen is gone before anything opens
   over the start (the first-run providers panel or model picker): its dismiss closes the top screen. The `memory`
   line and the card read the core's `memory_status` built-in: `ready`, `off, no principal store` (no store yet:
   a dim dash, not a green check, and a notice naming `exo init <id>`), `did not start` (also a dash), or `log only`
   (no memory plugin: the agent log and summary tree still run). An older core that cannot say leaves the plain label.

Where the numbers come from, all from events the core already sends: `model.call` (`requested`, `route`,
`usage.cache_read_tokens`), `usage.updated` (context tokens, session cost), `memory.summarized`,
`peer.message`, `taste.updated`, `tool.completed` (`duration_ms`), and the findings count the client polls.
Context tokens are the last call's whole prompt, input plus cache read and cache write. The cache share is cache
read over that prompt. Input is the uncached part on every provider (D-080), so the sum counts each token once.

Switching threads (`/new`, `/sessions`, `/resume`, `/fork`, `/open`): the old thread's events stop reaching the
window, so it drops that thread's turn state (`LIVE`, the activity line, steer mode, a queued steer, an open
question), its approval card and its numbers. A card with no thread stays (a held peer message that no live session
could take, D-026): it belongs to every thread, so the window asks `approvals.list` with no session filter and keeps
those cards. A new thread, and a window started on one, show them too. `MEM` and `CACHE` are empty until the new thread's next model call.
`COST` is kept per thread. The core's total for a thread counts all of it, so a thread the window showed before gets
back what the window saw of it, and the `+` for an unpriced call stays on its next total (D-032). A thread the
window never showed reads `–` until its next model call, and its `+` is a lower bound, as in a fresh window. So is
the `+` for an unpriced call made there while another thread was open. The thread that opens gets its state from the
core. `session.resume` and `session.get` report `status`: `live` while a turn runs, so the bar says `LIVE` and
ctrl+c stops it. `approvals.list` gives back the card of a request still waiting there (D-045). The same check runs
when the window starts with `--session` or `--resume`, and after the core refuses an answer. A finished subagent's
roll-up is a `usage.updated` with no `model.call` before it. It adds to `COST`, adds the `+` when the child's calls
had no price, and leaves `MEM`, `CACHE` and `MODEL` alone.

The event feed starts once `hello` answers, whatever the rest of the boot does. When a later step fails, the window
says `cannot start: ...` and still streams, so `/new` and a message work and their replies and approval cards show.
A `--session ID` the core does not know (a typo, a deleted thread) opens a new thread with the note
`no thread ID; started a new one`. One event the window cannot read does not end the feed. A value it does not
know, such as a newer core's panel kind, is kept, and the panel shows its text fallback. An event that still fails
to decode is skipped and logged under `exo.protocol.client`. If the feed itself dies, the window says
`event feed stopped: ...` and tells the harness (D-025). `/restart` reopens the thread.

## The /providers panel

`/providers` opens a modal in the same language: a cold `PROVIDERS` header rule, then one row per provider with a
status chip (green `verified` with latency, grey `key found`, dim `no key` or `no base_url`, orange `failed: <class>`, cold `running`
for a local server), where the key comes from (env, keychain, secret-service or file, never a value), the model count
and an orange default mark. Key chips: `[a]` add or replace a key, `[t]` test, `[r]` remove (asks y or n), `[d]` pick
the default model, `[esc]` close (leaving a panel is soft; Esc never stops a turn, ctrl+c or `/stop` does). `[a]` opens a small modal with one masked field; the key never goes through the
composer, the transcript or any log, and a key typed after `/providers add <name>` in the composer is refused and
not echoed. After a key change, or a test that passes, the client tells a running core to read keys again. That
also clears the provider's cooldown and route breaker, so the next message reaches it. At start, if no provider is
ready, one note says what the default needs: a key, a running local server (its host and port only, never a login
in its URL), or a CLI that is installed and logged in, and the panel opens. If providers are ready but no main
route uses one, the panel opens too, or the default-model picker when only one provider is ready.

## Focus, the big composer and the inverse layout (D-046)

- **Focus follows you.** A click on the transcript or any plain surface returns focus to the composer, and a printable key
  that arrives with focus on the transcript or nowhere is typed into it. The approval card, a panel, the completion menu
  and input fields keep their own keys.
- **Big composer.** `ctrl+g` or `/compose` grows the composer to most of the screen (`docs/media/tui-10-composer.png`):
  a head line with word and line count and the keys, soft wrap, light markdown colour (headings, list marks, quotes,
  fenced and inline code, bold), Enter for a new line, **`ctrl+s` to send** (`ctrl+enter` also, where the terminal reports
  it), Esc to close with the draft kept (the menu and ghost text close first; Esc never stops a turn), `ctrl+l` line numbers,
  `ctrl+t` or `/compose edit` for `$VISUAL`/`$EDITOR`, `/compose task` for the four headings. A long paste stays whole.
  Slash, argument and `@` completion work as in the one-line composer; up and down move the caret and do not recall history.
- **Inverse layout** (`docs/media/tui-11-inverse.png`): the status strip under the brand bar, then the input, the menu,
  approval card and activity line, then the delegated approval cue when a delegated task waits, then the transcript with the newest item first. Text inside one message still reads top to bottom. A thread opens on
  its newest item at the top. `/layout classic` restores the usual order: the transcript, the delegated approval cue,
  the activity line and the input, with the status strip at the bottom
  (`docs/media/tui-12-classic-bottom.png`: a long thread opens at the bottom). `/layout` moves the strip live. The choice is saved in `$EXO_HOME/state/ui.json`; `EXO_LAYOUT=classic` overrides for one run.
  Inverse is the default only for the experiment: set `DEFAULT_LAYOUT = "classic"` in `exo_tui/prefs.py` to flip it back.

## Switches

- `EXO_REDUCED_MOTION=1`: no boot sequence, no spinner, packet, pulse, fade or caret blink. The state is still
  shown, as still glyphs.
- `NO_COLOR`: the `exo-plain` theme paints nothing (the terminal's own colours); orange becomes bold, dim stays
  dim, so the shape still reads.
- 256-colour terminals: Textual and Rich downgrade the hex values; the palette was chosen so orange, blue and
  grey stay distinct. The app paints its own background, so light terminals are not blinded.

## Screenshots

The public set is in `docs/media/` (`tui-01-start.png` to `tui-12-classic-bottom.png`): start, a turn with tools, an
approval panel, the `/context` report, a background job, then the completion shots (`/co` menu with ghost text,
`/model ` arguments, `@s` files, the guidance line). They come from `scripts/promo_shots.py`, which
drives the app with Textual's pilot over an in-process core and a scripted model, with neutral demo data.
`docs/media/README.md` has the commands. The older shots in `docs/ui/` were removed because they used internal
names. `scripts/ui_shots.py` still renders the full set (boot, narrow 80x24) for local checks.
The `/providers` panel shots (`docs/ui/07-providers.png`, `docs/ui/07b-providers-key.png`, demo data, no
real status or keys) come from `scripts/ui_shots.py`.

## Credit

The design language, colours, type choices and the terminal figure belong to the Exo site. This client follows
them; it adds no borrowed branding. The completion menu and ghost text follow an idea from the Hermes CLI; the
code and the look are ours.
