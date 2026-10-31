# Web client look (D-031)

The web client (`plugins/exo-command/exo_command/static/`) is a shared workspace for a person and an agent.
Conversation text uses a readable sans serif face; code and measurements use monospace. Angular forms,
quiet surface levels and the orange accent retain the project's identity. Strong color identifies the
current action, attention requests and errors. Text names each state as well.

The application bar keeps navigation, connection state and Appearance available across views. Dark is the
default; Light and System are explicit choices. Only that choice is saved in browser storage, alongside
the existing conversation layout preference. No font, asset or dependency loads from another site.

Navigation links support browser Back and Forward. The keyboard skip link moves directly to the main
content. Changing views preserves the navigation nodes and each conversation's page-memory draft.
“Connected” describes the event connection, while the conversation reports its own work state.

## Native display preferences

An installed desktop host may inject `window.__exoDesktopPrefs` before scripts run. The shared web client
validates its display choices and gives them priority over browser storage left behind on a reused loopback
port. Browser-only sessions retain their current storage behavior. The host owns persistence and its seat/core
scope; this bridge does not open a file or infer a seat from conversation data.

Only `view`, `thread`, `layout`, `appearance`, `petMode` and a transcript `scroll` fraction cross
`window.webkit.messageHandlers.exoDesktop.postMessage`. Thread IDs accept letters, digits, underscores and
hyphens up to 200 characters; scroll is a finite fraction from zero to one. Null clears thread and scroll.
No token, prompt, draft, transcript or model response enters this channel. An explicit URL view or Tasks
Open request takes priority over the restored view/thread. The conversation list is bounded, so restoration fetches the saved thread directly and keeps its returned
metadata visible in the chooser. Only an exact session 404 clears the saved pointer and leaves the chooser
open. A failed list or other failed transcript read preserves the saved thread and fraction; a successful
reconnect can finish the initial restore. Restoration uses the ordinary conversation-open event buffer, so
live messages and tool state arriving during the snapshot read are reconciled afterward. A reconnect
waits for an active open to finish; queued recovery belongs to that selection and is discarded on navigation
or unmount. Thread changes clear the old scroll fraction. Snapshot and navigation checks prevent a late
saved-thread response from changing a newer selection, and unmount cancels restoration.

A host with no saved thread opens the latest conversation, as a browser does. That covers a first launch and a
launch after a 404 cleared the pointer. Only the load whose exact read returned 404 leaves the chooser open.

When the stream catches up, the redraw keeps the reader's place in a native page as in a browser ("Reading
position" under "What is where"). The host then stores that place. While the reader scrolls, the page sends the
scroll fraction at most once every 250 ms, because each message is a write to the host's preferences. It sends the
latest fraction at once when the page is hidden or closed (`visibilitychange`, `pagehide`), the conversation
changes or the view unmounts.

`petMode` comes from the Exopet display control, a `select.xc-pet-mode` that Exopet (#826) adds to the page. The
page listens for its change event from the start, so a choice made while start-up is loading is saved too. Until
#826 lands, Exo has no such control and sends no `petMode`.

## Conversation drafts and unconfirmed sends (D-045)

Each conversation keeps its own unsent text and selection while the page is open. Switching threads or
visiting another web view restores that conversation's draft. New thread starts with its own composer;
returning to the source conversation restores its draft. The initial composer belongs to the conversation
created by its first submission. Drafts live only in JavaScript memory, never browser storage or the core.
Reloading or closing the page discards them.

While a submission is pending, Send says “Sending…” and repeated Enter does not submit it twice. You can
keep drafting or visit another conversation. If the reply fails, the original text returns to an unchanged
empty composer. If you wrote something newer, “Restore text” adds the failed text ahead of that newer
draft when you choose it. Recovery stays with the originating conversation, including through view changes.
Check the transcript before sending again: an unconfirmed reply may have reached the core. Nothing retries
automatically. “Dismiss” removes the recovery notice; it does not erase the current draft.

## Connecting to the core (D-036)

`exo web` needs one core for the agent. It looks for `$EXO_HOME/run/exod.sock` (`exo serve`), then
`run/agent-<seat>.sock` (a terminal window running the core in process, D-028), and joins whichever answers. If
neither does it starts `exo serve` detached (log `$EXO_HOME/logs/exod.log`, pid `run/exod.pid`), under a start lock so
two clients never start two (and a core that starts anyway, from a terminal, loses to the one holding the socket's lock `run/exod.sock.lock`). A short helper (`python -P`, so the current folder is not on the import path) starts a
new session, forks the core and exits, so the core is a child of init (or the nearest subreaper), not of `exo web`
(unless `exo web` is PID 1 or a subreaper; it then reaps a core that exits at its next start); it passes the core's pid
back on the last line of its output (the core writes `run/exod.pid` itself, once it holds the socket's lock). If the core exits while starting, the page says so at once, with its exit code
when this process reaped it. While it starts, the page shows "Exo core is not running, starting it...". If it cannot
start, the page shows the reason and the log path. If the agent is held by a window that is not sharing it, the page
says to close that window or run `exo stop`. The background core stays up after the page closes (`exo stop` ends it).
With `--socket` or `--url` nothing is started; the page says the core is not reachable.
When the core ends the event stream (a restart, or a stream 4,096 events behind, D-079), `exo web` reconnects and
replays from the last event it applied, so no reply or approval card is lost. That holds up to 16,384 missed
events. After a longer gap, such as a long outage while the core kept working, it replays the newest 5,000 as on
start, so an approval asked before them does not show.

## Opening the page and the page token (D-039)

`exo web` makes a random page token for each launch and keeps it in memory only. It prints the page address with
the token in the URL fragment, `http://127.0.0.1:7342/#token=...`. A fragment is never sent to a server or written
to a server log. The page moves the token to `sessionStorage` (this tab only) and takes it out of the address bar.
A new launch makes a new token, so open the latest complete link after a restart; the bare URL shows instructions
rather than a usable client. Tokens are not persisted across launches. Opening a new token fragment in the same tab
reconnects it with that token; `--no-open` prints the link without opening it.

A second `exo web` on a port that is already in use exits with a message, not a raw bind error. It loads the page
shell `/` (no token needed) to tell the two cases apart. If Exo's web client answers, it says so and how to get back
in: an open page keeps working, the full link the first launch printed still works, Ctrl+C in that terminal stops it,
or `--port` starts a second one on the same core. It cannot show the first link, because the token lives in that
process only. A token in a file under `$EXO_HOME/run` was rejected: the agent's own file tools can read that folder
(#169), and a page with the token answers approvals. If another program holds the port, the message says so and
suggests `--port`.

When run in a terminal, `exo web` also opens the browser, but not on the token link: the URL handed to the browser
launcher sits in its argument list, which other users of the host can read in `/proc` (#166). The browser gets
`http://127.0.0.1:7342/#code=...` instead: a one-time launch code, made fresh for each browser launch, held in
memory, good for one use and for at most 120 seconds. The page takes the code out of the address bar first (so a
reload cannot send it twice), then posts it to `/api/launch`, which answers the token once and burns the code. That
route is the only `/api/` route without the token; it makes every other check of the provider routes (loopback
peer, Host, Origin, Sec-Fetch-Site), compares in constant time, and answers `Cache-Control: no-store`. A refused
code (used, expired, unknown or malformed) gets 403 and no token, and the page says: this browser link was already
used or has expired; paste the link `exo web` printed in the terminal into this tab. Any other refusal (Host,
Origin, peer) shows the server's reason, with the same advice. Paste, not click: a click in the terminal hands the
`#token=` link to the URL opener, whose argv is the leak #166 closes. The launch route answers loopback only, so
unless `--host` is `127.0.0.1`, `localhost`, `::1` or a wildcard bind (`0.0.0.0`, `::`, shown as `127.0.0.1`),
`exo web` opens no browser and says so; open the printed link instead.

A used code that comes back means two parties held it. `exo web` then replaces the page token: every open page
must re-open, and every other connection ends at once, both the event streams opened with the old token and the
requests that passed the token check but still wait for their body. The old token gets 403 everywhere, and a
request body is checked against the current token again once it is read. The terminal prints a warning and the new
`#token=` link; paste it into the browser that is already open (a click in the terminal hands it to the URL opener,
whose argv is the leak #166 closes). The leaked code causes this once; an expired or unknown code is refused without
a new token. A browser can keep the launch URL in its own argument list for its whole life, so the warning can also
come from a replay long after the launch; the replay gets no token either way. The printed link and `--no-open` are
unchanged.

Detection needs a second redemption. The first caller gets the token without a sound; only the launched page, coming
second, shows the race. A browser that never runs the page's script never redeems: with no display (an SSH
session), Python's `webbrowser` can fall back to a text browser such as `lynx` or `w3m`. A party that took the code
from `/proc` then keeps a working token, and nothing warns.

Every `/api/` route except `POST /api/launch` needs the token: the page sends it as `X-Exo-Token` on every fetch
(`api()` in `dom.js`) and as `?token=` on the two event streams (`stream()` in `dom.js`; a browser EventSource cannot
send headers). The Host header must be `127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>`, so a DNS rebinding
name gets nothing, not even the page. An Origin, when sent, must be that same host, and Sec-Fetch-Site must be
same-origin or none. Besides the launch route, only the page shell (`/`, `/static/`) loads without the token.
Responses carry `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff` and the Content-Security-Policy
below.

`POST /api/approvals/<id>` takes a JSON object with `decision` (`allow`, `deny` or `allow_always`) and an optional
`note`. An array, null or scalar gets 400 with `{"error": "body must be a json object"}` before any approval answer
reaches the core; malformed JSON keeps its 400 `{"error": "bad json"}` response (#740).
JSON deeper than the decoder supports gets that same approval error. The shared body reader used by chat and
provider POSTs rejects such input with its existing 400 `{"error": "body must be a json object"}` response
(follow-up to #751).

The web process answers approvals as the principal: on each connection it checks with the kernel that the socket
is served by the core named in its launch record (`run/<socket>.principal.key`), then sends that record's
credential on that connection (D-060, `docs/GATE.md` "Unix peer authentication"). It re-joins a core that restarted
with a new record, and refuses a socket that some other process serves. A refusal shows on the page with its reason
(for a core from before D-060: "launch record is not a v1 record") and a restart hint: run `exo stop` (or close the
Exo window that runs the core), then reopen the page. The optional
ASGI app (`asgi.py`) is gone: it re-exposed the routes with no checks.

The stream resumes through the core’s durable event log when its cursor has fallen outside the web event ring.
After replay the page refreshes durable messages, session activity, open panels and pending approvals. Composer
drafts and unfinished streamed output survive that refresh. A pending approval older than the replayed window is added from the core's open list (a card with no
asked-time), and shown once. `/context` uses typed token counts (`command.run`,
`args="json"`), while the default core command still returns the human-readable report. `/new private` creates a
private session; malformed known commands show usage. Literal paths such as `/etc/hosts is broken` remain messages.

## Content-Security-Policy (#162)

The page builds every node with `h()` and text nodes, never `innerHTML`, so model text cannot become markup. That
alone was the XSS defence, and Markdown panels (and completed replies) run model text through `markdown.js`. So
every response the web server writes, the page, static files, JSON, both event streams and every error, carries one
policy, `CSP` in `server.py`:

```
default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self';
object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'
```

with `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer` (`SECURITY_HEADERS`). Script and styles
load only from this server (in practice `/static/`); requests (fetch and the two EventSource streams) go only to
this server; there are no WebSockets, fonts or `data:` images; no other site may frame the page; forms never post
natively (each is handled in script). Inline scripts, inline event handlers, `javascript:` links, `eval` and string
timers, `<style>` blocks and `style` attributes are all refused, so markup that a renderer slip ever let into the
page would not run or restyle it. The policy has no `'unsafe-inline'`, `'unsafe-eval'`, hash or nonce, and must not
get one: a page that needs an inline script or style moves it into a file under `static/`. The few computed sizes
(gauges, bars, the task board's column count) are set through the CSSOM: `h()` turns a `style` key into
`el.style.cssText`, which the policy allows. `plugins/exo-command/tests/test_csp_web.py` pins the header on every
kind of response, loads the page in headless Chrome with no violation, and shows that injected markup runs without
the policy and does not run with it.

## Screens

Current workspace samples are `docs/ui/workspace-dark.png`, `workspace-light.png` and
`workspace-mobile.png`. They contain synthetic conversations. `scripts/shots_workspace.py OUTDIR`
renders both themes at 320, 390, 768, 1024 and 1440 pixels and records viewport geometry. It uses a
temporary browser profile and a stand-in server, without a real core or credentials.

The public set is in `docs/media/`: `web-01-start.png` (empty start: the site's hero behind the brand block,
instrument column on the right), `web-02-turn.png` (a turn with tools running), `web-03-approval.png` (an
approval panel) and `web-04-memory.png` (the memory column filled in). `scripts/promo_shots_web.py` makes them.
The Providers tab (D-034, demo data) is in `docs/ui/`: `web-providers.png` and `web-providers-narrow.png`.

## What is where

- **Brand bar.** The Exo mark, selected conversation, navigation links, Appearance and connection state.
  The square activity indicator turns orange while a turn runs and is hollow when idle.
- **Status strip** (wrapping instruments, hairline above): state, served model (and `← fallback` in orange when the route
  that served differs from the one asked, from `model.call`), memory gauge (tokens the last call carried, bar
  against the reported model capacity; an unknown capacity stays unknown, from `usage.updated` and structured
  `/context` reports), cache share of that call, session cost. While busy a
  packet travels along a dashed wire at the end of the strip. Findings are the open count from `/findings count`.
  Instruments wrap within the available conversation width; model names and values remain readable. Notices
  occupy a full row and wrap, including long unbroken text, in both classic and inverse layouts.
- **Transcript.** `›` prompt in orange, agent text in fg-2, one dim line per tool (name, short args, elapsed; click
  to open the arguments and output; a small wire runs while it works), `memory · log 1-16 folded · 1781 → 153 tok`
  and `context · compacted` lines in dim with a cold label, `tip:` lines in dim italic (a `notice` that starts with
  `tip`). A reply streams as plain text; once complete it is rendered as Markdown: headings, lists (ordered lists
  keep their source numbers, across blank lines and the fences or sub-items that belong to an item; a table, quote
  or fence indented under an item renders inside it), fenced code, `>` quotes, and GFM pipe tables that scroll
  sideways inside the message. Line breaks inside a paragraph are kept, as the streamed text showed them. Everything
  is built as DOM nodes, so model text never becomes markup: only `http(s)` links become anchors, images and raw
  HTML stay text. A reply builds at most 10,000 table cells, and a table has at most 64 columns and no more cells
  padded onto short rows than its source has characters; past that, a table is shown as its source text, so many
  replies in one thread cost what their bytes cost. Quotes nest at most 8 deep and lists 9, and a deeper `>` stays
  text. In the classic layout a reply that grows when it renders (a table is taller than its pipe text) keeps the
  view at the bottom if it was there.
  A context note (a stored user message with `meta.note`: what other threads did, D-028) is for the model, so the
  transcript leaves it out on load and live, as the terminal client does. Its size shows on the `threads` row of the
  memory column.
- **Panels.** A thin outline with a 3px left rule. Approval is orange (`APPROVAL · HIGH RISK`) with key chips
  `[o] once [a] always [d] deny`; Tab, click or Ctrl+F deliberately focuses a card, and cards never claim focus
  automatically. Each card shows the answer sequence for its available options and says "Ctrl+F to focus the first
  approval". The help changes during always confirmation and deny-note entry. A deliberately focused card has an
  accent outline. Shortcuts act only within that card, never in editable fields. Always (including uppercase A,
  a focused button shortcut, or clicking Always) requires a separate Enter or Confirm always button; Escape,
  another key, Cancel, or leaving the card cancels confirmation. Deny opens an optional note. The page reconciles
  its cards with the server's list on every thread open and stream reconnect, and the server asks the core first,
  so a card the core no longer holds is dropped (D-045). A card with no thread (a held peer message that no live
  session could take) shows in every thread, as in the terminal client. Taste, diff and the other panels are cold.
  A dismissed panel stays closed. The core sends no `ui.panel` close for a dismissal, so the page drops the panel
  from its own list as well, and the next panel event, a tab switch or a reconnect does not bring it back. A peer
  message has the sender in the header, a grey rule and the line "from another session, not from you": it never
  looks like the user. That holds after a reload or a thread switch too. A stored peer, bus or job message
  (`meta.peer`, `meta.bus`, `meta.job`) is the same card with its real author (`Bus · <seat>`, `Job · <id>`), and a
  live `peer.message` and its stored message make one card.
- **Composer.** A framed writing area with a separate action row. The desktop prompt says `steer ›` instead of
  `›` while a turn runs. A message sent while a turn runs shows as a `steer queued` chip above the
  box with an `×` (Up in an empty box does the same): it pulls the text back into the box if it has not landed
  (`POST /api/chat/sessions/<id>/unsteer`, D-045). The chip goes when the message lands or another window cancels it,
  not when the turn ends: a steer that arrives while a turn is finishing starts the next turn (#147). Reopening the
  thread drops a chip the core no longer queues (`GET /api/chat/sessions/<id>` lists the queued ids as `steers`).
- **Conversation list.** Find a conversation filters loaded titles, IDs and agent names. The count names its
  loaded scope; this is not full-history or message search. The status selector shows all conversations,
  working conversations or those with an approval waiting. Search and filtering do not switch the active
  conversation or touch its draft. Clear or Show all conversations resets the search.
  The list comes from `GET /api/chat/sessions`, which takes a `limit` from 1 to 500 (100 when none is given).
  A larger limit is cut to 500. A limit below 1, or one that is not a number, gets a 400 and sends nothing to
  the core: SQLite reads a negative `LIMIT` as no limit, so `limit=-1` used to return every session (exo-desktop #55).
  Narrow windows start with a compact Conversations disclosure showing the current title. Open it to
  search or select a thread. Choosing a conversation closes it and returns to the writing area. Resizing
  does not hide a focused control inside the disclosure. This choice is not saved as a pane preference.
  Each thread has a Rename button. It opens a labeled title field with Save and Cancel.
  Enter saves and Escape cancels. Double-click also opens this editor (or type `/rename <title>`): `POST /api/chat/sessions/<id>/rename`,
  the core `rename` built-in, principal only (D-069). Renaming another thread does not select it or change the
  current composer draft. A refused save keeps the title draft and shows the error.
  The page asks for the thread list again after each change, and two such requests can answer in either
  order. Only an answer to a later request replaces the list on screen, so a list asked for before a new
  thread existed cannot hide that thread once a newer list shows it (#859).
- **Reading position.** Show latest messages appears when the reader moves away from the newest messages.
  It returns to the newest item in either saved layout. Incoming text preserves the reader's position;
  readers already at the newest item continue following it. So does the redraw each time the event stream catches
  up, on load and after a reconnect, when the page reads the conversation again. Before, that redraw moved the
  reader back to the newest item. The transcript is keyboard focusable.
- **Event stream.** `bus.js` reopens the stream itself from the last seq when the browser gives up (EventSource never
  retries after an HTTP error such as a 403 after a restart or a 502 while the core restarts), ignores a seq it has
  already handled, and the server resumes from the later of `?since=` and `Last-Event-ID`. Before D-045 a drop replayed
  the whole page history, so streamed text doubled. A stale page token shows "page link expired" in the corner.
  A chat reader that falls 1000 events behind has its stream ended with nothing lost: the events still queued for it
  are dropped unwritten, and the reconnect replays them from the ring or the core's log. Before, the oldest queued
  event was thrown away and never replayed. `exo web` folds each core event into its state once, by `seq`, so its
  re-bootstrap after the core's stream drops counts no message, tool call, task update or approval twice. A core
  whose log is behind the page (a new `EXO_HOME`) starts the page over: the old picture goes, and every open stream
  reconnects and gets the reset.
- **Startup.** The workspace opens directly. Connection status and authentication errors remain visible;
  there is no timed boot overlay or duplicate startup request for a visual check sequence.

## Empty thread and instrument column

- An empty thread asks “What are we working on?” with a short prompt and relevant keyboard hints.
  Its presentation is static. Opening an empty conversation starts no canvas animation,
  observers or model calls. The prompt disappears when the first transcript item arrives.
- With no panel open, the right column is a quiet instrument column: memory (tokens on the next call split into
  system, tree, other threads and tail, tree nodes, log and fold frontier) and the last calls (served route,
  ttft, cache share, fallback). It is hidden under 1100 px. The memory numbers come from `/context`, read
  after a thread opens, after each turn and after a memory event. The last calls come from `model.call`
  events, so they start empty when a thread is opened.
- Typing `/context`, `/findings` or `/trace` in the composer runs that report and shows it in the transcript.
  The plugin exposes this as `POST /api/chat/sessions/<id>/command` (names limited to those three, sent as the
  existing `command.run`): a plugin route, not a protocol change.
- `/model <ref>` sets the shared main route through `POST /api/chat/sessions/<id>/model` (`session.get`, then the
  core's principal-only `models.set` for that thread). `/default <ref>` and `/model default <ref>` save the default
  through `POST /api/providers/default`, which answers a loopback page only (D-034): on a page opened at another
  address the note says so, and `/model` still works. Without a reference either one opens a picker under the box,
  filled from `GET /api/chat/models` (`models.list`); a later `/model` or `/default` replaces a list still loading,
  Esc or Cancel closes it, and choosing returns focus to the box. While a change is in flight the box sends nothing.
  A failed change keeps the line in the box. A `bad_model` or `bad_request` core error is a 400, not a 502, on every
  route of `api.py`'s table (chat, tasks, schedule, feedback), so a bad model reference is the caller's mistake (#146).
  `GET /api/chat/models` answers any page-token holder, a `--host` LAN page included, while `GET /api/providers/models`
  is loopback-only (D-034); it lists routes, model names, which routes are configured and the spend status, never
  a key.

## The composer (D-037)

Typing `/` opens a dropdown under the composer with the commands the page runs (`/context`, `/model`, `/default`,
`/findings`, `/trace`, `/new`, `/rename`, `/layout`), each with its argument shape and a line of description. Same ranking, Tab, Enter, arrows and Esc (close the menu only) as the
terminal client. Esc never stops a turn on this page either; the Stop button does. Approval hotkeys are ignored while a field has focus. After a command it offers the argument words (`/trace ` offers 5, 10, 25). `@` lists files from the
open thread's working folder through `GET /api/complete/files?session=&q=`: read-only, names only, inside the folder,
secret stores left out with `gate.is_sensitive_path`, and guarded like the key routes (loopback peer, Host, page
token). Ghost text from this page's history (right arrow takes it), up and down walk it; nothing is stored in the
browser. A mistyped `/command` is not sent: the box shows the fix. A command the page does not run stays in the box
with a note, as does a near typo of one: a name of the core's `commands.list` (`/grants`), or one of the terminal
client's own commands (`/help`, `/quit`; a core plugin's name such as `/orchestra` counts only when `commands.list` has
it, as in the TUI). `GET /api/complete/commands` lists them as `core` and `client`; `core` is null while the core
cannot answer. Until the core's names have loaded every `/name` line that is not the page's or the terminal client's
waits in the box, and the page asks again with no limit: a timer, 1 s doubling to every 30 s, and each send, which does
not move the backoff on. So a long core restart never turns a core command into a model turn, and the names are used
as soon as the core answers. An older core that answers `unknown_command` or `unsupported` for `commands.list` also
counts as an answer (`core` is `[]`); a core-only name the page then cannot know goes as a message.
A `/word` close to no command by the TUI's rule (`/tmp is full`, `/data is corrupted`, `/Users is big`) is sent as a message. One dim guidance line above the box (a failed call,
a finished job, open findings, a slow turn, a long thread). A failed call reads its own error and the errors of its
attempts, since a failure before any output ends as "all routes for 'main' failed or are cooling down" and its cause
is only in the attempts (#855). A refusal (`HTTP 401` or `HTTP 403`, an invalid or missing key, a permission denial,
the router's "rejected the key", "refused the login" or "no credentials for role") may point at the Providers page.
A transport failure (`ReadError`, `ReadTimeout`, `ConnectError` and the like) says the model could not be reached,
offers no key advice, and Tab fills in `/trace`. An error that reads as neither, a spend limit included, points at
`/trace` (D-092). Each of the three keeps its own count of two a session. The line shows one new tip per 25 seconds
at most, and it is off when the core
config key `guidance.enabled` is `false` (each `GET /api/complete/commands` answer says so, or says it does not know
yet, and the page asks again until it does, so a core down at page load still turns the line off once it is back; an
unset key, which the core answers with `not_found`, is on, and the asking ends once both answers are in),
no timer under reduced motion. An answer redraws an open menu only when the core's names arrive, so retries do not
move its selected row.
The logic is `static/complete.js` (pure, tested in node against the Python engine); the drawing is `static/composer.js`.
Screenshots: `docs/media/web-05-guidance.png`, `web-06-slash.png`, `web-07-files.png`.

## Layout: inverse or classic (D-046)

The chat has two layouts. **Inverse** (the experiment's default) puts the status strip and then the message box at the top,
with the approval cards under it, then the thread with the newest item first; classic keeps the strip under the thread; text inside one message reads
top to bottom, and a thread opens on its newest item at the top (`docs/media/web-08-inverse.png`). **Classic** is the usual
order, and a long thread now opens at its newest message at the bottom (`docs/media/web-09-classic-bottom.png`). Switch with
the "Layout" button under the thread list or `/layout inverse|classic`; the choice is saved in this browser
(`localStorage` key `xc-layout`; Appearance uses the separate `xc-appearance` key). The code is `static/layout.js` (pure, tested in node)
and `chat.js`. To end the experiment change `DEFAULT_LAYOUT` in `layout.js` to `"classic"`; saved choices stay.
Fix: the chat box had no fixed height (`flex: 1` in an auto column), so a long thread grew the page and opened at its top
with the box off screen. It has a definite height now and the log scrolls inside it. Tests drive headless Chrome over
DevTools (`tests/browser.py`, skipped when Chrome is missing). The driver turns off Chrome's throttling of background
timers and frames, and the suite's summary line names the Chrome binary and version it drove, so a CI log shows which
build a layout failure came from (#907). When a wrapped status and composer exceed the available
height, the chat column scrolls and retains space for the independently scrolling log. The page has no big composer
pane; that is terminal only. On narrow screens the log keeps its responsive height, and the slash-command menu keeps
its bounded height when the chat column scrolls.

## Cost and jobs (D-032)

Cost in the strip is the session total from `usage.updated`; each row of last calls shows that call's
`usage.cost_usd` from `model.call`. A call or session the core could not price reads `unknown`, never `$0`.
A parent's session total includes its finished subagents, which the core rolls up, so the Command page's cost
panel lists such a child as `in parent` and does not add it again.
`job.updated` shows as one dim line with a cold label: `job · pytest -q · done · 12.4s`, with the exit code
when it is not zero.

## Providers (D-034)

The Providers tab (`providers.js`) shows where keys are stored, one row per provider with a status chip
(verified with latency, key found, no key, no base_url, failed with the error class, local running, cli
installed), the source of the key (never a value), the model count and a default mark. A provider that needs a
key has a password field with Save, Test and Remove. After Save the field is cleared at once, also on error;
the key is read once to build the request body and is never logged, shown again, put in a URL or kept in
browser storage. A select lists the configured routes, then the available ones, with Save default. The routes
need the page token: `exo web` prints a link with `#token=...`; the page moves it to `sessionStorage` (this
tab only), removes it from the address bar and sends it as `X-Exo-Token` on every call (D-039; the provider
routes also need a loopback peer). Without a token the tab only asks you to open that link. When no provider
is ready, the Chat view shows one quiet line with a link to this tab.

When the model list cannot be fetched the tab says why: a core that never answered, one that answered and
refused this page, or one whose own call failed. A core on TCP that refuses this web process's bearer token
(`exo web --url URL --token-file FILE` with a stale token) is a refusal too, and the sentence names the token
file. A core's own failure is shown in its own words, with no exception class name (#856). The reply is still 200 with both lists present, plus
`error`, `error_class` and `retryable` (D-091). The sentence is redacted and capped at 300 characters, and
the "Try again" button appears only for the transport class, because a refusal would answer the same way.
A failed retry keeps the last good list, so the default picker stays usable.

A Test that passes also tells the running core to reload that provider (the reply carries `core_reloaded`). A
provider that was cooling down after a failure then takes the next message at once.

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
  Remove has no undo, so it waits for a separate Confirm remove click on that card (exo-desktop #55). The step is
  drawn in the page, not with `window.confirm`: a desktop web view with no confirm handler answers that with Cancel.
  Cancel or any other job action drops it, and the second click of a double click does not confirm. Pause, resume
  and run now act at once. Focus moves to Cancel when the step opens, so a second Enter cancels. Cancel, or Escape
  while focus is in the step, closes it and puts focus back on remove. While the remove is in flight the step stays,
  with both buttons disabled: Cancel cannot stop it, and the card's other buttons do not come back under the pointer
  for the second click of a double click. Each job's remove keeps its own step, so a remove on a second job leaves
  the first step as it is. A failed remove shows the error and puts focus on that card's remove, unless focus is on
  another control by then.
- **Command** is an instrument panel: six gauges (the selected one has an orange top rule), section tabs, then one
  hairline section per source. Approvals, decisions and sessions use pills; the Tasks section uses the Tasks board.
  An approval card shows the whole call (the tool, then every argument) and answers by click. The card grows with
  the call and the page scrolls to its end: the page redraws on every core event, and a scroll box of its own would
  jump back to its top each time.
  Allow always is a standing grant, so as on the Chat card it waits for a separate Confirm always click. Cancel,
  another section, Refresh or the request closing elsewhere drops the confirm. Allow and deny answer at once.
  Focus moves to Cancel when the confirm opens. Cancel, or Escape while focus is in the confirm, puts it back on
  allow always. A redraw on a core event keeps focus on the same approval button.
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

Motion respects `prefers-reduced-motion: reduce`. Focus remains visible in both themes. Appearance can
follow the operating system or keep an explicit light or dark choice. The mobile composer puts its writing
area above its actions so a narrow window does not squeeze the text between buttons.

## Fix found on the way

`redraw()` in `chat.js` replaced a node with itself (the item's `el` already pointed at the new node), so a
streaming message never lost its caret and a tool line never left "running" until the next full redraw. It now
keeps the old node first.

## Reproducing the screenshots

The layout shots (`web-08`, `web-09`) come from `python scripts/promo_shots_web.py layout`, which drives Chrome over DevTools
with `--use-mock-keychain --password-store=basic` so no keychain prompt appears.

`python scripts/promo_shots_web.py` serves the static client over a stand-in source that replays protocol
events for a scripted turn, and captures each state with headless Chrome. Nothing is captured from a live
`~/.exo`. See `docs/media/README.md`. What it adds to its copy of the page (the classic layout pin, the typed
composer text) is a script file loaded by `src`, because the page's policy refuses inline scripts.
The Providers shots use a stub `/api/providers` table with demo names (no real status, no keys), captured with
`Google Chrome --headless=old --screenshot` against a `WebServer` over a fake source at `/?boot=0#providers&token=demo`.

Source of the language: the Exo site, `site/`.

### Chat notice ownership

The chat status strip shows notices for the selected session only. Unscoped notices do not replace its
status. Model retry and fallback notices carry `source: model_route` and a `call_id` matching the
`model.call` record. A successful matching call clears that transient warning; another call cannot.
Turn completion clears any remaining route warning, while newer general notices and command errors
remain visible. Attempt details remain in the model-call records. Older cores without these fields retain
notices until another status message or thread selection replaces them.
