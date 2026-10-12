// Schedule view: list, add, pause, resume, run now and remove jobs through schedule.edit.
import { h, api, short, empty, errorBox, tag } from "./dom.js";

export function mount(root) {
  let jobs = [], err = "", msg = "";
  const list = h("div", { class: "xc-stack" });
  const f = {
    title: h("input", { class: "xc-field", placeholder: "Title (optional)", "aria-label": "Title" }),
    when: h("input", { class: "xc-field", placeholder: "0 9 * * *   or   every 30m", "aria-label": "When" }),
    tier: h("select", { class: "xc-field", "aria-label": "Tier" }, ["fast", "standard", "deep"].map((t) => h("option", { value: t }, t))),
    prompt: h("textarea", { class: "xc-input", rows: "3", placeholder: "What should it do each time?", "aria-label": "Prompt" }),
  };
  const form = h("form", { class: "xc-card xc-sched-form", onsubmit: (e) => { e.preventDefault(); add(); } },
    h("h3", null, "Add a job"), h("div", { class: "xc-row" }, f.title, f.when, f.tier), f.prompt,
    h("div", { class: "xc-btnrow" }, h("button", { class: "xc-btn", type: "submit" }, "Add job")));
  const note = h("div");
  root.replaceChildren(h("section", { class: "xc-panel" }, h("header", { class: "xc-panel-head" }, h("h2", null, "Schedule"),
    h("span", { class: "xc-meta" }, "each run is a fresh session; results appear in Tasks")), note, form, list));

  const jid = (j) => j.job_id || j.id;
  function draw() {
    note.replaceChildren(err ? errorBox(err) : null, msg ? h("div", { class: "xc-note" }, msg) : null);
    list.replaceChildren(...(jobs.length ? jobs.map((j) => {
      const paused = j.paused === true || j.status === "paused" || j.enabled === false;
      return h("article", { class: "xc-card xc-job" },
        h("div", { class: "xc-card-top" }, h("h3", null, j.title || short(j.prompt, 60) || jid(j)),
          h("div", { class: "xc-tags" }, tag(j.when || ""), paused ? tag("paused", "amber") : tag("active", "green"), j.tier ? tag(j.tier) : null)),
        j.prompt ? h("div", { class: "xc-pre-body" }, short(j.prompt, 240)) : null,
        j.next_run || j.last_run ? h("div", { class: "xc-dim" }, [j.next_run ? "next " + j.next_run : "", j.last_run ? "last " + j.last_run : ""].filter(Boolean).join(" · ")) : null,
        h("div", { class: "xc-btnrow" },
          h("button", { class: "xc-btn", onclick: () => edit(paused ? "resume" : "pause", j) }, paused ? "resume" : "pause"),
          h("button", { class: "xc-btn", onclick: () => edit("run_now", j) }, "run now"),
          h("button", { class: "xc-btn", onclick: () => edit("remove", j) }, "remove")));
    }) : [empty("No scheduled jobs.")]));
  }

  async function load() {
    try { jobs = (await api("GET", "/api/schedule")).jobs || []; err = ""; }
    catch (e) { err = e.status === 501 ? "The core does not run schedules yet." : e.message; }
    draw();
  }
  async function post(body, ok) {
    try { await api("POST", "/api/schedule", body); msg = ok; err = ""; await load(); }
    catch (e) { err = e.message; msg = ""; draw(); }
  }
  function add() {
    post({ action: "add", when: f.when.value.trim(), prompt: f.prompt.value.trim(), title: f.title.value.trim(), tier: f.tier.value }, "Job added.")
      .then(() => { if (!err) { f.when.value = f.prompt.value = f.title.value = ""; } });
  }
  function edit(action, j) { post({ action, job_id: jid(j) }, action + " done."); }

  load(); draw();
  return () => {};
}
