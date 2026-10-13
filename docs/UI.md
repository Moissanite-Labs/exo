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

1. Brand bar: the wordmark `3 X 0` (the X in orange, read from `site/brand/tui-wordmark.html`), agent, thread, and `LIVE` with a pulsing dot while a turn runs (`IDLE` otherwise).
2. Transcript: `›` and your text for a prompt (`steer ›` when sent mid-turn); agent text in fg-2 as Markdown;
   one line per tool with state glyph, name, short args, line count and elapsed time (ctrl+o expands); system
   lines dim; tips as `tip · ...` in dim italic; `memory · log 1-16 folded · 1781 → 153 tok` as a cold line.
3. Panels: approval (orange rule, `APPROVAL · <risk>`, key chips `[o] once [a] always [d] deny`; both `a` and `A` wait for Enter; the card never takes focus automatically and says "tab, click or ctrl+f to answer"; it is removed when the core closes the request, when the turn is stopped, and by a check against `approvals.list` every few seconds), taste,
   finding and `/context` report (cold rule), peer message (double cold rule, sender and trust in the header,
   never styled as you). The side panel for `ui.panel` uses the same header and rule; a choice is a card in the chat
   column instead (see "The choice card").
4. Activity: a wire with a travelling packet and `waiting for <model> 12s`, `running tool`.
5. Composer: block caret in the signal colour, placeholder hint, `steer ›` while busy, `answer ›` for a question. A steer sent mid-turn shows `steer queued ›` until it lands (its `message.completed`); `Up` in an empty composer or `/unsteer` sends `command.run unsteer` and puts the text back in the composer (D-045). Esc is not used for this.
   Under it, when you type `/`, `@` or `!`, the completion menu (D-037): a hairline above, mono rows, an orange rule on
   the selected row, a dim argument shape, up to eight rows then `N matches`. Ghost text is drawn dim after the caret.
   The placeholder doubles as the one-line guidance (`job j1 finished: ask for its job_output · tab fills it in`).
   No animation: the menu and the line change only on keys and events, so reduced motion needs no special case.
   Subagent commands (D-070): `/tasks` lists the tasks this session handed out, `/open <task>` shows the child's session (read-only look; the child's loop is not resumed), `/steer <task> <text>` sends a running child a note and `/stop <task>` stops it. A bare `/stop` still stops the running turn.
6. Status strip: `MODEL` (the route that served, in orange with `← requested` when it fell back), `MEM` gauge,
   `CACHE` share of the last call, `COST`, open findings.
7. Start: the exosphere in its x0 form, large (`exo_tui/hero.py`, drawn by `exo_tui/exosphere.py`): the lattice
   egg of `site/brand/egg.js` at stage 0, the yolk's six incentre dots (an octahedron) with the seed in orange inside the
   egg's ring of lattice dots, turning a quarter turn every 16 s (seamless, by the figure's symmetry). Frames are
   precomputed, played at 3 fps, 10 rows (7 below 38 terminal rows, hidden below 30), one still frame under reduced
   motion or `NO_COLOR`, gone with the first message, when the small exosphere takes the corner. A plain startup
   prints no `new thread` line; `/new` does.
   Before that, a boot sequence (under a second, any key skips it): the mark and the wordmark, read from
   `site/brand/tui-mark.html` and `tui-wordmark.html` (`exo_tui/brand.py`), then `core`, `agent`, `memory`, `model`
   marked as each real step finishes, then an empty-start card with the keys.
8. The exosphere: a small x0 in the bottom-right corner of the chat column for the whole session. See below.

Where the numbers come from, all from events the core already sends: `model.call` (`requested`, `route`,
`usage.cache_read_tokens`), `usage.updated` (context tokens, session cost), `memory.summarized`,
`peer.message`, `taste.updated`, `tool.completed` (`duration_ms`), and the findings count the client polls.

## The /providers panel

`/providers` opens a modal in the same language: a cold `PROVIDERS` header rule, then one row per provider with a
status chip (green `verified` with latency, grey `key found`, dim `no key`, orange `failed: <class>`, cold `running`
for a local server), where the key comes from (env, keychain, secret-service or file, never a value), the model count
and an orange default mark. Key chips: `[a]` add or replace a key, `[t]` test, `[r]` remove (asks y or n), `[d]` pick
the default model, `[esc]` close (leaving a panel is soft; Esc never stops a turn, ctrl+c or `/stop` does). `[a]` opens a small modal with one masked field; the key never goes through the
composer, the transcript or any log, and a key typed after `/providers add <name>` in the composer is refused and
not echoed. After a change the client tells a running core to read keys again. At start, if no provider is ready,
one note says to type `/providers`.

## Focus, the big composer and the inverse layout (D-046)

- **Focus follows you.** A click on the transcript or any plain surface returns focus to the composer, and a printable key
  that arrives with focus on the transcript or nowhere is typed into it. The approval card, a panel, the completion menu
  and input fields keep their own keys. The choice card keeps its arrows, digits, Enter and Esc; any other printable key
  goes to the composer (D-066).
- **Big composer.** `ctrl+g` or `/compose` grows the composer to most of the screen (`docs/media/tui-10-composer.png`):
  a head line with word and line count and the keys, soft wrap, light markdown colour (headings, list marks, quotes,
  fenced and inline code, bold), Enter for a new line, **`ctrl+s` to send** (`ctrl+enter` also, where the terminal reports
  it), Esc to close with the draft kept (the menu and ghost text close first; Esc never stops a turn), `ctrl+l` line numbers,
  `ctrl+t` or `/compose edit` for `$VISUAL`/`$EDITOR`, `/compose task` for the four headings. A long paste stays whole.
  Slash, argument and `@` completion work as in the one-line composer; up and down move the caret and do not recall history.
- **Inverse layout** (`docs/media/tui-11-inverse.png`): the status strip under the brand bar, then the input, the menu,
  approval card and activity line, then the transcript with the newest item first. Text inside one message still reads top to bottom. A thread opens on
  its newest item at the top. `/layout classic` restores the usual order with the status strip at the bottom
  (`docs/media/tui-12-classic-bottom.png`: a long thread opens at the bottom). `/layout` moves the strip live. The choice is saved in `$EXO_HOME/state/ui.json`; `EXO_LAYOUT=classic` overrides for one run.
  Inverse is the default only for the experiment: set `DEFAULT_LAYOUT = "classic"` in `exo_tui/prefs.py` to flip it back.

## The choice card (D-066)

A decision the agent waits on: a `ui.panel` of kind `choice`, or a `question.asked` with options. It is a card in the
chat column, in the same place as the approval card (above the activity line, under the input in inverse), not in
the side panel: that is where the eye already is, the transcript keeps its width, and the exosphere corner and the
D-046 layouts are unchanged (the card gets the same right padding as the approval card when the figure sits beside
it). Shots: `~/Work/handoffs/exo-ecosystem/choice-shots/` (classic, inverse, behind a draft, a question), rendered
by `scripts/exosphere_shots.py choice-01-classic ...`.

- **Look.** Orange header rule `CHOICE · <title>` (`QUESTION` for a question), the prompt as plain text, a blank row,
  then one row per option: its number, the label, and a dim description when `data.options[i].description` (or
  `detail`, `hint`, `desc`) is set. The highlighted row has an orange `▌` marker, the number in orange, the label in
  bold and a faint row tint; no inverse block. The left rule is grey until the card has focus, then orange. Footer
  chips `[↑↓] move  [1-N] pick  [enter] choose  [esc] dismiss`; a question adds "or type your own answer".
- **Focus.** The card takes focus when it arrives, unless you are writing: text in the composer, a key in the last
  1.5 s, the big editor, a modal, or focus on another card or field. Then the marker is grey and the footer says
  "ctrl+f, tab or click to choose; your typing is safe". `ctrl+f` goes to the pending decision: the approval card
  first, then the choice; a second press moves to the other one.
- **Keys.** Up and down move (Home and End jump), Enter chooses, `1` to `9` pick directly, a click on a row picks it,
  Esc dismisses without choosing and never stops a turn (D-043). Any other printable key goes to the composer: typing
  never picks by accident, and a free-text answer is one keystroke away.
- **What is sent, once.** A panel choice sends `ui.respond` `acted` `{choice: id}` (the learning signal) and your
  pick's label as your reply (`turn.send`; a steer if a turn is running), so the agent that asked hears it. A question
  sends `question.answer`. The card closes and focus returns to the composer. Typing an answer and pressing Enter
  answers a question the same way and closes its card. Esc on a panel choice sends `dismissed`; on a question it only
  closes the card, and the question stays open for a typed answer.
- **More than one.** The newest pending choice shows; an older one comes back when the newer one is answered. An agent
  `update` redraws the card in place; `close` removes it (`ignored` if you never touched it).
- **Reduced motion and `NO_COLOR`.** The card fades in like other panels, and not at all under
  `EXO_REDUCED_MOTION`. Under `NO_COLOR` the marker, the bold label and the chips' brackets carry the highlight.

## The exosphere (D-062)

The exosphere is the name of Exo's evolving lattice figure. Its forms are **x0** (the egg), then **x**, then
**3xo**, with more to come (`exosphere.FORMS` is an open list). The community figure on the site is x0 too. The
terminal draws x0 at two sizes: large on the start screen, and small (14 x 6 braille cells, 28 x 24 dots) in the
bottom-right corner of the chat column, with a one-line label under it.

**Label.** The form, and the level when one exists: `x0 · lv 3`. No XP or level source exists yet (the exopet XP
design is on branch `design/exopet`, not built), so today the label is `x0` alone. `Exosphere(level=...)` takes the
number when a source lands.

**Where it sits, and why text never runs under it.** Hermes Agent's CLI (prompt_toolkit) draws its Petdex pet as a
right-aligned window of fixed height above the prompt: a reserved band, not a wrap. TamaHermes and EvoPet draw their
pets in separate always-on-top windows (AppKit, Metal) that float over the terminal. None of them flows text around
the pet. Textual has no float, so a true wrap around a box inside a scrolling transcript is not clean either. Exo
reserves a corner region, with the input flowing beside it:

- The figure is its own widget on a layer above the chat column, pinned to the column's bottom-right (it moves when
  the terminal or a side panel changes the column's size).
- Classic layout: the activity line, the composer, the completion menu and the approval card get a right padding
  the width of the box, so their text wraps short of it. A gap of three rows under the transcript makes up the rest
  of the box's height. The composer's text flows beside the figure; the transcript ends above it.
- Inverse layout: the input is at the top, so the box stands in the right end of a gap of its own height under the
  transcript.
- Tests (`tests/clients/tui/test_exosphere.py`) check, in both layouts, with a long answer, a long draft and an
  approval card, that no text widget's content area overlaps the box and that every cell of the box shows the figure.

**States.** Mapped from events the client already gets:

| State | When | Animation |
|---|---|---|
| idle | nothing running | a slow breath (the shell swells 3.5% twice a loop) and a quarter turn every 12 s; 2 repaints a second |
| thinking | `turn.started` until the turn ends, no tool running | inner pulse: the yolk and the lines to its centre glow orange every 3 s |
| tool running | `tool.started` until `tool.completed` | packets: orange dots travel out of the core along the six axes to the shell |
| waiting on approval | an approval card is open, or the agent asked a question | steady glow, no motion, no timer; an orange `!` beside the label |
| error | an `error` event, or a turn that ends with `stop_reason` "error" | dim flicker: the figure goes dim, a few ring dots drop out, a different few each beat; 3 s |
| done | `turn.completed` | brief bloom: the yolk swells and brightens once, 1.6 s |

Waiting on the user outranks a passing beat; new work cuts a passing beat short. An error inside a running turn is
shown when the turn ends, so the turn's own state is not cut short.

**Cost.** Each state is a fixed loop of 48 frames (one only for approval, 6 for the bloom), cached after first use,
so a frame is one text swap of a 16 x 7 box with no layout pass, at 4 fps (2 when idle). The timer stops when the
figure is hidden or still. Measured headless with `scripts/exosphere_cpu.py` (30 s, M-series Mac): idle 0.80% of
one core with it, 0.55% without (+0.25%); a turn in flight 3.24% with it, 2.43% without (+0.8%). This counts the
app's own work, not the terminal's drawing.

**Rules.** On by default; `/exosphere off` and `/exosphere on` save the choice per user in
`$EXO_HOME/state/ui.json` (`EXO_EXOSPHERE=on|off` overrides it for one run). It hides on terminals under 90 columns,
under 20 rows, when a side panel leaves the chat column under 64 columns, in the big editor (ctrl+g), and while the
large start figure is on screen. It never takes focus and has no keys: a click on it is a click on a plain surface
(focus goes to the composer), ctrl+f still reaches an approval card, ctrl+g still opens the editor, Esc still never
stops a turn (D-043, D-045), and the completion menu (D-037) works beside it. `EXO_REDUCED_MOTION` and `NO_COLOR`
give one still frame per state (bold and dim only under `NO_COLOR`).

## Switches

- `EXO_REDUCED_MOTION=1`: no boot sequence, no spinner, packet, pulse, fade or caret blink. The state is still
  shown, as still glyphs.
- `NO_COLOR`: the `exo-plain` theme paints nothing (the terminal's own colours); orange becomes bold, dim stays
  dim, so the shape still reads.
- `EXO_EXOSPHERE=on|off`: shows or hides the corner exosphere for one run, over the saved `/exosphere` choice.
- 256-colour terminals: Textual and Rich downgrade the hex values; the palette was chosen so orange, blue and
  grey stay distinct. The app paints its own background, so light terminals are not blinded.

## Screenshots

The public set is in `docs/media/` (`tui-01-start.png` to `tui-12-classic-bottom.png`): start, a turn with tools, an
approval panel, the `/context` report, a background job, then the completion shots (`/co` menu with ghost text,
`/model ` arguments, `@s` files, the guidance line). They come from `scripts/promo_shots.py`, which
drives the app with Textual's pilot over an in-process core and a scripted model, with neutral demo data.
`docs/media/README.md` has the commands. The older shots in `docs/ui/` were removed because they used internal
names. `scripts/ui_shots.py` still renders the full set (boot, narrow 80x24) for local checks.
The exosphere shots (boot, start, a turn in both layouts, approval in both, the bloom, the menu, a narrow terminal)
come from `scripts/exosphere_shots.py <out-dir>`.
The `/providers` panel shots (`docs/ui/07-providers.png`, `docs/ui/07b-providers-key.png`, demo data, no
real status or keys) come from `scripts/ui_shots.py`.

## Credit

The design language, colours, type choices and the terminal figure belong to the Exo site. This client follows
them; it adds no borrowed branding. The completion menu and ghost text follow an idea from the Hermes CLI; the
code and the look are ours.
