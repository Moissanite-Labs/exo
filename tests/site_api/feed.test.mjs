// The feed's shape against docs/LIVE-EGG.md section 1.1, cursors, privacy, rate limit, the meditate endpoint,
// a meditation end to end, the settle cron, the store adapters and the demo feed.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { handleWebhook, handleFeed, handleMeditate, handleSettle } from "../../site/api/_lib/egg/http.mjs";
import { MemoryStore, UpstashStore } from "../../site/api/_lib/egg/store.mjs";
import { sign } from "../../site/api/_lib/egg/crypto.mjs";
import { makeDeps, hookReq, medReq, feedReq, issue, pr, review, release, user, state, feed, CORE, T0 } from "./helpers.mjs";

const send = (d, ev, p) => handleWebhook(hookReq(d, ev, p), d);
const alice = user(501, "alice-secret-login"), bob = user(502, "bob-secret-login");
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;
const TYPES = ["issue", "pr", "review", "merge", "release", "validated"];
const STATES = ["filling", "meditating", "waking", "cracking", "hatched"];
const LEVELS = { issue: 1, pr: 2, review: 2, merge: 3, release: 4 };
const WEIGHTS = { issue: 1, pr: 1, review: 1, merge: 2, release: 25 };
const VWEIGHT = { issue: 4, pr: 10, review: 4 }, VLEVEL = { issue: 1, pr: 2, review: 2 };

/* The v: 2 contract, checked field by field. Exported shape only; nothing extra. */
export function checkFeed(f) {
  assert.deepEqual(Object.keys(f).sort(), ["cursor", "egg", "events", "generated", "poll_s", "v"]);
  assert.equal(f.v, 2);
  assert.match(f.generated, ISO);
  assert.equal(typeof f.cursor, "string");
  assert.ok(Number.isInteger(f.poll_s) && f.poll_s >= 5 && f.poll_s <= 300);
  const e = f.egg;
  assert.deepEqual(Object.keys(e).sort(), ["crack_at", "hatch_at", "meditation", "pending", "stage", "state", "target", "xp"]);
  for (const k of ["xp", "pending", "target"]) assert.ok(Number.isInteger(e[k]) && e[k] >= 0, k);
  assert.ok(typeof e.stage === "number" && e.stage >= 0 && e.stage <= 4);
  assert.ok(STATES.includes(e.state));
  if (e.state === "meditating" || e.state === "waking") {
    const m = e.meditation;
    assert.deepEqual(Object.keys(m).sort(), ["done", "ends", "gained", "progress", "started", "total"]);
    assert.match(m.started, ISO); assert.match(m.ends, ISO);
    assert.ok(m.total >= 1 && m.done >= 0 && m.done <= m.total);
    assert.equal(m.progress, Math.round((m.done / m.total) * 1000) / 1000);
    assert.ok(Number.isInteger(m.gained));
    assert.equal(f.poll_s, 5);
  } else {
    assert.equal(e.meditation, null);
  }
  for (const k of ["crack_at", "hatch_at"]) assert.ok(e[k] === null || ISO.test(e[k]));
  assert.ok(Array.isArray(f.events) && f.events.length <= 50);
  const ids = new Set();
  let last = "";
  for (const ev of f.events) {
    assert.ok(TYPES.includes(ev.type));
    assert.deepEqual(Object.keys(ev).sort(), ev.type === "validated"
      ? ["id", "kind", "level", "t", "type", "weight"] : ["id", "level", "t", "type", "weight"]);
    assert.match(ev.id, /^[0-9a-f]{8}$/);
    assert.ok(!ids.has(ev.id)); ids.add(ev.id);
    assert.match(ev.t, ISO);
    if (ev.type === "validated") { assert.equal(ev.level, VLEVEL[ev.kind]); assert.equal(ev.weight, VWEIGHT[ev.kind]); }
    else { assert.equal(ev.level, LEVELS[ev.type]); assert.equal(ev.weight, WEIGHTS[ev.type]); }
    last = ev.t;
  }
  return f;
}

test("the feed matches the v: 2 contract and its headers", async () => {
  const d = makeDeps();
  await send(d, "issues", issue("opened", 1, alice));
  await send(d, "pull_request", pr("opened", 2, bob));
  await send(d, "pull_request_review", review(2, bob, alice, 5));
  await send(d, "pull_request", pr("closed", 2, bob, { merged: true }));
  await send(d, "release", release("exo-v1.0.0"));
  const r = await handleFeed(feedReq(), d);
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("content-type"), "application/json");
  assert.equal(r.headers.get("cache-control"), "public, s-maxage=5, stale-while-revalidate=15");
  const f = checkFeed(await r.json());
  assert.deepEqual(f.events.map((e) => e.type), ["issue", "pr", "review", "merge", "release"]);
  assert.equal(f.egg.state, "filling");
  assert.equal(f.poll_s, 10);
  assert.equal(f.egg.xp, 1 + 1 + 1 + 2 + 25);
  assert.equal(f.egg.pending, 10);
  assert.equal(f.egg.stage, Math.round(0.8 * 30 / 2048 * 1000) / 1000);
});

test("privacy: no titles, bodies, numbers, logins or ids in the feed; the private store holds hashes", async () => {
  const d = makeDeps();
  await send(d, "issues", issue("opened", 4711, alice, { title: "Leaky title zebra", body: "secret body quokka" }));
  await send(d, "pull_request", pr("closed", 4712, bob, { merged: true, title: "PR title walrus", body: "Fixes #4711" }));
  const txt = JSON.stringify(await feed(d, handleFeed));
  for (const s of ["zebra", "quokka", "walrus", "4711", "4712", "alice", "bob", "501", "502", "secret"]) assert.ok(!txt.includes(s), s);
  const raw = await d.store.get("egg:state");
  for (const s of ["zebra", "quokka", "walrus", "alice", "bob", "Fixes"]) assert.ok(!raw.includes(s), s);
  assert.ok(!/"4711"|:4711\b/.test(raw));
});

test("since: a cursor returns only newer events; a bad cursor returns the 24-hour replay window, at most 50", async () => {
  const d = makeDeps();
  await send(d, "issues", issue("opened", 1, alice));
  d.clock.t += 5e3;
  const a = await feed(d, handleFeed);
  assert.equal(a.events.length, 1);
  d.clock.t += 5e3;
  await send(d, "issues", issue("opened", 2, bob));
  d.clock.t += 3e3;
  const b = await feed(d, handleFeed, a.cursor);
  assert.equal(b.events.length, 1);
  assert.notEqual(b.events[0].id, a.events[0].id);
  d.clock.t += 5e3;
  const c = await feed(d, handleFeed, b.cursor);
  assert.equal(c.events.length, 0);
  for (let n = 10; n < 70; n++) await send(d, "issues", issue("opened", n, user(3000 + n)));
  const r = await feed(d, handleFeed, "../nonsense");
  assert.equal(r.events.length, 50);
  d.clock.t += 25 * 3600e3;
  const old = await feed(d, handleFeed);
  assert.equal(old.events.length, 0);                                    // older than 24 hours: not replayed
});

test("a cursor that is far behind pages through at most 50 at a time", async () => {
  const d = makeDeps();
  const a = await feed(d, handleFeed);
  d.clock.t += 1000;
  for (let n = 1; n <= 70; n++) { d.clock.t += 10; await send(d, "issues", issue("opened", n, user(4000 + n))); }
  d.clock.t += 5e3;
  const b = await feed(d, handleFeed, a.cursor);
  assert.equal(b.events.length, 50);
  const c = await feed(d, handleFeed, b.cursor);
  assert.equal(c.events.length, 20);
});

test("429 with retry-after past 30 requests a minute from one address; 503 without a store", async () => {
  const d = makeDeps();
  for (let i = 0; i < 30; i++) assert.equal((await handleFeed(feedReq(null, "203.0.113.9"), d)).status, 200);
  const r = await handleFeed(feedReq(null, "203.0.113.9"), d);
  assert.equal(r.status, 429);
  assert.ok(+r.headers.get("retry-after") > 0);
  assert.equal((await handleFeed(feedReq(null, "203.0.113.10"), d)).status, 200);
  const none = makeDeps({ store: null });
  assert.equal((await handleFeed(feedReq(), none)).status, 503);
});

test("meditate: bad signature, stale ts, reused nonce and unknown action are refused", async () => {
  const d = makeDeps();
  assert.equal((await handleMeditate(medReq(d, { action: "start" }, { secret: "wrong" }), d)).status, 401);
  assert.equal((await handleMeditate(medReq(d, { action: "start", ts: Math.floor(T0 / 1000) - 301 }), d)).status, 401);
  const raw = JSON.stringify({ action: "start", ts: Math.floor(T0 / 1000), nonce: "fixed-nonce-1" });
  const mk = () => new Request("https://site.test/api/egg/meditate", { method: "POST", body: raw,
    headers: { "x-egg-signature-256": sign(d.cfg.meditateSecret, raw) } });
  assert.equal((await handleMeditate(mk(), d)).status, 200);
  assert.equal((await handleMeditate(mk(), d)).status, 401);
  assert.equal((await handleMeditate(medReq(d, { action: "dance" }), d)).status, 400);
});

test("meditate with an empty queue: total 0, x0 does not meditate on nothing", async () => {
  const d = makeDeps();
  const r = await handleMeditate(medReq(d, { action: "start" }), d);
  assert.deepEqual(await r.json(), { total: 0 });
  assert.equal((await feed(d, handleFeed)).egg.state, "filling");
});

test("a meditation end to end: events, start, absorbs, waking, fold, unlinkable ids", async () => {
  const d = makeDeps();
  // outside work: 3 issues, 2 PRs (one merged fixing two issues, one salvaged), 2 reviews (one with ion's rocket)
  await send(d, "issues", issue("opened", 1, alice));
  await send(d, "issues", issue("opened", 2, bob));
  await send(d, "issues", issue("opened", 3, user(503)));
  await send(d, "pull_request", pr("opened", 4, bob));
  d.clock.t += 2 * 3600e3;
  await send(d, "pull_request", pr("closed", 4, bob, { sender: CORE }));
  await send(d, "pull_request_review", review(5, CORE, alice, 51));
  await send(d, "pull_request_review", review(6, CORE, bob, 52));
  await send(d, "pull_request", pr("closed", 7, alice, { merged: true, mergedAt: d.clock.t, body: "Fixes #1, closes #2\nSalvaged-from: #4" }));
  d.rockets.add("PRR_51");
  const before = checkFeed(await feed(d, handleFeed));
  const instant = before.egg.xp;
  assert.equal(before.egg.pending, 10 + 4 + 4 + 10);
  const repoIds = new Set(before.events.map((e) => e.id));

  const start = await handleMeditate(medReq(d, { action: "start" }), d);
  assert.deepEqual(await start.json(), { total: 5 });                    // pr 7, issues 1 and 2, pr 4, review 51
  assert.equal(d.calls.rockets, 1);
  assert.equal((await handleMeditate(medReq(d, { action: "start" }), d)).status, 409);

  let f = checkFeed(await feed(d, handleFeed));
  assert.equal(f.egg.state, "meditating");
  assert.equal(f.egg.meditation.total, 5);
  assert.equal(f.egg.meditation.done, 0);
  assert.equal(f.egg.pending, 0);
  // the doc's schedule: first absorb after the 6 s enter phase, then one every clamp(900 / 5, 3, 8) = 8 s
  assert.equal(Date.parse(f.egg.meditation.ends) - Date.parse(f.egg.meditation.started), 6000 + 4 * 8000);
  let cursor = f.cursor, absorbed = [];
  for (let s = 0; s < 40; s += 5) {
    d.clock.t += 5000;
    f = checkFeed(await feed(d, handleFeed, cursor));
    cursor = f.cursor;
    absorbed.push(...f.events.filter((e) => e.type === "validated" && !absorbed.some((a) => a.id === e.id)));
    if (f.egg.state === "meditating") {
      assert.equal(f.egg.xp, instant + f.egg.meditation.gained);
      assert.equal(f.egg.meditation.done, absorbed.length);
    }
  }
  assert.equal(absorbed.length, 5);
  assert.deepEqual(absorbed.map((e) => e.kind).sort(), ["issue", "issue", "pr", "pr", "review"]);
  for (const a of absorbed) assert.ok(!repoIds.has(a.id));
  d.clock.t += 10e3;
  f = checkFeed(await feed(d, handleFeed, cursor));
  assert.equal(f.egg.state, "waking");
  assert.equal(f.egg.meditation.done, 5);
  assert.equal(f.egg.meditation.gained, 32);
  d.clock.t += 31e3;
  f = checkFeed(await feed(d, handleFeed, f.cursor));
  assert.equal(f.egg.state, "filling");
  assert.equal(f.egg.meditation, null);
  assert.equal(f.egg.xp, instant + 32);
  const S = await state(d);
  assert.equal(S.meditation, null);
  assert.equal(S.xp, instant + 32);                                      // folded in one write
});

test("stop: absorbed items stay counted, the rest return to the queue", async () => {
  const d = makeDeps();
  for (let n = 1; n <= 4; n++) await send(d, "pull_request", pr("closed", n, user(700 + n), { merged: true }));
  const instant = (await state(d)).xp;
  await handleMeditate(medReq(d, { action: "start" }), d);
  d.clock.t += 6000 + 8000 + 1;                                          // two absorbed
  const r = await handleMeditate(medReq(d, { action: "stop" }), d);
  assert.deepEqual(await r.json(), { absorbed: 2, returned: 2 });
  let f = checkFeed(await feed(d, handleFeed));
  assert.equal(f.egg.state, "waking");
  assert.equal(f.egg.pending, 20);
  assert.equal(f.events.filter((e) => e.type === "validated").length, 2);
  d.clock.t += 31e3;
  f = await feed(d, handleFeed);
  assert.equal(f.egg.xp, instant + 20);
  assert.equal((await handleMeditate(medReq(d, { action: "stop" }), d)).status, 409);
});

test("crossing the target during a meditation: x0 wakes, then cracks for 24 hours, then hatches", async () => {
  const d = makeDeps({ env: { EGG_TARGET: "40" } });
  for (let n = 1; n <= 4; n++) await send(d, "pull_request", pr("closed", n, user(800 + n), { merged: true }));
  await handleMeditate(medReq(d, { action: "start" }), d);
  d.clock.t += 6000 + 3 * 8000 + 1;
  let f = await feed(d, handleFeed);
  assert.equal(f.egg.state, "waking");
  d.clock.t += 30e3;
  f = checkFeed(await feed(d, handleFeed));
  assert.equal(f.egg.state, "cracking");
  assert.ok(f.egg.crack_at && f.egg.hatch_at);
  assert.equal(f.egg.stage, 0.8);
  d.clock.t += 12 * 3600e3;
  assert.equal((await feed(d, handleFeed)).egg.stage, 0.875);
  d.clock.t += 13 * 3600e3;
  f = await feed(d, handleFeed);
  assert.equal(f.egg.state, "hatched");
  assert.equal(f.egg.stage, 1);
});

test("settle: needs the cron secret, prunes old rows, starts the 14-day backstop", async () => {
  const d = makeDeps();
  const auth = (s) => new Request("https://site.test/api/egg/settle", { headers: { authorization: "Bearer " + s } });
  assert.equal((await handleSettle(auth("nope"), d)).status, 401);
  await send(d, "issues", issue("opened", 1, alice));
  await send(d, "pull_request", pr("closed", 2, bob, { merged: true }));
  d.clock.t += 9 * 864e5;
  let r = await (await handleSettle(auth(d.cfg.cronSecret), d)).json();
  assert.equal(r.backstop, 0);
  let S = await state(d);
  assert.equal(Object.keys(S.items).length, 1);                          // the open issue stays; the merged PR row goes
  assert.equal(S.events.length, 0);                                      // older than 7 days
  d.clock.t += 5 * 864e5;
  r = await (await handleSettle(auth(d.cfg.cronSecret), d)).json();
  assert.equal(r.backstop, 1);
  assert.equal((await feed(d, handleFeed)).egg.state, "meditating");
});

test("the memory store: nx, expiry, incr", async () => {
  let t = 0;
  const s = new MemoryStore(() => t);
  assert.equal(await s.set("k", "a", { nx: true, px: 100 }), true);
  assert.equal(await s.set("k", "b", { nx: true }), false);
  assert.equal(await s.get("k"), "a");
  t = 101;
  assert.equal(await s.get("k"), null);
  assert.equal(await s.incr("n", 2, { px: 50 }), 2);
  assert.equal(await s.incr("n", 3), 5);
  t = 152;
  assert.equal(await s.get("n"), null);
});

test("the Upstash adapter speaks the REST command protocol", async () => {
  const sent = [];
  const fake = async (url, init) => {
    const cmd = JSON.parse(init.body); sent.push({ url, auth: init.headers.authorization, cmd });
    const result = cmd[0] === "SET" ? "OK" : cmd[0] === "INCRBY" ? 1 : cmd[0] === "GET" ? "v" : 1;
    return new Response(JSON.stringify({ result }), { status: 200 });
  };
  const s = new UpstashStore("https://kv.example/", "tok", fake);
  assert.equal(await s.set("k", "v", { nx: true, px: 1000 }), true);
  assert.equal(await s.get("k"), "v");
  assert.equal(await s.incr("n", 1, { px: 60000 }), 1);
  await s.del("k");
  assert.deepEqual(sent.map((x) => x.cmd), [["SET", "k", "v", "NX", "PX", "1000"], ["GET", "k"], ["INCRBY", "n", "1"], ["PEXPIRE", "n", "60000"], ["DEL", "k"]]);
  assert.equal(sent[0].url, "https://kv.example");
  assert.equal(sent[0].auth, "Bearer tok");
});

test("the demo feed is v: 2 responses with a full meditation cycle", () => {
  const url = new URL("../../site/brand/egg-feed.demo.json", import.meta.url);
  const list = JSON.parse(readFileSync(url, "utf8"));
  assert.ok(Array.isArray(list) && list.length > 5);
  list.forEach(checkFeed);
  const states = list.map((r) => r.egg.state);
  const types = new Set(list.flatMap((r) => r.events.map((e) => e.type)));
  for (const t of ["issue", "pr", "review", "merge", "release", "validated"]) assert.ok(types.has(t), t);
  const validated = list.flatMap((r) => r.events.filter((e) => e.type === "validated").map((e) => e.kind)).sort();
  assert.deepEqual(validated, ["issue", "issue", "issue", "pr", "pr", "pr", "review", "review"]);
  const repo = list.flatMap((r) => r.events.filter((e) => e.type !== "validated"));
  assert.ok(repo.length >= 18 && repo.length <= 24);
  const iMed = states.indexOf("meditating"), iWake = states.indexOf("waking");
  assert.ok(iMed > 0 && iWake > iMed && states.lastIndexOf("filling") > iWake);
  const done = list.filter((r) => r.egg.state === "meditating").map((r) => r.egg.meditation.done);
  assert.deepEqual(done, [...done].sort((a, b) => a - b));
  const ids = list.flatMap((r) => r.events.map((e) => e.id));
  assert.equal(new Set(ids).size, ids.length);
});
