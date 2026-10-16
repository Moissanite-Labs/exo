# Web client look (D-031)

The web client (`plugins/exo-command/exo_command/static/`) now speaks the language of the project site
(`site/assets/style.css`, the `figure.term` section): same tokens, hairlines instead of boxes, mono uppercase
labels, `·` as the separator, orange only for the one thing that matters, cold blue for what the system knows.
Presentation only: no protocol change, the JS behaviour is the same, no build step, no new dependency, no font
or file from outside.

## Connecting to the core (D-036)

`exo web` needs one core for the agent. It looks for `$EXO_HOME/run/exod.sock` (`exo serve`), then
`run/agent-<seat>.sock` (a terminal window running the core in process, D-028), and joins whichever answers. If
neither does it starts `exo serve` detached (log `$EXO_HOME/logs/exod.log`, pid `run/exod.pid`), under a start lock so
two clients never start two. A short helper (`python -P`, so the current folder is not on the import path) starts a
new session, forks the core and exits, so the core is a child of init (or the nearest subreaper), not of `exo web`; it passes the core's pid
back on the last line of its output. If the core exits while starting, the page says so at once, with its exit code
when this process reaped it. While it starts, the page shows "Exo core is not running, starting it...". If it cannot
start, the page shows the reason and the log path. If the agent is held by a window that is not sharing it, the page
says to close that window or run `exo stop`. The background core stays up after the page closes (`exo stop` ends it).
With `--socket` or `--url` nothing is started; the page says the core is not reachable.

## Opening the page and the page token (D-039)

`exo web` makes a random page token for each launch and keeps it in memory only. It prints the page address with
the token in the URL fragment, `http://127.0.0.1:7342/#token=...`, and, when run in a terminal, opens that address
in the browser. A fragment is never sent to a server or written to a server log. The page moves the token to
`sessionStorage` (this tab only) and takes it out of the address bar. A new launch makes a new token, so open the
latest complete link after a restart; the bare URL shows instructions rather than a usable client. Tokens are
not persisted across launches. Opening a new token fragment in the same tab reconnects it with that token; `--no-open` prints the link without opening it.

Every `/api/` route needs the token: the page sends it as `X-Exo-Token` on every fetch (`api()` in `dom.js`) and as
`?token=` on the two event streams (`stream()` in `dom.js`; a browser EventSource cannot send headers). The Host
header must be `127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>`, so a DNS rebinding name gets nothing, not
even the page. An Origin, when sent, must be that same host, and Sec-Fetch-Site must be same-origin or none. Only
the page shell (`/`, `/static/`) loads without the token. Responses carry `Referrer-Policy: no-referrer`.

The web process answers approvals as the principal: it reads the core's principal credential
(`run/<socket>.principal.key`) on each request, so it re-joins a core that restarted with a new one. The optional
ASGI app (`asgi.py`) is gone: it re-exposed the routes with no checks.

The stream resumes through the core’s durable event log when its cursor has fallen outside the web event ring.
After replay the page refreshes durable messages, session activity, open panels and pending approvals. Composer
drafts and unfinished streamed output survive that refresh. `/context` uses typed token counts (`command.run`,
`args="json"`), while the default core command still returns the human-readable report. `/new private` creates a
private session; malformed known commands show usage. Literal paths such as `/etc/hosts is broken` remain messages.

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
  against the reported model capacity; an unknown capacity stays unknown, from `usage.updated` and structured
  `/context` reports), cache share of that call, session cost. While busy a
  packet travels along a dashed wire at the end of the strip. Findings are the open count from `/findings count`.
- **Transcript.** `›` prompt in orange, agent text in fg-2, one dim line per tool (name, short args, elapsed;
  click to open the arguments and output; a small wire runs while it works), `memory · log 1-16 folded ·
  1781 → 153 tok` and `context · compacted` lines in dim with a cold label, `tip:` lines in dim italic
  (a `notice` that starts with `tip`).
- **Panels.** A thin outline with a 3px left rule. Approval is orange (`APPROVAL · HIGH RISK`) with key chips
  `[o] once [a] always [d] deny`; Tab, click or Ctrl+F deliberately focuses a card, and cards never claim focus
  automatically. Shortcuts act only within that card, never in editable fields. Always (including uppercase A,
  a focused button shortcut, or clicking Always) requires a separate Enter or Confirm always button; Escape,
  another key, Cancel, or leaving the card cancels confirmation. Deny opens an optional note. The page reconciles
  its cards with the server's list on every thread open and stream reconnect, and the server asks the core first,
  so a card the core no longer holds is dropped (D-045). Taste, diff and the
  other panels are cold. A peer message has the sender in the header, a grey rule and the line "from another
  session, not from you": it never looks like the user. Panels and new transcript items reveal on arrival.
- **Composer.** Block caret in orange (`caret-shape: block`, where the browser supports it), `steer ›` instead of
  `›` while a turn runs, a cut-corner Send. A message sent while a turn runs shows as a `steer queued` chip above the
  box with an `×` (Up in an empty box does the same): it pulls the text back into the box if it has not landed
  (`POST /api/chat/sessions/<id>/unsteer`, D-045). The chip goes when the message lands or another window cancels it.
- **Thread list.** Double-click a thread to rename it (or type `/rename <title>`): `POST /api/chat/sessions/<id>/rename`,
  the core `rename` built-in, principal only (D-069).
- **Event stream.** `bus.js` reopens the stream itself from the last seq when the browser gives up (EventSource never
  retries after an HTTP error such as a 403 after a restart or a 502 while the core restarts), ignores a seq it has
  already handled, and the server resumes from the later of `?since=` and `Last-Event-ID`. Before D-045 a drop replayed
  the whole page history, so streamed text doubled. A stale page token shows "page link expired" in the corner.
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

## The composer (D-037)

Typing `/` opens a dropdown under the composer with the commands the page runs (`/context`, `/findings`, `/trace`,
`/new`), each with its argument shape and a line of description. Same ranking, Tab, Enter, arrows and Esc (close the menu only) as the
terminal client. Esc never stops a turn on this page either; the Stop button does. Approval hotkeys are ignored while a field has focus. After a command it offers the argument words (`/trace ` offers 5, 10, 25). `@` lists files from the
open thread's working folder through `GET /api/complete/files?session=&q=`: read-only, names only, inside the folder,
secret stores left out with `gate.is_sensitive_path`, and guarded like the key routes (loopback peer, Host, page
token). Ghost text from this page's history (right arrow takes it), up and down walk it; nothing is stored in the
browser. A mistyped `/command` is not sent: the box shows the fix. One dim guidance line above the box (a failed call,
a finished job, open findings, a slow turn, a long thread), one new line per 25 seconds at most, off when the core
config key `guidance.enabled` is `false` (`GET /api/complete/commands` says so), no timer under reduced motion.
The logic is `static/complete.js` (pure, tested in node against the Python engine); the drawing is `static/composer.js`.
Screenshots: `docs/media/web-05-guidance.png`, `web-06-slash.png`, `web-07-files.png`.

## Layout: inverse or classic (D-046)

The chat has two layouts. **Inverse** (the experiment's default) puts the status strip and then the message box at the top,
with the approval cards under it, then the thread with the newest item first; classic keeps the strip under the thread; text inside one message reads
top to bottom, and a thread opens on its newest item at the top (`docs/media/web-08-inverse.png`). **Classic** is the usual
order, and a long thread now opens at its newest message at the bottom (`docs/media/web-09-classic-bottom.png`). Switch with
the "Layout" button under the thread list or `/layout inverse|classic`; the choice is saved in this browser
(`localStorage` key `xc-layout`, the only use of it, pinned by a test). The code is `static/layout.js` (pure, tested in node)
and `chat.js`. To end the experiment change `DEFAULT_LAYOUT` in `layout.js` to `"classic"`; saved choices stay.
Fix: the chat box had no fixed height (`flex: 1` in an auto column), so a long thread grew the page and opened at its top
with the box off screen. It has a definite height now and the log scrolls inside it. Tests drive headless Chrome over
DevTools (`tests/browser.py`, skipped when Chrome is missing). The page has no big composer pane; that is terminal only.

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
removes it from the address bar and sends it as `X-Exo-Token` on every call (D-039; the provider routes also
need a loopback peer). Without a token the tab only asks you to open that link. When no provider is ready, the Chat view shows one quiet line with a
link to this tab.

## Sub-pages (D-061)

Tasks, Schedule, Command and Providers follow the site v2 (`site/assets/style.css`, `site/roadmap/roadmap.css`).

- **Head.** Each page opens with a mono eyebrow and an orange rule (`02 / Tasks`), an Inter display title, one line of
  purpose, and a readout on the right (Tasks: open, failed, done; Schedule: jobs, active, next run; Command: core and
  seq with Refresh; Providers: ready count and default). Shared helpers in `dom.js`: `pageHead`, `readout`, `pill`,
  `emptyState`, `stamp`, `until`.
- **Status in form.** A pill (`.xc-st[data-s]`) or led (`.xc-led[data-s]`) carries the state in its shape and the word
  names it; colour only confirms. run: dashed orange, blinking led. queue: hollow. done: filled cold. fail: crossed.
  stop: dashed. on: filled orange. ok: filled green. known: tinted cold. pause: two bars. warn: half filled. alert:
  filled with a ring. Numbers are tabular.
- **Tasks** reads like the roadmap chart: a tally bar with a key, then one hairline lane per status with cards whose
  top rule repeats the state. Under 760px the lanes stack. The empty state shows the lattice egg at stage 0
  (`static/egg.js`, a byte-for-byte copy of `site/brand/egg.js`, checked by a test); this is the only place it appears.
- **Task controls (D-070).** A card whose task has a child session gets Open, which shows that session in Chat. A running
  card also gets a note box with Steer, and Stop. They call `GET /api/tasks/<task>` and `POST /api/tasks/<task>/steer`
  or `/stop`, which run the core's `subagent` built-in. The core allows the principal only and its refusal (403, or 409
  for a finished child) shows on the card. A half-typed note survives the next event.
- **Schedule** shows each job as a card in a hairline grid: the clock line in mono, the title, the prompt, then next,
  last and tier as a readout (local date and time plus "in 2h"), and the actions. A paused job's top rule is dashed.
  The form sits beside the list, with labelled fields; under 1100px it follows the list.
- **Command** is an instrument panel: six gauges (the selected one has an orange top rule), section tabs, then one
  hairline section per source. Approvals, decisions and sessions use pills; the Tasks section uses the Tasks board.
  At 1280px and up approvals and decisions sit left of live sessions; tasks, projects and cost run full width.
  Empty sections say what will appear and how.
- **Providers** is a rack of ruled rows: name and default mark, the status pill, then key source, env var, model count
  and roles as labelled values, with the key field and buttons under them. The default model has its own panel.
- **Focus and motion.** Every control keeps the orange focus ring (inset on tabs and gauges). The page head reveals
  once; the running led blinks. All of it stops under `prefers-reduced-motion`.
- **Fix found on the way.** `replaceChildren(err ? box : null, ...)` drew the text `null` above Tasks, Schedule and
  Command; the lists are filtered first now.

Screenshots: `python scripts/shots_web_tabs.py OUTDIR` serves the client over demo data (no core, no Exo home, provider
table and snapshot stubbed, no key read) and shoots every tab with data and empty, at 1440 and 400px, full page.

## Motion and accessibility

Every animation (pulse, packet, caret blink, reveal, boot) sits behind `prefers-reduced-motion: reduce`, which
turns them all off and hides the boot. The page is dark only, like the site, and paints its own background.

## Fix found on the way

`redraw()` in `chat.js` replaced a node with itself (the item's `el` already pointed at the new node), so a
streaming message never lost its caret and a tool line never left "running" until the next full redraw. It now
keeps the old node first.

## Reproducing the screenshots

The layout shots (`web-08`, `web-09`) come from `python scripts/promo_shots_web.py layout`, which drives Chrome over DevTools
with `--use-mock-keychain --password-store=basic` so no keychain prompt appears.

`python scripts/promo_shots_web.py` serves the static client over a stand-in source that replays protocol
events for a scripted turn, and captures each state with headless Chrome. Nothing is captured from a live
`~/.exo`. See `docs/media/README.md`.
The Providers shots use a stub `/api/providers` table with demo names (no real status, no keys), captured with
`Google Chrome --headless=old --screenshot` against a `WebServer` over a fake source at `/?boot=0#providers&token=demo`.

Source of the language: the Exo site, `site/`.
