// Tasks view: tasks.list once, then task.updated events keep it current.
import { h, api, short, empty, errorBox } from "./dom.js";
import * as bus from "./bus.js";

const ORDER = ["running", "queued", "failed", "done", "cancelled"];

export function mount(root) {
  const tasks = new Map();
  let err = "";
  const body = h("div", { class: "xc-tasks" });
  root.replaceChildren(h("section", { class: "xc-panel" }, h("header", { class: "xc-panel-head" }, h("h2", null, "Tasks"),
    h("span", { class: "xc-meta" }, "from tasks.list and task.updated")), body));

  function draw() {
    const by = {};
    for (const t of tasks.values()) (by[t.status || "queued"] = by[t.status || "queued"] || []).push(t);
    const cols = ORDER.concat(Object.keys(by).filter((k) => !ORDER.includes(k))).filter((k) => by[k]);
    body.replaceChildren(err ? errorBox(err) : null,
      ...(cols.length ? [h("div", { class: "xc-lanes" }, cols.map((k) => h("div", { class: "xc-lane xc-ks-" + k },
        h("div", { class: "xc-lane-head" }, h("span", null, k), h("span", { class: "xc-n" }, by[k].length)),
        by[k].map((t) => h("article", { class: "xc-card xc-task" }, h("h3", null, short(t.title || t.task_id, 120)),
          t.detail ? h("div", { class: "xc-dim" }, short(t.detail, 200)) : null,
          h("code", null, short(t.task_id, 24)))))))] : [empty("No tasks yet.")]));
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
  api("GET", "/api/tasks").then((d) => { (d.tasks || []).forEach(merge); draw(); })
    .catch((e) => { err = e.status === 501 ? "The core does not list tasks yet. Updates still appear here." : e.message; draw(); });
  draw();
  return off;
}
