// The exosphere on the chat landing page (docs/BRAND.md "The exosphere"): Exo's evolving 3D graphic, here at form x0,
// the egg. It is the lattice engine from site/brand/egg.js (static/egg.js is a byte copy), not a separate drawing.
// The engine owns pausing off screen or in a hidden tab, the device pixel ratio cap, and one still frame under
// prefers-reduced-motion. This file adds the form table and a small mapping from what the client already sees
// (thinking, a tool running, waiting on approval, done) onto the engine's activity, pulse and, when the engine
// has them, signal and meditate.
import "./egg.js";   // defines window.ExoEgg

// The forms, in order. Later forms are still being named by the principal: add a row here and nothing else changes.
// `stage` is the engine stage that draws the form (ExoEgg.STAGES); `level` is its place in the line.
export const FORMS = [
  { id: "x0", level: 0, stage: 0, note: "the egg" },
  { id: "x", level: 1, stage: 2, note: "the lattice" },
  { id: "3xo", level: 2, stage: 3, note: "the fourth dimension unfolds" },
];
export const HERO_FORM = "x0";
export const formOf = (id) => FORMS.find((f) => f.id === id) || FORMS[0];
export const formLabel = (id) => { const f = formOf(id); return f.id + " · level " + f.level; };

// activity (0..1) and a one-off burst of packets for each thing the agent can be doing
const MOODS = {
  idle: { activity: 0.22 },
  thinking: { activity: 0.55, pulse: 3 },
  tool: { activity: 0.8, pulse: 4, signal: 1 },
  approval: { activity: 0.1 },   // holding still: it is waiting on you
  done: { activity: 0.35, pulse: 6, signal: 2 },
};

export function createHero(canvas, formId = HERO_FORM) {
  const form = formOf(formId);
  let egg = null, mood = "idle", meditating = false;

  function make() {
    const E = window.ExoEgg;
    if (egg || !E || !canvas.isConnected) return;
    egg = E.mount(canvas, {
      stage: form.stage, activity: MOODS.idle.activity, seed: 7, spin: 0.7,
      layout: (W, H) => ({ cx: W * 0.74, cy: H * 0.5, r: Math.min(W * 0.3, H * 0.42) }),
    });
    apply(mood, false);
    if (typeof ResizeObserver !== "undefined") new ResizeObserver(() => { if (egg) { egg.resize(); if (!egg.running) egg.reduce ? egg.still() : egg.draw(0); } }).observe(canvas);
  }
  function apply(next, burst) {
    const m = MOODS[next] || MOODS.idle;
    const wait = next === "approval";
    if (typeof egg.meditate === "function") {   // newer engines fold inward while waiting
      if (wait && !meditating) { egg.meditate(true); meditating = true; }
      else if (!wait && meditating) { egg.meditate(false); meditating = false; }
    }
    egg.setActivity(m.activity);
    if (burst) {
      if (typeof egg.signal === "function" && m.signal) egg.signal(m.signal);
      else if (m.pulse && typeof egg.pulse === "function") egg.pulse(m.pulse);
    }
  }
  // state: "idle" | "thinking" | "tool" | "approval" | "done". "done" is a moment: it settles back to idle.
  // `again` forces a burst for a repeat (a second tool starting).
  function react(state, again) {
    const changed = state !== mood;
    mood = MOODS[state] ? state : "idle";
    if (egg && (changed || again || state === "done")) apply(mood, true);
    if (mood === "done") { mood = "idle"; if (egg) setTimeout(() => { if (mood === "idle") egg.setActivity(MOODS.idle.activity); }, 2500); }
  }
  return {
    form,
    label: formLabel(form.id),
    react,
    start() { make(); if (egg) egg.resume(); },
    stop() { if (egg) egg.pause(); },
    still() { make(); if (egg) egg.reduce ? egg.still() : egg.draw(0); },
  };
}
