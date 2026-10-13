// The x0 engine (site/brand/egg.js) under a stub canvas: the v2 event API, meditation, absorbs, reduced motion.
import { test } from "node:test";
import assert from "node:assert/strict";
import { makeWindow, load } from "./harness.mjs";

function mount(opts = {}, win = makeWindow(opts)) {
  load(win, "brand/egg.js");
  const egg = win.ExoEgg.mount(win.__canvas(), Object.assign({ stage: 0.5, seed: 3 }, opts));
  egg.pause();                                                         // frames are driven by hand
  const run = (s, dt = 1 / 60) => { for (let i = 0; i < s / dt; i++) egg.draw(dt); };
  return { win, egg, run };
}

test("ExoEgg.EVENTS matches the contract", () => {
  const { win } = mount();
  assert.deepEqual(JSON.parse(JSON.stringify(win.ExoEgg.EVENTS)), {
    issue: { level: 1 }, pr: { level: 2 }, review: { level: 2 }, merge: { level: 3 }, release: { level: 4 },
    validated: { issue: 1, pr: 2, review: 2 }
  });
});

test("every event type, unknown types and odd keys play without errors at every stage", () => {
  for (const stage of [0, 1, 2.2, 3]) {
    const { egg, run } = mount({ stage });
    for (const t of ["issue", "pr", "review", "merge", "release", "nope", "__proto__", "constructor"]) egg.event(t, { weight: 1 });
    for (const k of ["issue", "pr", "review", "odd"]) egg.event("validated", { kind: k });
    egg.signal(4); egg.signal(-3);
    run(20);
    assert.equal(egg.queued(), 0);
  }
});

test("the repository queue folds past 20, keeping the highest level", () => {
  const { egg } = mount();
  for (let i = 0; i < 21; i++) egg.event(i === 7 ? "merge" : "issue");
  assert.equal(egg.q.length, 1);
  assert.equal(egg.q[0].lv, 3);
  assert.equal(egg.q[0].type, "merge");
});

test("meditation: enter folds and calms, breath runs, absorbs land with onArrive, wake waits for them", () => {
  const { egg, run } = mount({ activity: 0.6 });
  assert.equal(egg.meditating(), false);
  egg.meditate(true);
  assert.equal(egg.meditating(), true);
  run(3.2);
  assert.ok(egg.fold > 0.85 && egg.calm === 1);
  assert.ok(Math.abs(egg.activity - 0.15) < 0.1);
  let arrived = 0;
  for (const k of ["pr", "issue", "review"]) egg.event("validated", { kind: k, onArrive: () => arrived++ });
  egg.meditate(true);                                                   // already meditating: no change
  egg.meditate(false);                                                  // queued wake: absorbs first
  run(4);
  assert.equal(egg.meditating(), true);
  run(16);
  assert.equal(arrived, 3);
  run(4);
  assert.equal(egg.meditating(), false);
  assert.equal(egg.fold, 0);
  assert.ok(Math.abs(egg.activity - 0.6) < 1e-9);                       // activity restored
});

test("no ambient packets while meditating; repository events still play there without a bloom", () => {
  const { egg, run } = mount({ stage: 2, activity: 1 });
  egg.meditate(true, { immediate: true });
  assert.equal(egg.fold, 1);
  egg.packets = [];
  run(5);
  assert.equal(egg.packets.length, 0);
  egg.event("merge");
  run(0.1);
  assert.equal(egg.bloom < 0.05, true);
  assert.ok(egg.packets.length > 0 && egg.packets.length <= 6);
});

test("absorbs outside meditation play alone, without the fold", () => {
  const { egg, run } = mount({ stage: 2 });
  let n = 0;
  egg.event("validated", { kind: "pr", replay: true, onArrive: () => n++ });
  run(3);
  assert.equal(n, 1);
  assert.equal(egg.meditating(), false);
  assert.equal(egg.fold, 0);
});

test("past 40 waiting absorbs, extras fold into the last glow and still report arrival", () => {
  const { egg, run } = mount({ stage: 2 });
  egg.meditate(true, { immediate: true });
  let n = 0;
  for (let i = 0; i < 45; i++) egg.event("validated", { kind: "issue", onArrive: () => n++ });
  assert.equal(egg.aq.length, 40);
  run(40 * 6 + 10, 1 / 20);
  assert.equal(n, 45);
});

test("a release lifts the stage by 0.15 and returns", () => {
  const { egg, run } = mount({ stage: 1 });
  egg.event("release");
  run(0.6);
  assert.ok(Math.abs(egg.target - 1.15) < 1e-9);
  run(3);
  assert.equal(egg.target, 1);
});

test("reduced motion: events are only counted; meditate swaps stills; onArrive runs at once", () => {
  const { egg } = mount({ reduce: true });
  egg.event("issue"); egg.event("merge");
  assert.equal(egg.queued(), 0);
  assert.equal(egg.counts.issue, 1);
  let n = 0;
  egg.event("validated", { kind: "pr", onArrive: () => n++ });
  assert.equal(n, 1);
  egg.meditate(true);
  assert.equal(egg.meditating(), true);
  assert.equal(egg.fold, 1);
  egg.setStage(1.2);
  assert.equal(egg.fold, 1);
  egg.meditate(false);
  assert.equal(egg.meditating(), false);
  assert.equal(egg.fold, 0);
});
