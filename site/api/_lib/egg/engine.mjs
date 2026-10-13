/* The x0 state machine (docs/LIVE-EGG.md section 3, docs/EXOPET.md section 3). Pure: it changes a state object
 * and never does I/O. The HTTP layer (http.mjs) loads the state, takes the lock, prefetches anything from GitHub,
 * and saves the state.
 *
 * The state is one private JSON document. It holds keyed hashes, never logins, numbers or titles:
 *   xp, crackAt                      counted XP (meditation gains are folded in after the wake) and the crack time
 *   coreDay, coreLife                core XP per UTC day and over x0's life (instant and validated)
 *   outsideDay, outsideHour, outsideLife, acctDay   outside instant XP caps
 *   reviewDay, reviewPr              validated reviews per account per day and per pull request
 *   lastRelease                      the last release that counted
 *   items                            { hash: record } for issues, pull requests and reviews (section 3.2 step 7)
 *   queue                            validated items waiting for meditation
 *   meditation                       the running meditation, or null
 *   events                           the public list: { id, t, type, kind?, level, weight, vis } (vis is private)
 */
import {
  INSTANT, VALIDATED, LEVEL, VALIDATED_LEVEL, CAPS, DAY_MS, utcDay, utcHour, iso, isCore, isAgentWork,
  hasVoidLabel, closingRefs, salvagedRefs, stageFor, round3, AGENT_LABEL, VOID_LABELS
} from "./rules.mjs";
import { feedId, shuffle } from "./crypto.mjs";

export const WAKE_MS = 30e3, ENTER_MS = 6e3, REPLAY_MS = DAY_MS, MAX_EVENTS = 50, OVERLAP_MS = 2e3;

export function emptyState() {
  return {
    v: 1, xp: 0, crackAt: null, coreDay: {}, coreLife: 0, outsideDay: {}, outsideHour: {}, outsideLife: 0,
    acctDay: {}, reviewDay: {}, reviewPr: {}, lastRelease: 0, items: {}, queue: [], meditation: null,
    events: [], publicHour: {}, daySums: {}, lastMeditation: 0
  };
}

const inc = (o, k, n) => { o[k] = (o[k] || 0) + n; };
const coreLifeCap = (cfg) => Math.floor(CAPS.coreLifeShare * cfg.target);
const outsideLifeCap = (cfg) => Math.floor(CAPS.outsideLifeShare * cfg.target);
const ts = (s, now) => { const t = Date.parse(s); return isNaN(t) ? now : t; };

function addXp(S, cfg, x, now) {
  if (x <= 0) return;
  S.xp += x; inc(S.daySums, utcDay(now), x);
  if (!S.crackAt && S.xp >= cfg.target) S.crackAt = now;
}

/* Instant XP for one repository event, after every cap. Never taken back. */
export function grantInstant(S, cfg, now, ev) {
  const day = utcDay(now), hour = utcHour(now);
  let x = INSTANT[ev.type] || 0;
  if (ev.type === "release") {                                       // validated by the act; outside the core cap
    if (!ev.counts || now - S.lastRelease < CAPS.releaseEveryDays * DAY_MS) return 0;
    S.lastRelease = now; addXp(S, cfg, x, now); return x;
  }
  if (ev.agent && ev.type !== "merge") x = 0;                       // agent issues, PRs and reviews earn nothing
  if (ev.own) x = 0;                                                 // a review of one's own pull request
  if (ev.cls === "core") {
    x = Math.max(0, Math.min(x, CAPS.coreDay - (S.coreDay[day] || 0), coreLifeCap(cfg) - S.coreLife));
    inc(S.coreDay, day, x); S.coreLife += x;
  } else {
    if (ev.young) x = 0;                                             // accounts newer than 7 days
    if (ev.type !== "merge") x = Math.min(x, CAPS.accountDay - (S.acctDay[ev.author + "|" + day] || 0));
    x = Math.max(0, Math.min(x, CAPS.outsideHour - (S.outsideHour[hour] || 0), CAPS.outsideDay - (S.outsideDay[day] || 0),
      outsideLifeCap(cfg) - S.outsideLife));
    if (ev.type !== "merge") inc(S.acctDay, ev.author + "|" + day, x);
    inc(S.outsideHour, hour, x); inc(S.outsideDay, day, x); S.outsideLife += x;
  }
  addXp(S, cfg, x, now);
  return x;
}

function publish(S, now, type, t, extra) {
  const hour = utcHour(now);
  if ((S.publicHour[hour] || 0) >= CAPS.publicPerHour) return null;  // the write cap: XP still counts
  inc(S.publicHour, hour, 1);
  const taken = new Set(S.events.map((e) => e.id));
  const e = Object.assign({ id: feedId(taken), t: iso(t), type, level: LEVEL[type], weight: INSTANT[type], vis: now }, extra || {});
  S.events.push(e);
  trimEvents(S, now);
  return e;
}

export function trimEvents(S, now) {
  S.events = S.events.filter((e) => e.vis > now || e.vis >= now - CAPS.feedKeepMs);
  S.events.sort((a, b) => a.vis - b.vis);
  if (S.events.length > CAPS.feedKeep) {
    const future = S.events.filter((e) => e.vis > now), past = S.events.filter((e) => e.vis <= now);
    S.events = past.slice(Math.max(0, past.length - (CAPS.feedKeep - future.length))).concat(future);
  }
}

function queueValidation(S, key, kind, day, now) {
  const it = S.items[key];
  if (!it || it.void || it.val) return false;
  if (it.agent && kind !== "pr") return false;                       // agent issues and reviews never validate
  it.val = "queued"; it.touched = now;
  S.queue.push({ key, kind, nominal: VALIDATED[kind], a: it.a, cls: it.cls, day, queued: now });
  return true;
}

function voidItem(S, key, now) {
  const it = S.items[key];
  if (!it || it.val === "done") return;                              // once absorbed, a validation stands
  it.void = true; it.touched = now;
  if (it.val === "queued") { S.queue = S.queue.filter((q) => q.key !== key); it.val = undefined; }
}

/* The fixes and salvages a merged pull request carries. Called on merge and on edits within 7 days. */
function applyRefs(S, cfg, h, pr, title, body, now) {
  pr.fixes = pr.fixes || []; pr.salv = pr.salv || [];
  const day = utcDay(pr.merged);
  for (const n of closingRefs(title + "\n" + body, cfg.repo)) {
    if (pr.fixes.length >= CAPS.issuesPerMerge) break;
    const key = h("num", n), it = S.items[key];
    if (!it || it.k !== "issue" || pr.fixes.includes(key) || !(it.opened < pr.merged)) continue;
    if (queueValidation(S, key, "issue", day, now)) pr.fixes.push(key);
  }
  for (const n of salvagedRefs(body)) {
    if (pr.salv.length >= CAPS.salvagesPerMerge) break;
    const key = h("num", n), it = S.items[key];
    if (!it || it.k !== "pr" || pr.salv.includes(key) || !it.closed || it.merged || it.a === pr.a) continue;
    if (queueValidation(S, key, "pr", day, now)) pr.salv.push(key);
  }
}

/* Who an event credits, so the HTTP layer can look up account age before taking the lock. */
export function creditedUser(event, p) {
  if (event === "issues" && p.action === "opened") return p.issue && p.issue.user;
  if (event === "pull_request" && (p.action === "opened" || (p.action === "closed" && p.pull_request && p.pull_request.merged)))
    return p.pull_request && p.pull_request.user;
  if (event === "pull_request_review" && p.action === "submitted") return p.review && p.review.user;
  return null;
}

/* Apply one verified, accepted webhook delivery. ctx: { cfg, now, h(kind, value), young }.
   Returns the public event written, or null. */
export function applyEvent(S, ctx, event, p) {
  const { cfg, now, h } = ctx;
  const acct = (u) => h("acct", u && u.id);
  const cls = (u) => (isCore(u, cfg) ? "core" : "outside");
  if (event === "issues" && p.issue) {
    const I = p.issue, key = h("num", I.number);
    if (p.action === "opened") {
      if (S.items[key]) return null;
      const agent = isAgentWork(I.labels, I.body);
      S.items[key] = { k: "issue", a: acct(I.user), cls: cls(I.user), agent, opened: ts(I.created_at, now), touched: now };
      grantInstant(S, cfg, now, { type: "issue", author: S.items[key].a, cls: S.items[key].cls, agent, young: ctx.young });
      return publish(S, now, "issue", ts(I.created_at, now));
    }
    const it = S.items[key];
    if (!it) return null;
    if (p.action === "deleted" || p.action === "transferred") voidItem(S, key, now);
    else if (p.action === "labeled") {
      if (p.label && VOID_LABELS.has(String(p.label.name).toLowerCase())) voidItem(S, key, now);
      if (p.label && String(p.label.name).toLowerCase() === AGENT_LABEL && !it.val) it.agent = true;
    } else if (p.action === "closed") {
      it.closed = now; it.touched = now;
      const own = p.sender && I.user && String(p.sender.id) === String(I.user.id);
      if (hasVoidLabel(I.labels) || (own && now - it.opened < CAPS.voidOwnCloseMs)) voidItem(S, key, now);
    }
    return null;
  }
  if (event === "pull_request" && p.pull_request) {
    const P = p.pull_request, key = h("num", P.number);
    if (p.action === "opened") {
      if (S.items[key]) return null;
      const agent = isAgentWork(P.labels, P.body);
      S.items[key] = { k: "pr", a: acct(P.user), cls: cls(P.user), agent, opened: ts(P.created_at, now), touched: now };
      grantInstant(S, cfg, now, { type: "pr", author: S.items[key].a, cls: S.items[key].cls, agent, young: ctx.young });
      return publish(S, now, "pr", ts(P.created_at, now));
    }
    let it = S.items[key];
    if (p.action === "closed" && P.merged) {
      if (!it) it = S.items[key] = { k: "pr", a: acct(P.user), cls: cls(P.user), agent: isAgentWork(P.labels, P.body), opened: ts(P.created_at, now) };
      if (it.merged) return null;
      it.merged = ts(P.merged_at, now); it.closed = now; it.touched = now;
      grantInstant(S, cfg, now, { type: "merge", author: it.a, cls: it.cls, agent: it.agent, young: ctx.young });
      queueValidation(S, key, "pr", utcDay(it.merged), now);
      applyRefs(S, cfg, h, it, P.title || "", P.body || "", now);
      return publish(S, now, "merge", it.merged);
    }
    if (!it) return null;
    if (p.action === "closed") {
      it.closed = now; it.touched = now;
      const own = p.sender && P.user && String(p.sender.id) === String(P.user.id);
      if (hasVoidLabel(P.labels) || (own && now - it.opened < CAPS.voidOwnCloseMs)) voidItem(S, key, now);
    } else if (p.action === "edited") {
      if (it.merged && now - it.merged <= CAPS.editWindowDays * DAY_MS) applyRefs(S, cfg, h, it, P.title || "", P.body || "", now);
    } else if (p.action === "labeled") {
      if (p.label && VOID_LABELS.has(String(p.label.name).toLowerCase()) && !it.merged) voidItem(S, key, now);
      if (p.label && String(p.label.name).toLowerCase() === AGENT_LABEL && !it.val) it.agent = true;
    }
    return null;
  }
  if (event === "pull_request_review" && p.action === "submitted" && p.review && p.pull_request) {
    const R = p.review, key = h("review", R.id);
    if (S.items[key]) return null;
    const own = R.user && p.pull_request.user && String(R.user.id) === String(p.pull_request.user.id);
    const agent = isAgentWork([], R.body);
    const at = ts(R.submitted_at, now);
    S.items[key] = { k: "review", a: acct(R.user), cls: cls(R.user), agent, own: !!own, node: String(R.node_id || ""),
      pr: h("num", p.pull_request.number), opened: at, touched: now };
    grantInstant(S, cfg, now, { type: "review", author: S.items[key].a, cls: S.items[key].cls, agent, own, young: ctx.young });
    return publish(S, now, "review", at);
  }
  if (event === "release" && p.action === "published" && p.release) {
    const counts = /^exo-v/i.test(String(p.release.tag_name || ""));
    grantInstant(S, cfg, now, { type: "release", counts });
    return publish(S, now, "release", ts(p.release.published_at, now));
  }
  return null;
}

/* Reviews that could be validated by the bot's rocket: recorded in the last 30 days, not yet validated. */
export function reviewCandidates(S, now) {
  const out = [];
  for (const [key, it] of Object.entries(S.items)) {
    if (it.k !== "review" || it.val || it.void || it.own || it.agent || !it.node) continue;
    if (now - it.opened > CAPS.reviewWindowDays * DAY_MS) continue;
    out.push({ key, node: it.node });
  }
  return out;
}

export function meditationState(S, now) {
  const M = S.meditation;
  if (!M) return null;
  if (now < M.ends) return "meditating";
  if (now < M.ends + WAKE_MS) return "waking";
  return "done";
}

/* Start a meditation (section 3.4). rockets: the set of review node ids the bot marked. */
export function startMeditation(S, cfg, now, rockets) {
  const st = meditationState(S, now);
  if (st === "meditating" || st === "waking") return { status: 409, body: { error: "meditation running" } };
  if (st === "done") fold(S, cfg, now);
  for (const c of reviewCandidates(S, now)) {
    if (!rockets || !rockets.has(c.node)) continue;
    const it = S.items[c.key], day = utcDay(it.opened);
    const kp = it.a + "|" + it.pr, kd = it.a + "|" + day;
    if ((S.reviewPr[kp] || 0) >= CAPS.reviewsPerAccountPr || (S.reviewDay[kd] || 0) >= CAPS.reviewsPerAccountDay) {
      it.val = "capped"; continue;
    }
    if (queueValidation(S, c.key, "review", day, now)) { inc(S.reviewPr, kp, 1); inc(S.reviewDay, kd, 1); }
  }
  if (!S.queue.length) return { status: 200, body: { total: 0 } };
  const items = shuffle(S.queue);
  const gap = Math.min(8, Math.max(3, 900 / items.length)) * 1000;
  const taken = new Set(S.events.map((e) => e.id));
  const med = { started: now, ends: 0, items: [] };
  items.forEach((q, i) => {
    let x = q.nominal;
    if (q.cls === "core") {                                          // validated core XP is dated to its validation day
      x = Math.max(0, Math.min(x, CAPS.coreDay - (S.coreDay[q.day] || 0), coreLifeCap(cfg) - S.coreLife));
      inc(S.coreDay, q.day, x); S.coreLife += x;                     // reserved now, released if the run is stopped
    }
    const at = now + ENTER_MS + i * gap, id = feedId(taken);
    taken.add(id);
    med.items.push({ id, at, xp: x, kind: q.kind, nominal: q.nominal, cls: q.cls, day: q.day, key: q.key, a: q.a, queued: q.queued });
    if (S.items[q.key]) S.items[q.key].val = "done";
    S.events.push({ id, t: iso(at), type: "validated", kind: q.kind, level: VALIDATED_LEVEL[q.kind], weight: q.nominal, vis: at });
  });
  med.ends = med.items[med.items.length - 1].at;
  S.queue = []; S.meditation = med;
  trimEvents(S, now);
  return { status: 200, body: { total: med.items.length } };
}

/* Stop early: absorbed items stay counted, the rest return to the queue. */
export function stopMeditation(S, cfg, now) {
  if (meditationState(S, now) !== "meditating") return { status: 409, body: { error: "not meditating" } };
  const M = S.meditation, kept = [], back = [];
  for (const it of M.items) (it.at <= now ? kept : back).push(it);
  const drop = new Set(back.map((b) => b.id));
  S.events = S.events.filter((e) => !drop.has(e.id));
  for (const b of back) {
    if (b.cls === "core") { S.coreDay[b.day] = Math.max(0, (S.coreDay[b.day] || 0) - b.xp); S.coreLife = Math.max(0, S.coreLife - b.xp); }
    if (S.items[b.key]) S.items[b.key].val = "queued";
    S.queue.push({ key: b.key, kind: b.kind, nominal: b.nominal, a: b.a, cls: b.cls, day: b.day, queued: b.queued });
  }
  if (!kept.length) S.meditation = null;
  else { M.items = kept; M.ends = Math.min(M.ends, now); }
  return { status: 200, body: { absorbed: kept.length, returned: back.length } };
}

/* After the wake, fold the meditation into the totals in one write. Returns true when something changed. */
export function fold(S, cfg, now) {
  if (meditationState(S, now) !== "done") return false;
  for (const it of S.meditation.items) { S.xp += it.xp; inc(S.daySums, utcDay(it.at), it.xp); }
  if (!S.crackAt && S.xp >= cfg.target) S.crackAt = S.meditation.ends + WAKE_MS;
  S.lastMeditation = S.meditation.ends; S.meditation = null;
  return true;
}

/* Delete private rows per docs/LIVE-EGG.md section 4, and old counters. */
export function prune(S, now) {
  for (const [key, it] of Object.entries(S.items)) {
    const open = !it.void && !it.val && (it.k === "issue" || it.k === "review" || !it.merged);
    const keep = open ? (it.k === "review" ? CAPS.reviewWindowDays : 90) * DAY_MS : 8 * DAY_MS;
    const since = open ? it.opened : (it.touched || it.opened);
    if (now - since > keep) delete S.items[key];
  }
  const cut = utcDay(now - 8 * DAY_MS);
  for (const m of [S.coreDay, S.outsideDay, S.daySums]) for (const k of Object.keys(m)) if (k < cut) delete m[k];
  for (const m of [S.acctDay, S.reviewDay]) for (const k of Object.keys(m)) if (k.split("|")[1] < cut) delete m[k];
  for (const k of Object.keys(S.outsideHour)) if (k < cut) delete S.outsideHour[k];
  for (const k of Object.keys(S.publicHour)) if (k < cut) delete S.publicHour[k];
  for (const k of Object.keys(S.reviewPr)) if (!S.items[k.split("|")[1]]) delete S.reviewPr[k];   // the pull request's row is gone
  trimEvents(S, now);
}

/* The 14-day backstop: validated XP has waited that long with no meditation. */
export function backstopDue(S, now) {
  if (S.meditation || !S.queue.length) return false;
  const oldest = Math.min(...S.queue.map((q) => q.queued));
  return now - oldest >= CAPS.backstopDays * DAY_MS && now - (S.lastMeditation || 0) >= CAPS.backstopDays * DAY_MS;
}

export function encodeCursor(ms) { return "c" + Math.max(0, Math.floor(ms)).toString(36); }
export function decodeCursor(s) {
  if (typeof s !== "string" || !/^c[0-9a-z]{1,11}$/.test(s)) return null;
  const n = parseInt(s.slice(1), 36);
  return Number.isFinite(n) ? n : null;
}

/* The public feed, v: 2, exactly per docs/LIVE-EGG.md section 1.1. Nothing private leaves this function. */
export function feedView(S, cfg, now, since) {
  const st = meditationState(S, now);
  const M = st === "meditating" || st === "waking" ? S.meditation : null;
  const done = M ? M.items.filter((i) => i.at <= now) : [];
  const gained = done.reduce((a, i) => a + i.xp, 0);
  const xp = S.xp + gained;
  let state = M ? st : "filling";
  if (!M && S.crackAt) state = now < S.crackAt + DAY_MS ? "cracking" : "hatched";
  const visible = S.events.filter((e) => e.vis <= now).sort((a, b) => a.vis - b.vis);
  const sinceMs = since == null ? null : decodeCursor(since);
  let list, cursorMs = now - OVERLAP_MS;
  if (sinceMs == null || sinceMs < now - CAPS.feedKeepMs) {
    list = visible.filter((e) => e.vis > now - REPLAY_MS).slice(-MAX_EVENTS);
  } else {
    list = visible.filter((e) => e.vis > sinceMs);
    if (list.length > MAX_EVENTS) { list = list.slice(0, MAX_EVENTS); cursorMs = list[list.length - 1].vis; }
    cursorMs = Math.max(cursorMs, sinceMs);
  }
  return {
    v: 2,
    generated: iso(now),
    cursor: encodeCursor(cursorMs),
    poll_s: M ? 5 : 10,
    egg: {
      xp,
      pending: S.queue.reduce((a, q) => a + q.nominal, 0),
      target: cfg.target,
      stage: stageFor(state, xp, cfg.target, now, S.crackAt),
      state,
      meditation: M ? {
        started: iso(M.started), ends: iso(M.ends), total: M.items.length, done: done.length,
        progress: round3(done.length / M.items.length), gained
      } : null,
      crack_at: S.crackAt ? iso(S.crackAt) : null,
      hatch_at: S.crackAt ? iso(S.crackAt + DAY_MS) : null
    },
    events: list.map((e) => (e.type === "validated"
      ? { id: e.id, t: e.t, type: e.type, kind: e.kind, level: e.level, weight: e.weight }
      : { id: e.id, t: e.t, type: e.type, level: e.level, weight: e.weight }))
  };
}
