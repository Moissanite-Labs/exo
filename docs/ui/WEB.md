# Web client look (D-031)

The web client (`plugins/exo-command/exo_command/static/`) now speaks the language of the project site
(`site/assets/style.css`, the `figure.term` section): same tokens, hairlines instead of boxes, mono uppercase
labels, `·` as the separator, orange only for the one thing that matters, cold blue for what the system knows.
Presentation only: no protocol change, the JS behaviour is the same, no build step, no new dependency, no font
or file from outside.

## Screens

The public set is in `docs/media/`: `web-01-start.png` (empty start: the site's hero behind the brand block,
instrument column on the right), `web-02-turn.png` (a turn with tools running), `web-03-approval.png` (an
approval panel) and `web-04-memory.png` (the memory column filled in). `scripts/promo_shots_web.py` makes them.
The Providers tab (D-034, demo data) is in `docs/ui/`: `web-providers.png` and `web-providers-narrow.png`.

## What is where

- **Brand bar.** `E X O`, the agent and thread, the tabs, the connection led. The square dot before `E X O`
  pulses orange while a turn runs and is hollow when idle.
- **Status strip** (one line, hairline above): state, served model (and `← fallback` in orange when the route
  that served differs from the one asked, from `model.call`), memory gauge (tokens the last call carried, bar
  against a nominal 200k window, from `usage.updated`), cache share of that call, session cost. While busy a
  packet travels along a dashed wire at the end of the strip. Findings are the open count from `/findings count`.
- **Transcript.** `›` prompt in orange, agent text in fg-2, one dim line per tool (name, short args, elapsed;
  click to open the arguments and output; a small wire runs while it works), `memory · log 1-16 folded ·
  1781 → 153 tok` and `context · compacted` lines in dim with a cold label, `tip:` lines in dim italic
  (a `notice` that starts with `tip`).
- **Panels.** A thin outline with a 3px left rule. Approval is orange (`APPROVAL · HIGH RISK`) with key chips
  `[y] allow [n] deny [a] allow always`; the keys work when the composer is not focused. Taste, diff and the
  other panels are cold. A peer message has the sender in the header, a grey rule and the line "from another
  session, not from you": it never looks like the user. Panels and new transcript items reveal on arrival.
- **Composer.** Block caret in orange (`caret-shape: block`, where the browser supports it), `steer ›` instead of
  `›` while a turn runs, a cut-corner Send.
- **Boot.** Four lines go green as real calls answer: core, agent, memory (message count on record), model
  route. A line the server cannot answer stays dim as `unavailable`. Skipped under reduced motion, with
  `?boot=0`, and after the first time in a tab.

## Empty thread and instrument column

- An empty thread shows the site's hero: the masked hairline grid and the node shell around an orange core
  (`hero.js`, ported from `site/assets/main.js`), behind a brand block (`E X O`, agent, model, memory line)
  and key hints as chips. It fades out when the first item arrives. It draws about 24 frames a second with the
  pixel ratio capped at 1.5, stops when the tab is hidden or the canvas is off screen, and draws one still
  frame under reduced motion. Measured in headless Chrome on an idle empty thread: 1.7 percent main-thread
  time with motion, 0.0 percent with reduced motion.
- With no panel open, the right column is a quiet instrument column: memory (tokens on the next call split into
  system, tree, other threads and tail, tree nodes, log and fold frontier) and the last calls (served route,
  ttft, cache share, fallback). It is hidden under 1100 px. The memory numbers come from `/context`, read
  after a thread opens, after each turn and after a memory event. The last calls come from `model.call`
  events, so they start empty when a thread is opened.
- Typing `/context`, `/findings` or `/trace` in the composer runs that report and shows it in the transcript.
  The plugin exposes this as `POST /api/chat/sessions/<id>/command` (names limited to those three, sent as the
  existing `command.run`): a plugin route, not a protocol change.

## Cost and jobs (D-032)

Cost in the strip is the session total from `usage.updated`; each row of last calls shows that call's
`usage.cost_usd` from `model.call`. A call or session the core could not price reads `unknown`, never `$0`.
`job.updated` shows as one dim line with a cold label: `job · pytest -q · done · 12.4s`, with the exit code
when it is not zero.

## Providers (D-034)

The Providers tab (`providers.js`) shows where keys are stored, one row per provider with a status chip
(verified with latency, key found, no key, failed with the error class, local running, cli installed), the
source of the key (never a value), the model count and a default mark. A provider that needs a key has a
password field with Save, Test and Remove. After Save the field is cleared at once, also on error; the key is
read once to build the request body and is never logged, shown again, put in a URL or kept in browser storage.
A select lists the configured routes, then the available ones, with Save default. The routes need the page
token: `exo web` prints a link with `#token=...`; the page moves it to `sessionStorage` (this tab only),
removes it from the address bar and sends it as `X-Exo-Token` on `/api/providers` calls only. Without a token
the tab only asks you to open that link. When no provider is ready, the Chat view shows one quiet line with a
link to this tab.

## Motion and accessibility

Every animation (pulse, packet, caret blink, reveal, boot) sits behind `prefers-reduced-motion: reduce`, which
turns them all off and hides the boot. The page is dark only, like the site, and paints its own background.

## Fix found on the way

`redraw()` in `chat.js` replaced a node with itself (the item's `el` already pointed at the new node), so a
streaming message never lost its caret and a tool line never left "running" until the next full redraw. It now
keeps the old node first.

## Reproducing the screenshots

`python scripts/promo_shots_web.py` serves the static client over a stand-in source that replays protocol
events for a scripted turn, and captures each state with headless Chrome. Nothing is captured from a live
`~/.exo`. See `docs/media/README.md`.
The Providers shots use a stub `/api/providers` table with demo names (no real status, no keys), captured with
`Google Chrome --headless=old --screenshot` against a `WebServer` over a fake source at `/?boot=0#providers&token=demo`.

Source of the language: the Exo site, `site/`.
