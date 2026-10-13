# The live egg: x0 reacts to repo activity (D-059)

Status: **Proposed** (2026-10-13), revised the same day for instant events, the name x0 and meditation. Nothing here is
built. D-059 in `DECISIONS.md`. The XP rules the feed applies are in `docs/EXOPET.md` section 3 (D-058). The egg
itself is `site/brand/egg.js` (`docs/BRAND.md`, D-049).

The idea: the egg on the project site is the community egg, named **x0** (read "x-o", which sounds like "exo"). Each
person's own copy stays "your Exo". Every time something happens in the repository, x0 shows it at once, so a
contributor sees their contribution land. An issue is a small pulse. A pull request is a stronger pulse with a packet
that travels the lattice. A review is a packet that crosses the lattice. A merge is a bloom. A release is a growth
flash. Each event adds a small share of XP at that moment.

The larger share is earned later, when the team reviews community work in a batch. Then x0 **meditates**: it folds
inward, breathes slowly, and absorbs each contribution that was validated (an issue fixed by a merged pull request, a
pull request merged or salvaged, a review the reviewer bot marked as meaningful), gaining XP for each. Then it wakes.
This mirrors how Exo learns: a little at once from experience, most of it on reflection.

The org's automated reviewer is ion, the org's review bot. The function identifies the bot by its numeric
GitHub account id, never by name, so a rename would change no rule.

## 1. The interface (stable)

This section is the contract between the feed function and the site page. Other sections explain it; they do not
change it. A change here needs a new `v` number in the feed and a note in D-059. This document defines `v: 2`.

### 1.1 The feed

`GET /api/egg/feed` on the site's own host (production and staging each serve their own). Query parameters:

| Parameter | Meaning |
|---|---|
| `since` | optional. The `cursor` from the previous response. Only newer events are returned. Absent or unknown: the replay window (section 2.3). |

Response, `200`, `content-type: application/json`, `cache-control: public, s-maxage=5, stale-while-revalidate=15`:

```json
{
  "v": 2,
  "generated": "2026-10-20T15:00:50Z",
  "cursor": "k7Qm2x",
  "poll_s": 5,
  "egg": {
    "xp": 1290,
    "pending": 64,
    "target": 2048,
    "stage": 0.504,
    "state": "meditating",
    "meditation": {
      "started": "2026-10-20T15:00:00Z",
      "ends": "2026-10-20T15:01:58Z",
      "total": 15,
      "done": 6,
      "progress": 0.4,
      "gained": 52
    },
    "crack_at": null,
    "hatch_at": null
  },
  "events": [
    { "id": "e9c1a04f", "t": "2026-10-20T15:00:20Z", "type": "issue",     "level": 1, "weight": 1 },
    { "id": "4b20d7e3", "t": "2026-10-20T15:00:30Z", "type": "validated", "kind": "pr",     "level": 2, "weight": 10 },
    { "id": "a77f13c0", "t": "2026-10-20T15:00:38Z", "type": "validated", "kind": "review", "level": 2, "weight": 4 },
    { "id": "0c5d92b1", "t": "2026-10-20T15:00:49Z", "type": "review",    "level": 2, "weight": 1 }
  ]
}
```

Fields:

| Field | Type | Meaning |
|---|---|---|
| `v` | integer | format version, `2`. A page that gets a higher `v` it does not know ignores `events` and still reads `egg`. |
| `generated` | string | UTC time the response was made, `YYYY-MM-DDTHH:MM:SSZ`. |
| `cursor` | string | opaque. Pass it back as `since`. Never parse it. |
| `poll_s` | integer | seconds the page should wait before the next poll: 10 normally, 5 while `egg.state` is `meditating` or `waking`. The server may raise it under load (up to 300). |
| `egg.xp` | integer | counted community XP. Authoritative. Never compute it from `events`. |
| `egg.pending` | integer | validated XP waiting for the next meditation. It will be added then (caps permitting). Draw it as a faint second fill, or ignore it. |
| `egg.target` | integer | XP at which the egg starts to crack. |
| `egg.stage` | number | the value to pass to `egg.setStage()`, 0 to 4, computed by the server (section 3.6). |
| `egg.state` | string | `filling`, `meditating`, `waking`, `cracking` or `hatched`. A page treats any other value as `filling`. |
| `egg.meditation` | object or null | present while `state` is `meditating` or `waking`, else `null`. |
| `egg.meditation.started` | string | UTC time the meditation began. |
| `egg.meditation.ends` | string | UTC time the last item will be absorbed. An estimate for display; the state is authoritative. |
| `egg.meditation.total` | integer | validated items in this meditation, 1 or more. |
| `egg.meditation.done` | integer | items absorbed so far, 0 to `total`. |
| `egg.meditation.progress` | number | `done / total`, 0 to 1, rounded to 3 places. |
| `egg.meditation.gained` | integer | XP added by this meditation so far. Already included in `egg.xp`. |
| `egg.crack_at`, `egg.hatch_at` | string or null | UTC times once known. |
| `events` | array | oldest first. At most 50. Empty when nothing new. |
| `events[].id` | string | opaque, 8 lowercase hex characters, unique within the feed. Use it to skip duplicates. It is not a GitHub id, cannot be turned into one, and a `validated` event's id is not linked to the id of the event it validates. |
| `events[].t` | string | UTC time to the second: for repository events, the time at GitHub; for `validated`, the time x0 absorbed it. |
| `events[].type` | string | `issue`, `pr`, `review`, `merge`, `release` or `validated`. A page treats any other value as `issue`. |
| `events[].kind` | string | only on `validated`: `issue`, `pr` or `review`, the kind of contribution that was validated. A page treats any other value as `issue`. |
| `events[].level` | integer | 1 to 4, the animation strength. issue 1, pr 2, review 2, merge 3, release 4. For `validated`, the level of its kind: issue 1, pr 2, review 2. |
| `events[].weight` | integer | the nominal XP of the event. For repository events, the instant XP: issue 1, pr 1, review 1, merge 2, release 25. For `validated`, the validated XP of its kind: issue 4, pr 10, review 4. It is the same for every event of a type and kind, so it says nothing about who acted. Caps apply only to `egg.xp`. |

There is nothing else in the feed. No titles, numbers, authors, branch names, labels, links or repository content.
New optional fields may be added under `v: 2`; a page must ignore fields it does not know. `validated` events within
one meditation are shuffled, so their order says nothing about which items they were.

Changes from `v: 1`, for anyone holding the earlier draft: `poll_s` and the cache are shorter; `egg.pending` now means
validated XP waiting for meditation (it was XP inside a settle window); `egg.state` gains `meditating` and `waking`;
`egg.meditation`, the `review` and `validated` types and `events[].kind` are new; `weight` is now the instant share.
No client was built against `v: 1`. A `v: 1` page would still read `egg` and grow the fill correctly.

Errors: `429` with a `retry-after` header when a client polls too fast; `503` when the store is down. On any error the
page keeps the last good state and retries after `poll_s` (or `retry-after`), doubling up to 300 seconds.

**The demo feed.** `site/brand/egg-feed.demo.json` (to be added by the site build) has the same shape, with `v: 2`.
It is an array of responses, played in order, one per poll, then looped: about 20 events across all five repository
types, then a meditation of 8 `validated` events (3 `pr`, 3 `issue`, 2 `review`) with `meditating` responses whose
`done` rises, one `waking` response, and a return to `filling`. A page given `?feed=demo` reads it and ignores `t`
except for order. The demo is how the site side is built and tested before the function exists.

### 1.2 The egg event API

Added to the object `ExoEgg.mount()` returns. Existing methods keep their meaning: `pulse(n)` still sends `n`
packets out of the core, and `setStage` and `setActivity` are unchanged. Every method returns the egg unless noted.

```js
// One repository event, played at once. type: "issue" | "pr" | "review" | "merge" | "release";
// anything else plays as "issue".
egg.event(type, {
  replay: false,      // true: part of the catch-up on page open; plays at 0.6 strength, merges do not bloom twice
  weight: undefined   // optional, from the feed; only shown by a debug HUD, never changes the animation
});

// One validated contribution: a soft glow travels into the core.
// kind: "issue" | "pr" | "review"; anything else plays as "issue".
egg.event("validated", { kind: "pr", replay: false, weight: undefined });

egg.meditate(true);   // enter meditation: fold inward, dim, slow breathing. Does nothing if already meditating.
egg.meditate(false);  // wake: unfold and return to normal. Does nothing if not meditating.
egg.meditate(true, { immediate: true });   // jump to the meditating pose with no transition (page opened mid-meditation)
egg.meditating();     // returns true from meditate(true) until the wake has finished playing

egg.signal(level);    // the repository animations by level, 1..4, for callers with no type
egg.queued();         // returns the number of events waiting to play (for a HUD or tests)

ExoEgg.EVENTS;        // { issue: {level: 1}, pr: {level: 2}, review: {level: 2}, merge: {level: 3},
                      //   release: {level: 4}, validated: {issue: 1, pr: 2, review: 2} }
```

`egg.event("validated")` while not meditating plays the absorb glow on its own, without the fold. Repository events
while meditating still play at once (section 2.2).

### 1.3 The feed client

One small client, in its own file so `egg.js` stays free of network code:

```js
// <script src="/brand/egg-feed.js"></script>  defines window.ExoEggFeed
var feed = ExoEggFeed.connect(egg, {
  url: "/api/egg/feed",   // or "/brand/egg-feed.demo.json" (also chosen by ?feed=demo)
  replay: true,           // play the replay window once on open
  onState: function (eggBlock) {},       // called with the "egg" object after each good poll, for counters and text
  onEvent: function (ev) {}              // called with each event as it is handed to the egg, for a ticking counter
});
feed.stop(); feed.start();
feed.poll();              // poll now (the client also does this itself when the page becomes visible)
```

On each good response, in this order, `connect`:

1. calls `egg.setStage(egg_block.stage)`;
2. if `egg.state` is `meditating` and `egg.meditating()` is false, calls `egg.meditate(true)`, with
   `{immediate: true}` when this is the first poll of the page;
3. calls `egg.event(type, {replay, weight})` for each new repository event, or
   `egg.event("validated", {kind, replay, weight})` for each `validated` event, in order;
4. if `egg.state` is neither `meditating` nor `waking` and `egg.meditating()` is true, calls `egg.meditate(false)`
   (so the wake plays after the last absorb has been queued);
5. calls `onState(egg_block)`.

It polls again after `poll_s`, polls at once when the page becomes visible (a contributor switching back from GitHub
sees their event within seconds), and stops polling while the page is hidden (`document.hidden`). A page may show an
XP counter that adds `weight` as each `validated` event plays, and must reset it to `egg.xp` on the next poll.

## 2. What each event looks like

All motion uses the egg's existing parts: packets on edges, lit dots, cell glow, the core heartbeat and the stage.
Orange means "something happened now", as everywhere in Exo. Cold blue means "learned", as in the pet (EXOPET section
5), so the absorb glow in meditation is cold blue.

### 2.1 Repository events: instant

| Level | Type | Animation | Length |
|---|---|---|---|
| 1 | issue | the core heartbeat beats once, brighter; 2 packets leave the core | 0.6 s |
| 2 | pr | a strong beat; 4 packets leave the core, and one long orange packet enters at a random outer dot and travels the lattice to the core, lighting each dot it passes | 1.5 to 2.5 s |
| 2 | review | one orange packet enters at a random outer dot, crosses the lattice through the core and leaves at the opposite side, lighting each dot it passes; one beat as it passes the core | 1.5 s |
| 3 | merge | a bloom: a wave of cell glow runs from the core to the outer cells, every live dot lights for an instant, 12 packets leave the core | 1.2 s |
| 4 | release | a growth flash: the stage lifts by 0.15 and eases back, the bloom runs twice, 24 packets, then the stage settles at the feed's new value | 2.5 s |

No delay is added anywhere. An event starts the moment the poll that carries it returns, if nothing is playing.
GitHub delivers the webhook within a second or two, the edge cache adds at most 5 seconds and the poll at most 10, so a
contributor watching the site sees their event in about 15 seconds at worst, and within a few seconds when they switch
back to the tab.

Pacing, only when several events arrive in the same poll, so a burst stays readable:

- They play in order from a queue. Gaps between starts: 0.4 s after a level 1, 1.2 s after a level 2, 2.5 s after a
  level 3 or 4.
- The queue holds at most 20. When it is full, the waiting events are folded into one: the highest level among them,
  played once.
- At most one bloom (level 3 or 4) every 3 seconds; extra merges inside that window play as level 2.
- `egg.event` never changes `activity`. The page may raise `activity` a little while the queue is not empty.

### 2.2 Meditation

| Phase | Motion | Length |
|---|---|---|
| Enter (`egg.meditate(true)`) | packets already in flight finish; the lattice folds inward toward close packing (the fold mechanism, closing about two thirds of the way) and the shell draws in by 6 percent; line alpha dims to 80 percent; `activity` eases to 0.15 | 3 s |
| Breathe | a slow, calm breath: in over 2.5 s (the fold opens slightly, the core brightens by 10 percent), hold 0.5 s, out over 3 s. No packets, no orange. Runs until the wake | 6 s a cycle |
| Absorb (each `validated` event) | a soft cold-blue glow appears at a random outer dot, travels inward along lattice edges to the core, lighting each dot it passes faintly, and the core glows cold blue once as it arrives. Size by kind: issue small, review medium, pr large with a short trail. Starts on the next in-breath | 2 s |
| Wake (`egg.meditate(false)`) | one deep breath; the lattice unfolds to its open pose and the shell returns; line alpha returns to full; one soft orange beat; `activity` returns to its value before meditation | 2.5 s |

Absorbs queue like repository events, at least 1.5 s apart, at most 40 waiting (past that, the extras fold into the
glow of the last one). A repository event during meditation plays at once at 0.6 strength over the folded lattice,
without breaking the breath; merges and releases do not bloom (they play as level 2 with a brighter beat). The XP
counter on the page ticks up by `weight` as each glow reaches the core.

### 2.3 Replay on first open

The first poll without `since` returns the replay window: the last 24 hours, at most 50 events. With `replay: true`
the page plays them compressed, oldest first, over at most 8 seconds, at 0.6 strength, with at most one bloom and no
fold (validated items in the window play as absorb glows only). Then it plays live events at full strength. If x0 is
meditating when the page opens, the page starts in the meditating pose (`{immediate: true}`) and plays only the
absorbs that arrive after that. A page opened on a quiet day sees a short, honest echo of the day.

### 2.4 Reduced motion

Under `prefers-reduced-motion` the egg already draws one still frame. With the feed:

- No pulses, packets, blooms, breathing or glows. `egg.event` only counts.
- The still frame is redrawn when `stage` changes, so the fill still grows, and when `meditate` is called: the
  meditating still is the folded, dimmed pose with the core in cold blue; the wake redraws the open pose.
- A short text line near the egg, in an `aria-live="polite"` region, says what happened, updated at most once a
  minute: "In the last hour: 3 issues, 1 pull request, 2 reviews, 2 merges." While meditating it reads "x0 is
  meditating: 6 of 15 contributions absorbed, 52 XP gained.", updated at most every 15 seconds; after the wake, "x0
  woke: 15 contributions validated, 120 XP gained." The same line is available to screen readers on every page load.

## 3. The data path

```
GitHub (private repo) --webhook, HMAC-signed--> POST /api/egg/hook      (Vercel function)
                                                   |  verify, classify, instant XP, record validations
maintainer --GitHub Actions, HMAC-signed------> POST /api/egg/meditate  (Vercel function)
                                                   |  read the bot's reactions, schedule the absorbs
                                                   v
                                         small key-value store (private: item log, validation queue, totals)
                                                   |
browser <--poll, CDN-cached 5 s-- GET /api/egg/feed  (Vercel function, public, privacy-safe)
```

### 3.1 Secrets and the one token

The repository is private. A page cannot read it, and a token in the page would be public. The hook needs no GitHub
token: GitHub pushes to it. Reactions are the exception: GitHub sends no webhook for them, so the meditate function
reads them through the API with one token, `EGG_GITHUB_TOKEN`: fine-grained, read-only (pull requests: read,
metadata: read), on this one repository, used by nothing else. The other secrets are `EGG_WEBHOOK_SECRET`,
`EGG_MEDITATE_SECRET`, `EGG_HASH_KEY` and `CRON_SECRET`. All are Vercel environment variables; none ever reaches
client code or the feed.

### 3.2 The hook: `POST /api/egg/hook`

1. **Size.** Reject bodies over 1 MB (`413`).
2. **Signature.** Compute HMAC-SHA256 of the raw body with `EGG_WEBHOOK_SECRET` and compare with the
   `X-Hub-Signature-256` header in constant time. Mismatch or missing: `401`, nothing stored, nothing logged except a
   counter. Verify before parsing JSON.
3. **Replay.** Keep each `X-GitHub-Delivery` id for 48 hours; a repeat gets `200` and is ignored.
4. **Repository.** Accept only the one configured repository id (`EGG_REPO_ID`).
5. **Events taken.** `issues` (`opened`, `closed`, `labeled`, `deleted`, `transferred`), `pull_request` (`opened`,
   `closed`, `edited`, `labeled`), `pull_request_review` (`submitted`), `release` (`published`), `ping`. Everything
   else gets `204`.
6. **Bots.** Events whose `sender.type` is `Bot` or whose login ends in `[bot]` are dropped: no XP and no animation, so
   a bot loop cannot make the egg flicker. This includes reviews by ion, the org's review bot.
7. **Classify and score.** Apply D-058 (`docs/EXOPET.md` section 3): author class (core, agent, outside), instant XP
   and its caps, voids. The private record keeps an HMAC of the item's repository number and of the author login
   (keyed with `EGG_HASH_KEY`), the GitHub node id of reviews (needed to read reactions), the class, the type, the time
   and the status. Logins in the clear are never stored. The list of core logins is an environment variable, not a
   file in the repo.
8. **Record validations.** On a merged pull request, and on an `edited` merged pull request up to 7 days after the
   merge, apply the detection rules in section 3.3. Each validated item joins the validation queue with its kind and
   XP; `egg.pending` rises.
9. **Publish.** Append the public event (`id`, `t`, `type`, `level`, `weight`) to the feed list, trimmed to the last
   500 and to 7 days. Instant XP goes straight into the totals, so `egg.xp` and `egg.stage` move on the same poll that
   carries the event.
10. **Write cap.** At most 600 public events an hour. Past that, events still go through the XP rules but are not
    added to the feed. The page would not show them anyway.

### 3.3 Detection rules for validation

All three are checked by the function from data GitHub sends or holds; none needs a person to type a number into a
form.

| Kind | Validated when | How the function detects it |
|---|---|---|
| issue | a pull request that fixes it was merged | the merged pull request's title or body contains a GitHub closing keyword (`close`, `closes`, `closed`, `fix`, `fixes`, `fixed`, `resolve`, `resolves`, `resolved`, any case, optional colon) followed by `#N` or `<this repo>#N`. These are the same words GitHub uses to close the issue itself, and the team's pull requests already say "Fixes #N". Issue N must be known to the store, not voided, not already validated, and opened before the merge. At most 3 issues per merged pull request. |
| pr | it was merged, or it was salvaged: its commits or a substantial part of its diff landed through another pull request | merged: the `pull_request` `closed` event with `merged: true`. Salvaged: the landing pull request, when merged, has a line `Salvaged-from: #N` in its body (one per line, any case, at most 3). Pull request N must be closed without merge, not voided, not already validated, and opened by a different account than the landing pull request (an author who re-lands their own work is credited by that merge). The maintainer who merges is answerable for the line; ion, the org's review bot may propose it when it sees the same commits. |
| review | ion, the org's review bot judged it a meaningful contribution | at meditation start, for each recorded review from the last 30 days not yet validated, the function asks the GitHub GraphQL API for the review's `ROCKET` reactions and checks whether one is from the account whose numeric id is `EGG_REVIEWER_BOT_ID`. Reactions from any other account, including maintainers, are ignored. A reaction removed before the meditation does not count; once absorbed, a validation stands. |

**The reaction is `rocket`.** It is the one reaction that says "this moved the work forward", which is what a
meaningful review does. `+1`, `heart` and `hooray` are what people use every day for agreement, thanks and
celebration, so a bot's mark there would blur into the crowd and read as praise rather than a judgement. `eyes` means
"looking at it" and `confused` and `-1` are negative. `rocket` is rare in review threads, reads well to a contributor
who sees it on their review, and survives the bot's rename because the check is on the bot's account id, not the
emoji alone.

The edit window (7 days after the merge) lets a maintainer add a missed "Fixes #N" or `Salvaged-from: #N` line by
editing the merged pull request's body.

### 3.4 Meditation: `POST /api/egg/meditate`

**Start: a maintainer action.** A GitHub Actions workflow, `x0-meditate`, run by hand (`workflow_dispatch`) when the
team sits down to review community work. Only people with write access can run it, every run is in the Actions log,
and ion, the org's review bot can dispatch it too at the end of a batch run. The workflow sends
`{"action": "start", "ts": <unix seconds>, "nonce": <random>}` signed with HMAC-SHA256 under `EGG_MEDITATE_SECRET`
(held as a repository Actions secret and a Vercel variable) in the `X-Egg-Signature-256` header. The function rejects
a bad signature (`401`), a `ts` more than 5 minutes off, or a nonce seen in the last 48 hours.

A label was considered and rejected: labels live on one issue or pull request, while a meditation covers the whole
batch, and a label event says less about who acted. A fixed schedule was rejected as the trigger because it would
meditate whether or not the team reviewed anything, which is not what the state means. It stays only as a backstop:
if validated XP has waited 14 days with no meditation, the daily cron starts one, so nothing waits forever.

**What start does.**

1. Refuse with `409` if a meditation is running.
2. Read the bot's reactions for candidate reviews (section 3.3) and add newly validated reviews to the queue.
3. Take every item in the queue. If there are none, return `200 {"total": 0}`: x0 does not meditate on nothing.
4. Apply the caps (`docs/EXOPET.md` section 3) and compute each item's XP.
5. Shuffle the items and give each an absorb time: the first 6 seconds after start (the enter phase), then one every
   `clamp(900 / total, 3, 8)` seconds, so a meditation lasts between about 30 seconds and 15 minutes.
6. Store the meditation record (`started`, `ends`, the items with their times) and return `200 {"total": N}`.

**End.** The feed computes the state from the record and the clock: `meditating` until `ends`, `waking` for 30 seconds
after, then `filling` (or `cracking` if `xp` reached the target, or `hatched` after the hatch). Each item's XP counts
from its absorb time. The first feed read or cron after the wake folds the record into the totals in one atomic write.

A meditation can be stopped early with `{"action": "stop"}`: absorbed items stay counted, the rest return to the
queue.

### 3.5 The feed: `GET /api/egg/feed`

Reads only the public list, the totals and the meditation record. It is cached at the edge for 5 seconds, so a
thousand readers cost about twelve function runs a minute. A firewall rule limits one address to 30 requests a minute
(`429` past that). `validated` events are generated from the record as their absorb times pass.

Polling, not streaming: events arrive a few times an hour at most, a 10 second poll costs nothing at the edge, and a
held connection would keep a function running for every open tab. `poll_s` lets the server slow everyone down.

A daily cron (`/api/egg/settle`, Vercel cron, guarded by `CRON_SECRET`) applies voids, starts the backstop
meditation, and deletes private rows (section 4). Counted totals are kept as per-day sums with no item detail.

### 3.6 Stage from XP

The server computes `egg.stage` so the page does not need the rules:

| State | Stage |
|---|---|
| `filling`, `meditating`, `waking` | `0.8 * xp / target`, so the shell stays whole until the target |
| `cracking` (24 hours from reaching the target) | eases from 0.8 to 0.95 over the 24 hours |
| `hatched` | 1, then one more stage per factor of 8 in community XP if the principal picks the "keeps evolving" option (`docs/EXOPET.md` open question 3) |

Because most XP arrives during meditation, x0 usually reaches its target while meditating: it wakes, then cracks.

### 3.7 Storage and hosting

One small key-value store from the Vercel Marketplace (a Redis-compatible store) holds the lists, the validation
queue, the meditation record and a few counters; atomic increments keep two hooks from racing. The site stays static;
the functions live in `site/api/` and the deploy script copies that folder into the git-free deploy copy (D-047).
Staging and production are separate Vercel projects: each has its own store, secrets, token and webhook, so testing on
staging never moves the production egg. The `x0-meditate` workflow takes the target (staging or production) as an
input.

### 3.8 When the repository goes public

The feed format does not change. The principal may then allow richer public events (for example a link to the item),
but only as new optional fields under a new decision.

## 4. Privacy

| In the feed | Never in the feed |
|---|---|
| event type, kind, level, nominal weight | titles, bodies, comments, labels, branch names |
| time to the second | issue and pull request numbers, GitHub ids, links |
| an opaque id made for the feed, unlinked between an event and its validation | authors, logins, avatars, author class |
| community totals, meditation progress | per-person or per-class counts, which items were validated |

The private store holds keyed hashes, not logins. Rows for items that can still be validated (open issues, pull
requests closed without merge, reviews) are kept until validated, voided or 90 days old (reviews: 30 days), then
dropped; other rows go after 8 days. Function logs record status codes and counts, never payloads. The time of each
event is public while the repo is private; that shows when the project is busy, which the principal accepted in his
brief.

## 5. Build plan

1. **Site side, now, against the demo feed.** `egg.event` for all six types, `egg.meditate`, `egg.meditating`,
   `egg.signal`, the queues, the animations, the meditation phases, reduced motion stills and text, `egg-feed.js`,
   `?feed=demo`. Staging only.
2. **Functions.** `site/api/egg/hook`, `meditate`, `feed`, `settle`; the store; secrets and the read-only token in both
   Vercel projects; webhooks on the repo; the `x0-meditate` workflow (staging first). Tests: a fixture payload with a
   good and a bad signature, a replayed delivery, a bot event, a spam-labelled issue that never validates, a merged
   pull request with "Fixes #N" and a `Salvaged-from` line, a review with a `rocket` from the bot's id and one from a
   maintainer, a meditation start with a bad signature, an empty queue, and a stop.
3. **Production.** Point the production webhook, check the feed and one meditation for a day on staging, then
   promote.
