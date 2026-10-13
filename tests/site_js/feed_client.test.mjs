// The x0 feed client (site/brand/egg-feed.js): the v2 poll steps in their fixed order, replay, errors, 404s,
// visibility and the demo loop. A recording egg stands in for the engine.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { makeWindow, load } from "./harness.mjs";

function recorder() {
  const log = [];
  let med = false;
  return {
    log,
    setStage(s) { log.push(["stage", s]); },
    meditate(on, o) { log.push(["meditate", on, !!(o && o.immediate)]); med = on; },
    meditating() { return med; },
    event(t, o) { log.push(["event", t === "validated" ? "validated:" + o.kind : t, !!o.replay]); }
  };
}
function resp(state, events, extra) {
  return Object.assign({ v: 2, generated: "2026-10-20T15:00:00Z", cursor: "c" + Math.random().toString(36).slice(2, 6),
    poll_s: state === "meditating" || state === "waking" ? 5 : 10,
    egg: { xp: 1, pending: 0, target: 2048, stage: 0.4, state, meditation: null, crack_at: null, hatch_at: null },
    events }, extra || {});
}
let n = 0;
const ev = (type, kind) => Object.assign({ id: (++n).toString(16).padStart(8, "0"), t: "2026-10-20T15:00:00Z", type, level: 1, weight: 1 }, kind ? { kind } : {});

function setup(responses, opts = {}) {
  const win = makeWindow();
  const urls = [];
  win.fetch = async (u) => {
    urls.push(u);
    const r = responses.length > 1 ? responses.shift() : responses[0];
    if (typeof r === "number") return { ok: false, status: r, headers: { get: () => (r === 429 ? "40" : null) }, json: async () => ({}) };
    return { ok: true, status: 200, headers: { get: () => null }, json: async () => r };
  };
  load(win, "brand/egg-feed.js");
  const egg = recorder(), states = [];
  const feed = win.ExoEggFeed.connect(egg, Object.assign({ url: "/api/egg/feed", replay: true, onState: (e) => { states.push(e.state); egg.log.push(["state", e.state]); } }, opts));
  return { win, egg, feed, urls, states };
}

test("each poll goes stage, meditate, events, wake, onState, in that order", async () => {
  const { win, egg } = setup([
    resp("filling", [ev("issue")]),
    resp("meditating", [ev("pr"), ev("validated", "pr")]),
    resp("meditating", [ev("validated", "review")]),
    resp("filling", [ev("validated", "issue")])
  ]);
  await win.__advance(0);
  await win.__advance(10000);
  await win.__advance(5000);
  await win.__advance(5000);
  assert.deepEqual(egg.log.map((x) => x.join(":")), [
    "stage:0.4", "event:issue:true", "state:filling",
    "stage:0.4", "meditate:true:false", "event:pr:false", "event:validated:pr:false", "state:meditating",
    "stage:0.4", "event:validated:review:false", "state:meditating",
    "stage:0.4", "event:validated:issue:false", "meditate:false:false", "state:filling"
  ]);
});

test("the first poll mid-meditation enters at once and does not replay absorbs already done", async () => {
  const { win, egg } = setup([resp("meditating", [ev("issue"), ev("validated", "pr"), ev("validated", "issue")])]);
  await win.__advance(0);
  assert.deepEqual(egg.log.map((x) => x.join(":")), ["stage:0.4", "meditate:true:true", "event:issue:true", "state:meditating"]);
});

test("waking does not re-enter or wake early; the wake comes with filling", async () => {
  const { win, egg } = setup([resp("meditating", []), resp("waking", [ev("validated", "pr")]), resp("filling", [])]);
  await win.__advance(0); await win.__advance(5000);
  assert.equal(egg.meditating(), true);
  await win.__advance(5000);
  assert.equal(egg.meditating(), false);
  assert.deepEqual(egg.log.filter((x) => x[0] === "meditate").map((x) => x.join(":")), ["meditate:true:true", "meditate:false:false"]);
});

test("v above 2: the egg block applies, events are ignored; a missing v still plays; unknown fields are fine", async () => {
  const { win, egg } = setup([resp("filling", [], { v: 2 }), resp("filling", [ev("issue")], { v: 3, extra: 1 }),
    resp("filling", [ev("merge")], { v: undefined })]);
  await win.__advance(0); await win.__advance(10000); await win.__advance(10000);
  assert.deepEqual(egg.log.filter((x) => x[0] === "event").map((x) => x[1]), ["merge"]);
  assert.equal(egg.log.filter((x) => x[0] === "stage").length, 3);
});

test("duplicates are skipped and the cursor is passed back as since", async () => {
  const a = ev("issue");
  const { win, egg, urls } = setup([resp("filling", [a], { cursor: "cAAA" }), resp("filling", [a, ev("pr")], { cursor: "cBBB" })]);
  await win.__advance(0); await win.__advance(10000);
  assert.deepEqual(egg.log.filter((x) => x[0] === "event").map((x) => x[1]), ["issue", "pr"]);
  assert.equal(urls[1], "/api/egg/feed?since=cAAA");
});

test("errors back off (retry-after honoured), 404s stop the client after 3 in a row", async () => {
  const s = setup([429, resp("filling", [])]);
  await s.win.__advance(0);
  assert.equal(s.urls.length, 1);
  await s.win.__advance(39000);
  assert.equal(s.urls.length, 1);
  await s.win.__advance(1000);
  assert.equal(s.urls.length, 2);
  const g = setup([404]);
  await g.win.__advance(0); await g.win.__advance(20000); await g.win.__advance(40000); await g.win.__advance(600000);
  assert.equal(g.urls.length, 3);
  assert.equal(g.feed.gaveUp, true);
});

test("no polling while hidden; polls at once when visible again; feed.poll() polls now", async () => {
  const { win, urls, feed } = setup([resp("filling", [])]);
  await win.__advance(0);
  win.document.hidden = true;
  await win.__advance(60000);
  assert.equal(urls.length, 1);
  win.document.hidden = false; win.__fire("visibilitychange");
  await win.__advance(0);
  assert.equal(urls.length, 2);
  feed.poll(); await win.__advance(0);
  assert.equal(urls.length, 3);
});

test("demo mode plays the real demo file one response per poll, through a meditation, and loops", async () => {
  const list = JSON.parse(readFileSync(new URL("../../site/brand/egg-feed.demo.json", import.meta.url), "utf8"));
  const { win, egg, urls, states } = setup([list], { url: "/brand/egg-feed.demo.json", demo: true, pace: 0.5 });
  for (let i = 0; i < list.length + 3; i++) await win.__advance(5000);
  assert.equal(urls.length, 1);                                         // read once
  assert.ok(states.includes("meditating") && states.includes("waking"));
  const kinds = egg.log.filter((x) => x[0] === "event" && x[1].startsWith("validated")).length;
  assert.ok(kinds >= 8);
  assert.deepEqual(egg.log.filter((x) => x[0] === "meditate").slice(0, 2).map((x) => x[1]), [true, false]);
});
