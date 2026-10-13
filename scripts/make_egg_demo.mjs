#!/usr/bin/env node
/* Build site/brand/egg-feed.demo.json by driving the real x0 functions (site/api/_lib/egg) with a memory store,
 * a fake clock and fixture webhooks, and recording each feed response (docs/LIVE-EGG.md section 1.1, the demo feed).
 * About 20 repository events across all five types, then a meditation of 8 validated items (3 pr, 3 issue,
 * 2 review), the meditating responses with done rising, one waking response, and the return to filling.
 *
 *   node scripts/make_egg_demo.mjs            writes the file
 *   node scripts/make_egg_demo.mjs --check    exits 1 if the file is missing or not v: 2
 */
import { writeFileSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { readConfig } from "../site/api/_lib/egg/config.mjs";
import { MemoryStore } from "../site/api/_lib/egg/store.mjs";
import { sign } from "../site/api/_lib/egg/crypto.mjs";
import { emptyState } from "../site/api/_lib/egg/engine.mjs";
import { handleWebhook, handleFeed, handleMeditate } from "../site/api/_lib/egg/http.mjs";

const OUT = fileURLToPath(new URL("../site/brand/egg-feed.demo.json", import.meta.url));
if (process.argv.includes("--check")) {
  const j = JSON.parse(readFileSync(OUT, "utf8"));
  process.exit(Array.isArray(j) && j.every((r) => r.v === 2) ? 0 : 1);
}

const clock = { t: Date.parse("2026-10-20T14:00:00Z") };
const cfg = readConfig({ EGG_WEBHOOK_SECRET: "demo", EGG_MEDITATE_SECRET: "demo", EGG_HASH_KEY: "demo", EGG_REPO_ID: "1",
  EGG_REPO: "demo/x0", EGG_CORE_ACCOUNTS: "1", EGG_REVIEWER_BOT_ID: "9", CRON_SECRET: "demo", EGG_STORE: "memory" });
const store = new MemoryStore(() => clock.t);
const rockets = new Set();
const deps = { cfg, store, now: () => clock.t,
  github: { rocketReviews: async (ids) => new Set(ids.filter((i) => rockets.has(i))), accountCreated: async () => 0 } };
const S = emptyState(); S.xp = 1178;                                     // a community egg about half full
await store.set("egg:state", JSON.stringify(S));

const repo = { id: 1 }, iso = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
let d = 0, uid = 100;
const u = () => ({ id: ++uid, login: "u" + uid, type: "User" });
async function hook(event, p) {
  const body = JSON.stringify(Object.assign({ repository: repo }, p));
  const r = await handleWebhook(new Request("https://demo/api/egg/webhook", { method: "POST", body,
    headers: { "x-github-event": event, "x-github-delivery": "demo-" + (++d), "x-hub-signature-256": sign("demo", body) } }), deps);
  if (r.status !== 200) throw new Error(event + " " + r.status);
}
const people = {};
const who = (n) => (people[n] = people[n] || u());
const issue = (n) => hook("issues", { action: "opened", sender: who("i" + n),
  issue: { number: n, user: who("i" + n), labels: [], body: "", title: "", created_at: iso(clock.t) } });
const prOpen = (n) => hook("pull_request", { action: "opened", sender: who("p" + n),
  pull_request: { number: n, user: who("p" + n), labels: [], body: "", title: "", created_at: iso(clock.t) } });
const merge = (n, body) => hook("pull_request", { action: "closed", sender: { id: 1, login: "core", type: "User" },
  pull_request: { number: n, user: who("p" + n), labels: [], body, title: "", merged: true, merged_at: iso(clock.t), created_at: iso(clock.t) } });
const review = (pr, id, rocket) => { if (rocket) rockets.add("R" + id);
  return hook("pull_request_review", { action: "submitted", sender: who("r" + id),
    review: { id, node_id: "R" + id, user: who("r" + id), body: "", submitted_at: iso(clock.t) },
    pull_request: { number: pr, user: who("p" + pr) } }); };
const release = () => hook("release", { action: "published", sender: { id: 1, login: "core", type: "User" },
  release: { tag_name: "exo-v0.9.0", published_at: iso(clock.t) } });

const out = [];
let cursor = null;
const emitted = new Set();
async function poll(step) {
  clock.t += step * 1000;
  const r = await handleFeed(new Request("https://demo/api/egg/feed" + (cursor ? "?since=" + cursor : "")), deps);
  const j = await r.json();
  cursor = j.cursor;
  j.events = j.events.filter((e) => !emitted.has(e.id));                // the feed overlaps by 2 s; the client dedupes
  j.events.forEach((e) => emitted.add(e.id));
  out.push(j);
}
const later = (s) => { clock.t += s * 1000; };

// the replay window: the morning's activity
await issue(1); later(1500); await prOpen(20); later(1800); await issue(2); later(1200); await review(20, 1, true);
later(2400); await issue(3); later(900);
await poll(0);
// live polls, a few events each
await prOpen(21); await poll(10);
await issue(4); await review(21, 2, false); await issue(9); await poll(10);
await merge(20, "Fixes #1"); await poll(10);
await issue(5); await prOpen(22); await poll(10);
await review(22, 3, true); await issue(6); await poll(10);
await merge(21, "Closes #2\nfixes #3"); await poll(10);
await release(); await issue(7); await poll(10);
await merge(22, "No refs"); await review(22, 4, false); await poll(10);
// the team sits down to review: x0 meditates
const raw = JSON.stringify({ action: "start", ts: Math.floor(clock.t / 1000), nonce: "demo-nonce-0001" });
const m = await handleMeditate(new Request("https://demo/api/egg/meditate", { method: "POST", body: raw,
  headers: { "x-egg-signature-256": sign("demo", raw) } }), deps);
const total = (await m.json()).total;
if (total !== 8) throw new Error("expected 8 validated items, got " + total);
await poll(1);
for (let i = 0; i < 14; i++) { if (i === 6) await issue(8); await poll(5); if (out.at(-1).egg.state !== "meditating") break; }
while (out.at(-1).egg.state === "meditating") await poll(5);
if (out.at(-1).egg.state !== "waking") await poll(5);
while (out.at(-1).egg.state === "waking") await poll(30);

for (const r of out) r.cursor = "demo" + out.indexOf(r);                // the demo ignores cursors
writeFileSync(OUT, JSON.stringify(out, null, 1) + "\n");
console.log("wrote", out.length, "responses:", out.map((r) => r.egg.state[0]).join(""));
