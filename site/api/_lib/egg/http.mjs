/* The HTTP layer for the x0 functions: web-standard Request in, Response out (Vercel's Node.js runtime, no
 * framework). Each handler takes its dependencies, so tests pass a memory store, a fixed clock and a fake GitHub.
 *
 *   POST /api/egg/webhook   GitHub webhook receiver (also served at /api/egg/hook, the name in docs/LIVE-EGG.md)
 *   GET  /api/egg/feed      the public feed, v: 2
 *   POST /api/egg/meditate  signed start or stop, from the x0-meditate workflow
 *   GET  /api/egg/settle    the daily cron (Authorization: Bearer CRON_SECRET)
 *
 * Logs carry status codes and counts only, never payloads.
 */
import { timingSafeEqual } from "node:crypto";
import { readConfig } from "./config.mjs";
import { getStore, withLock } from "./store.mjs";
import { makeGithub } from "./github.mjs";
import { verifySignature, keyed } from "./crypto.mjs";
import { isBot, isCore, CAPS, DAY_MS } from "./rules.mjs";
import {
  emptyState, applyEvent, creditedUser, startMeditation, stopMeditation, fold, prune, feedView,
  meditationState, reviewCandidates, backstopDue
} from "./engine.mjs";

const STATE = "egg:state", MAX_HOOK = 1 << 20, MAX_MED = 64 << 10, SEEN_MS = 48 * 3600e3;
const TAKEN = {
  issues: new Set(["opened", "closed", "labeled", "deleted", "transferred"]),
  pull_request: new Set(["opened", "closed", "edited", "labeled"]),
  pull_request_review: new Set(["submitted"]),
  release: new Set(["published"])
};

export function defaultDeps() {
  const cfg = readConfig();
  return { cfg, store: getStore(cfg), github: makeGithub(cfg), now: () => Date.now() };
}

function json(status, body, headers) {
  return new Response(body == null ? null : JSON.stringify(body), {
    status, headers: Object.assign({ "content-type": "application/json", "cache-control": "no-store" }, headers || {})
  });
}
async function count(store, name) { try { await store.incr("egg:count:" + name, 1, { px: 8 * DAY_MS }); } catch (e) { /* counters are best effort */ } }

export async function loadState(store) {
  const raw = await store.get(STATE);
  return raw ? Object.assign(emptyState(), JSON.parse(raw)) : emptyState();
}
async function mutate(deps, fn) {
  return withLock(deps.store, "state", async () => {
    const S = await loadState(deps.store);
    const out = await fn(S);
    await deps.store.set(STATE, JSON.stringify(S));
    return out;
  }, { clock: deps.now });
}

async function readBody(req, limit) {
  const len = +req.headers.get("content-length");
  if (len > limit) return null;
  const buf = Buffer.from(await req.arrayBuffer());
  return buf.length > limit ? null : buf;
}

/* ---------- POST /api/egg/webhook ---------- */
export async function handleWebhook(req, deps = defaultDeps()) {
  const { cfg, store } = deps;
  if (req.method !== "POST") return json(405, { error: "method" }, { allow: "POST" });
  if (!cfg.webhookSecret || !store) return json(503, { error: "not configured" });
  const raw = await readBody(req, MAX_HOOK);
  if (!raw) return json(413, { error: "too large" });
  if (!verifySignature(cfg.webhookSecret, raw, req.headers.get("x-hub-signature-256"))) {   // before any parsing
    await count(store, "hook-401");
    return json(401, { error: "signature" });
  }
  const delivery = req.headers.get("x-github-delivery") || "";
  if (!/^[\w-]{1,100}$/.test(delivery)) return json(400, { error: "delivery" });
  const dkey = "egg:dlv:" + keyed(cfg.hashKey, "dlv", delivery);
  if (!(await store.set(dkey, "1", { nx: true, px: SEEN_MS }))) return json(200, { ok: true, duplicate: true });
  try {
    let p;
    try { p = JSON.parse(raw.toString("utf8")); } catch (e) { return json(400, { error: "json" }); }
    const event = req.headers.get("x-github-event") || "";
    if (!p || !p.repository || String(p.repository.id) !== cfg.repoId) return json(403, { error: "repository" });
    if (event === "ping") return json(200, { ok: true });
    if (!TAKEN[event] || !TAKEN[event].has(p.action)) return json(204);
    const who = creditedUser(event, p);
    if (isBot(p.sender, cfg.botId) || (who && isBot(who, cfg.botId))) { await count(store, "hook-bot"); return json(200, { ok: true, ignored: true }); }
    let young = false;
    if (who && !isCore(who, cfg)) young = await isYoung(deps, who);
    const now = deps.now();
    const h = (kind, v) => keyed(cfg.hashKey, kind, v);
    await mutate(deps, (S) => applyEvent(S, { cfg, now, h, young }, event, p));
    return json(200, { ok: true });
  } catch (e) {
    await store.del(dkey).catch(() => {});                          // let GitHub's redelivery through
    return json(503, { error: "unavailable" });
  }
}

/* Accounts newer than 7 days earn no instant XP. Unknown age counts as new (fails closed). Cached 30 days. */
async function isYoung(deps, user) {
  const { cfg, store, github } = deps, key = "egg:age:" + keyed(cfg.hashKey, "acct", user.id);
  let created = await store.get(key);
  if (created == null) {
    created = await github.accountCreated(user.id).catch(() => null);
    if (created != null) await store.set(key, String(created), { px: 30 * DAY_MS });
  }
  return created == null || deps.now() - +created < CAPS.newAccountDays * DAY_MS;
}

/* ---------- GET /api/egg/feed ---------- */
export async function handleFeed(req, deps = defaultDeps()) {
  const { cfg, store } = deps;
  if (req.method !== "GET" && req.method !== "HEAD") return json(405, { error: "method" }, { allow: "GET" });
  if (!store) return json(503, { error: "unavailable" }, { "retry-after": "60" });
  try {
    const now = deps.now();
    const ip = (req.headers.get("x-forwarded-for") || req.headers.get("x-real-ip") || "").split(",")[0].trim();
    if (ip) {
      const minute = Math.floor(now / 60e3);
      const n = await store.incr("egg:rl:" + keyed(cfg.hashKey, "ip", ip) + ":" + minute, 1, { px: 61e3 });
      if (n > 30) return json(429, { error: "slow down" }, { "retry-after": String(60 - Math.floor((now % 60e3) / 1000)) });
    }
    let S = await loadState(store);
    if (meditationState(S, now) === "done") S = await mutate(deps, (T) => { fold(T, cfg, now); return T; });
    const since = new URL(req.url).searchParams.get("since");
    const body = feedView(S, cfg, now, since);
    return new Response(req.method === "HEAD" ? null : JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json", "cache-control": "public, s-maxage=5, stale-while-revalidate=15" }
    });
  } catch (e) {
    return json(503, { error: "unavailable" }, { "retry-after": "30" });
  }
}

/* ---------- POST /api/egg/meditate ---------- */
export async function handleMeditate(req, deps = defaultDeps()) {
  const { cfg, store, github } = deps;
  if (req.method !== "POST") return json(405, { error: "method" }, { allow: "POST" });
  if (!cfg.meditateSecret || !store) return json(503, { error: "not configured" });
  const raw = await readBody(req, MAX_MED);
  if (!raw) return json(413, { error: "too large" });
  if (!verifySignature(cfg.meditateSecret, raw, req.headers.get("x-egg-signature-256"))) return json(401, { error: "signature" });
  let b;
  try { b = JSON.parse(raw.toString("utf8")); } catch (e) { return json(400, { error: "json" }); }
  const now = deps.now();
  if (!b || typeof b.ts !== "number" || Math.abs(now / 1000 - b.ts) > 300) return json(401, { error: "stale" });
  if (typeof b.nonce !== "string" || !/^[\w-]{8,128}$/.test(b.nonce)) return json(401, { error: "nonce" });
  if (!(await store.set("egg:nonce:" + keyed(cfg.hashKey, "nonce", b.nonce), "1", { nx: true, px: SEEN_MS }))) return json(401, { error: "replay" });
  try {
    if (b.action === "start") {
      const S0 = await loadState(store);                             // read reactions outside the lock
      const st = meditationState(S0, now);
      if (st === "meditating" || st === "waking") return json(409, { error: "meditation running" });
      let rockets;
      try { rockets = await github.rocketReviews(reviewCandidates(S0, now).map((c) => c.node)); }
      catch (e) { return json(502, { error: "github" }); }
      const r = await mutate(deps, (S) => startMeditation(S, cfg, now, rockets));
      return json(r.status, r.body);
    }
    if (b.action === "stop") {
      const r = await mutate(deps, (S) => stopMeditation(S, cfg, now));
      return json(r.status, r.body);
    }
    return json(400, { error: "action" });
  } catch (e) {
    return json(503, { error: "unavailable" });
  }
}

/* ---------- GET /api/egg/settle (Vercel cron) ---------- */
export async function handleSettle(req, deps = defaultDeps()) {
  const { cfg, store, github } = deps;
  if (!cfg.cronSecret || !store) return json(503, { error: "not configured" });
  const want = Buffer.from("Bearer " + cfg.cronSecret), got = Buffer.from(req.headers.get("authorization") || "");
  if (got.length !== want.length || !timingSafeEqual(got, want)) return json(401, { error: "auth" });
  try {
    const now = deps.now();
    const S0 = await loadState(store);
    let rockets = null;
    if (backstopDue(S0, now)) rockets = await github.rocketReviews(reviewCandidates(S0, now).map((c) => c.node)).catch(() => new Set());
    const out = await mutate(deps, (S) => {
      const folded = fold(S, cfg, now);
      let backstop = 0;
      if (rockets && backstopDue(S, now)) backstop = startMeditation(S, cfg, now, rockets).body.total || 0;
      prune(S, now);
      return { folded, backstop };
    });
    return json(200, Object.assign({ ok: true }, out));
  } catch (e) {
    return json(503, { error: "unavailable" });
  }
}
