# The live egg: the site egg reacts to repo activity (D-059)

Status: **Proposed** (2026-10-13). Nothing here is built. D-059 in `DECISIONS.md`. The XP rules the feed applies are
in `docs/EXOPET.md` section 3 (D-058). The egg itself is `site/brand/egg.js` (`docs/BRAND.md`, D-049).

The idea: the egg on the project site is the community egg. Every time something happens in the repository, the egg
shows it. An issue is a small pulse. A pull request is a stronger pulse with a packet that travels the lattice. A
merge is a bloom. A release is a growth flash. The same events fill the community egg toward its hatch.

## 1. The interface (stable)

This section is the contract between the feed function and the site page. Other sections explain it; they do not
change it. A change here needs a new `v` number in the feed and a note in D-059.

### 1.1 The feed

`GET /api/egg/feed` on the site's own host (production and staging each serve their own). Query parameters:

| Parameter | Meaning |
|---|---|
| `since` | optional. The `cursor` from the previous response. Only newer events are returned. Absent or unknown: the replay window (below). |

Response, `200`, `content-type: application/json`, `cache-control: public, s-maxage=10, stale-while-revalidate=30`:

```json
{
  "v": 1,
  "generated": "2026-10-13T12:00:04Z",
  "cursor": "k7Qm2x",
  "poll_s": 20,
  "egg": {
    "xp": 612,
    "pending": 38,
    "target": 2048,
    "stage": 0.239,
    "state": "filling",
    "crack_at": null,
    "hatch_at": null
  },
  "events": [
    { "id": "e9c1a04f", "t": "2026-10-13T11:41:10Z", "type": "issue",   "level": 1, "weight": 1 },
    { "id": "4b20d7e3", "t": "2026-10-13T11:52:37Z", "type": "pr",      "level": 2, "weight": 3 },
    { "id": "a77f13c0", "t": "2026-10-13T11:59:58Z", "type": "merge",   "level": 3, "weight": 10 }
  ]
}
```

Fields:

| Field | Type | Meaning |
|---|---|---|
| `v` | integer | format version. This document defines `1`. A page that gets a higher `v` it does not know ignores `events` and still reads `egg`. |
| `generated` | string | UTC time the response was made, `YYYY-MM-DDTHH:MM:SSZ`. |
| `cursor` | string | opaque. Pass it back as `since`. Never parse it. |
| `poll_s` | integer | seconds the page should wait before the next poll. The server may raise it under load (up to 300). |
| `egg.xp` | integer | counted community XP. Authoritative. Never compute it from `events`. |
| `egg.pending` | integer | XP from events still inside their settle window (D-058). It may later count or drop. Draw it as a faint second fill, or ignore it. |
| `egg.target` | integer | XP at which the egg starts to crack. |
| `egg.stage` | number | the value to pass to `egg.setStage()`, 0 to 4, computed by the server (section 3.4). |
| `egg.state` | string | `filling`, `cracking` or `hatched`. |
| `egg.crack_at`, `egg.hatch_at` | string or null | UTC times once known. |
| `events` | array | oldest first. At most 50. Empty when nothing new. |
| `events[].id` | string | opaque, 8 lowercase hex characters, unique within the feed. Use it to skip duplicates. It is not a GitHub id and cannot be turned into one. |
| `events[].t` | string | UTC time of the event at GitHub, to the second. |
| `events[].type` | string | `issue`, `pr`, `merge` or `release`. A page treats any other value as `issue`. |
| `events[].level` | integer | 1 to 4: issue 1, pr 2, merge 3, release 4. The animation strength. |
| `events[].weight` | integer | the nominal XP of the type: 1, 3, 10 or 25. It is the same for every event of a type, so it says nothing about who acted. Caps and settling apply only to `egg.xp`. |

There is nothing else in the feed. No titles, numbers, authors, branch names, labels, links or repository content.
New optional fields may be added under `v: 1`; a page must ignore fields it does not know.

Errors: `429` with a `retry-after` header when a client polls too fast; `503` when the store is down. On any error the
page keeps the last good state and retries after `poll_s` (or `retry-after`), doubling up to 300 seconds.

**The demo feed.** `site/brand/egg-feed.demo.json` (to be added by the site build) has the same shape, with `v: 1`,
a fixed `egg` block and about 20 events across all four types. A page given `?feed=demo` reads it and replays it in a
loop, ignoring `t` except for order. The demo is how the site side is built and tested before the function exists.

### 1.2 The egg event API

Added to the object `ExoEgg.mount()` returns. Existing methods keep their meaning: `pulse(n)` still sends `n`
packets out of the core, and `setStage` and `setActivity` are unchanged.

```js
// One repository event. type: "issue" | "pr" | "merge" | "release"; anything else plays as "issue".
egg.event(type, {
  replay: false,      // true: part of the catch-up on page open; plays at 0.6 strength, merges do not bloom twice
  weight: undefined   // optional, from the feed; only shown by a debug HUD, never changes the animation
});

egg.signal(level);    // the same animations by level, 1..4, for callers with no type
egg.queued();         // number of events waiting to play (for a HUD or tests)

ExoEgg.EVENTS;        // { issue: {level: 1}, pr: {level: 2}, merge: {level: 3}, release: {level: 4} }
```

And one small client, in its own file so `egg.js` stays free of network code:

```js
// <script src="/brand/egg-feed.js"></script>  defines window.ExoEggFeed
var feed = ExoEggFeed.connect(egg, {
  url: "/api/egg/feed",   // or "/brand/egg-feed.demo.json" (also chosen by ?feed=demo)
  replay: true,           // play the replay window once on open
  onState: function (eggBlock) {}  // called with the "egg" object after each good poll, for counters and text
});
feed.stop(); feed.start();
```

`connect` polls, calls `egg.setStage(feed.egg.stage)` on each good response, calls `egg.event(type, {replay})` for
each new event in order, and stops polling while the page is hidden (`document.hidden`).

## 2. What each event looks like

All motion uses the egg's existing parts: packets on edges, lit dots, cell glow, the core heartbeat and the stage.
Orange means "something happened now", as everywhere in Exo.

| Level | Type | Animation | Length |
|---|---|---|---|
| 1 | issue | the core heartbeat beats once, brighter; 2 packets leave the core | 0.6 s |
| 2 | pr | a strong beat; 4 packets leave the core, and one long orange packet enters at a random outer dot and travels the lattice to the core, lighting each dot it passes | 1.5 to 2.5 s |
| 3 | merge | a bloom: a wave of cell glow runs from the core to the outer cells, every live dot lights for an instant, 12 packets leave the core | 1.2 s |
| 4 | release | a growth flash: the stage lifts by 0.15 and eases back, the bloom runs twice, 24 packets, then the stage settles at the feed's new value | 2.5 s |

Pacing, so a burst stays readable:

- Events play in order from a queue. Gaps between starts: 0.4 s after a level 1, 1.2 s after a level 2, 2.5 s after
  a level 3 or 4.
- The queue holds at most 20. When it is full, the waiting events are folded into one: the highest level among them,
  played once.
- At most one bloom (level 3 or 4) every 3 seconds; extra merges inside that window play as level 2.
- `egg.event` never changes `activity`. The page may raise `activity` a little while the queue is not empty.

### Replay on first open

The first poll without `since` returns the replay window: the last 24 hours, at most 50 events. With `replay: true`
the page plays them compressed, oldest first, over at most 8 seconds, at 0.6 strength, with at most one bloom. Then it
plays live events at full strength. A page opened on a quiet day sees a short, honest echo of the day.

### Reduced motion

Under `prefers-reduced-motion` the egg already draws one still frame. With the feed:

- No pulses, packets or blooms. `egg.event` only counts.
- The still frame is redrawn when `stage` changes, so the fill still grows.
- A short text line near the egg, in an `aria-live="polite"` region, says what happened, updated at most once a
  minute: "In the last hour: 3 issues, 1 pull request, 2 merges." The same line is available to screen readers on
  every page load.

## 3. The data path

```
GitHub (private repo) --webhook, HMAC-signed--> POST /api/egg/hook  (Vercel function)
                                                   |  verify, classify, apply XP rules
                                                   v
                                         small key-value store (private: event log, settle list, totals)
                                                   |
browser <--poll, CDN-cached 10 s-- GET /api/egg/feed  (Vercel function, public, privacy-safe)
```

### 3.1 Why a webhook and not the API

The repository is private. A page cannot read it, and a token in the page would be public. The function needs no
GitHub token at all: GitHub pushes to it. The only secret is the webhook secret, held as a Vercel environment
variable. No token ever reaches client code or the feed.

### 3.2 The hook: `POST /api/egg/hook`

1. **Size.** Reject bodies over 1 MB (`413`).
2. **Signature.** Compute HMAC-SHA256 of the raw body with `EGG_WEBHOOK_SECRET` and compare with the
   `X-Hub-Signature-256` header in constant time. Mismatch or missing: `401`, nothing stored, nothing logged except a
   counter. Verify before parsing JSON.
3. **Replay.** Keep each `X-GitHub-Delivery` id for 48 hours; a repeat gets `200` and is ignored.
4. **Repository.** Accept only the one configured repository id (`EGG_REPO_ID`).
5. **Events taken.** `issues` (`opened`, `closed`, `labeled`, `deleted`, `transferred`), `pull_request` (`opened`,
   `closed` with `merged`, `labeled`), `release` (`published`), `ping`. Everything else gets `204`.
6. **Bots.** Events whose `sender.type` is `Bot` or whose login ends in `[bot]` are dropped: no XP and no animation, so
   a bot loop cannot make the egg flicker.
7. **Classify and score.** Apply D-058 (`docs/EXOPET.md` section 3): author class (core, agent, outside), settle
   window, caps. The private record keeps an HMAC of the GitHub node id and of the author login (keyed with a second
   secret, `EGG_HASH_KEY`), the class, the type, the time and the status. Logins in the clear are never stored. The
   list of core logins is an environment variable, not a file in the repo.
8. **Publish.** Append the public event (`id`, `t`, `type`, `level`, `weight`) to the feed list, trimmed to the last
   500 and to 7 days.
9. **Write cap.** At most 600 public events an hour. Past that, events still go through the XP rules but are not added
   to the feed. The page would not show them anyway.

A daily cron (`/api/egg/settle`, Vercel cron, guarded by `CRON_SECRET`) moves pending items whose window has passed
to counted, recomputes `xp`, `pending` and `stage`, and deletes private rows older than 8 days. Counted totals are
kept as per-day sums with no item detail.

### 3.3 The feed: `GET /api/egg/feed`

Reads only the public list and the totals. It is cached at the edge for 10 seconds, so a thousand readers cost about
six function runs a minute. A firewall rule limits one address to 30 requests a minute (`429` past that).

Polling, not streaming: events arrive a few times an hour at most, a 20 second poll costs nothing at the edge, and a
held connection would keep a function running for every open tab. `poll_s` lets the server slow everyone down.

### 3.4 Stage from XP

The server computes `egg.stage` so the page does not need the rules:

| State | Stage |
|---|---|
| `filling` | `0.8 * xp / target`, so the shell stays whole until the target |
| `cracking` (24 hours from reaching the target) | eases from 0.8 to 0.95 over the 24 hours |
| `hatched` | 1, then one more stage per factor of 8 in community XP if the principal picks the "keeps evolving" option (`docs/EXOPET.md` open question 3) |

### 3.5 Storage and hosting

One small key-value store from the Vercel Marketplace (a Redis-compatible store) holds three lists and a few
counters; atomic increments keep two hooks from racing. The site stays static; the functions live in `site/api/` and
the deploy script copies that folder into the git-free deploy copy (D-047). Staging and production are separate
Vercel projects: each has its own store, its own secret and its own webhook on the repo, so testing on staging never
moves the production egg.

### 3.6 When the repository goes public

The feed format does not change. The principal may then allow richer public events (for example a link to the item),
but only as new optional fields under a new decision.

## 4. Privacy

| In the feed | Never in the feed |
|---|---|
| event type, level, nominal weight | titles, bodies, comments, labels, branch names |
| time to the second | issue and pull request numbers, GitHub ids, links |
| an opaque id made for the feed | authors, logins, avatars, author class |
| community totals | per-person or per-class counts |

The private store holds keyed hashes, not logins, and drops item rows after 8 days. Function logs record status
codes and counts, never payloads. The time of each event is public while the repo is private; that shows when the
project is busy, which the principal accepted in his brief.

## 5. Build plan

1. **Site side, now, against the demo feed.** `egg.event`, `egg.signal`, the queue, the four animations, reduced
   motion text, `egg-feed.js`, `?feed=demo`. Staging only.
2. **Functions.** `site/api/egg/hook`, `feed`, `settle`; the store; secrets in both Vercel projects; webhooks on the
   repo (staging first). Tests: a fixture payload with a good and a bad signature, a replayed delivery, a bot event,
   a spam-labelled issue that drops out of `pending`.
3. **Production.** Point the production webhook, check the feed for a day on staging, then promote.
