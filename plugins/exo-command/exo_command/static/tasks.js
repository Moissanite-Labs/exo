// Tasks view: tasks.list once, then task.updated events keep it current.
// Drawn like the site's roadmap grid (D-061): a status tally, then one hairline lane per status.
import { h, api, short, ago, errorBox, pageHead, readout, pill, emptyState } from "./dom.js";
import * as bus from "./bus.js";
import "./egg.js";   // defines window.ExoEgg (copied from site/brand/egg.js); used once, in the empty state

const ORDER = ["running", "queued", "failed", "done", "cancelled"];
// status word -> the pill state, whose led form carries it without colour
const FORM = { running: "run", queued: "queue", failed: "fail", done: "done", cancelled: "stop" };

export function mount(root) {
  const tasks = new Map(), notes = new Map(), drafts = new Map();   // notes: task id -> what the last steer or stop said (#97)
  let err = "", loaded = false, egg = null, eggEl = null;
  const body = h("div", { class: "xc-tasks" });
  const counts = h("div", { class: "xc-phead-read" });
  root.replaceChildren(h("div", { class: "xc-pg" }, pageHead({ eyebrow: "02 / Tasks", title: "Tasks",
    lede: "Work the agents handed off, by status. It updates as each task moves.", aside: counts }), body));

  // The empty state's mark: the egg at stage 0, a closed shell. Made once and kept, so redraws do not restart it.
  function mark() {
    if (eggEl) return eggEl;
    const cv = h("canvas", { class: "xc-egg", "aria-hidden": "true" });
    eggEl = h("div", { class: "xc-void-mark" }, cv);
    requestAnimationFrame(() => {
      if (!egg && window.ExoEgg && cv.isConnected) egg = window.ExoEgg.mount(cv, { stage: 0, activity: 0.25, seed: 7, spin: 0.6 });
    });
    return eggEl;
  }

  // A child session can be opened in Chat; a running one can be steered or stopped. The core allows the principal only
  // and says why when it refuses (a finished child, a task that is not this page's to steer).
  async function control(t, verb, text) {
    try {
      await api("POST", "/api/tasks/" + encodeURIComponent(t.task_id) + "/" + verb, verb === "steer" ? { text } : {});
      notes.set(t.task_id, verb === "steer" ? "note sent" : "stop requested");
    } catch (e) { notes.set(t.task_id, e.message); }
    draw();
  }
  function controls(t) {
    if (!t.child_session) return null;
    const box = h("input", { class: "xc-input", type: "text", "aria-label": "Note to " + (t.title || t.task_id), placeholder: "note for this subagent", value: drafts.get(t.task_id) || "" });
    box.addEventListener("input", () => drafts.set(t.task_id, box.value));   // a redraw on the next event keeps the draft
    const steer = () => { const v = box.value.trim(); if (v) { drafts.delete(t.task_id); control(t, "steer", v); } };
    box.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); steer(); } });
    return h("div", { class: "xc-tcard-ctl" },
      h("button", { class: "xc-btn", onclick: () => { sessionStorage.setItem("xc-open", t.child_session); location.hash = "#chat"; } }, "Open"),
      t.status === "running" ? [box, h("button", { class: "xc-btn", onclick: steer }, "Steer"),
        h("button", { class: "xc-btn", onclick: () => control(t, "stop") }, "Stop")] : null,
      notes.has(t.task_id) ? h("span", { class: "xc-dim", role: "status" }, notes.get(t.task_id)) : null);
  }

  function draw() {
    const by = {};
    for (const t of tasks.values()) (by[t.status || "queued"] = by[t.status || "queued"] || []).push(t);
    const cols = ORDER.concat(Object.keys(by).filter((k) => !ORDER.includes(k))).filter((k) => by[k]);
    const n = (k) => (by[k] || []).length;
    const total = tasks.size;
    counts.replaceChildren(readout([["Open", String(n("running") + n("queued")), n("running") ? "run" : "off"],
      ["Failed", String(n("failed")), n("failed") ? "fail" : "off"], ["Done", String(n("done")), n("done") ? "done" : "off"]]));
    const kids = [];
    if (err) kids.push(errorBox(err));
    if (cols.length) {
      kids.push(h("div", { class: "xc-tally" },
        h("div", { class: "xc-tally-bar", role: "img", "aria-label": cols.map((k) => n(k) + " " + k).join(", ") },
          cols.map((k) => h("span", { "data-s": FORM[k] || "off", style: "flex-grow:" + n(k) }))),
        h("ul", { class: "xc-tally-key" }, cols.map((k) => h("li", null, pill(k, FORM[k]), h("b", null, String(n(k))))),
          h("li", { class: "xc-tally-total" }, "total ", h("b", null, String(total))))));
      kids.push(h("div", { class: "xc-board", style: "--n:" + cols.length }, cols.map((k) => h("section", { class: "xc-lane2 xc-ks-" + k, "aria-label": k + " tasks" },
        h("header", { class: "xc-lane2-head" }, pill(k, FORM[k]), h("span", { class: "xc-n" }, String(n(k)))),
        h("div", { class: "xc-lane2-body" }, by[k].map((t) => h("article", { class: "xc-tcard", "data-s": FORM[k] || "off" },
          h("h3", { title: t.title || "" }, short(t.title || t.task_id, 120)),
          t.detail ? h("p", { class: "xc-tcard-detail" }, short(t.detail, 200)) : null,
          h("footer", { class: "xc-tcard-foot" }, h("code", null, short(t.task_id, 24)),
            t.updated_at || t.finished_at ? h("span", null, ago(t.finished_at || t.updated_at)) : null), controls(t))))))));
    } else if (!loaded) {
      kids.push(h("div", { class: "xc-loading" }, "Reading tasks..."));
    } else if (!err) {
      kids.push(emptyState({ mark: mark(), k: "No tasks yet",
        title: "Delegated work lands here.",
        body: "When an agent hands work to a subagent, or a scheduled job runs, the task appears in a lane: queued, running, then done or failed.",
        hint: h("span", null, "Ask for something that runs in the background in ", h("a", { href: "#chat" }, "Chat"),
          ", or put a prompt on a clock in ", h("a", { href: "#schedule" }, "Schedule"), ".") }));
    }
    body.replaceChildren(...kids);
  }

  function merge(t) {
    const id = t.task_id || t.id;
    if (!id) return;
    const cur = tasks.get(id) || {};
    const next = { ...cur };
    for (const k in t) if (t[k] !== "" && t[k] != null) next[k] = t[k];
    next.task_id = id;
    tasks.set(id, next);
  }

  const off = bus.on((ev) => { if (ev.type === "task.updated") { merge(ev.body || {}); draw(); } });
  api("GET", "/api/tasks").then((d) => { loaded = true; (d.tasks || []).forEach(merge); draw(); })
    .catch((e) => { loaded = true; err = e.status === 501 ? "The core does not list tasks yet. Updates still appear here." : e.message; draw(); });
  draw();
  return () => { off(); if (egg) egg.destroy(); };
}
