// Schedule view: list, add, pause, resume, run now and remove jobs through schedule.edit.
// Drawn like the site's roadmap cards (D-061): each job a card with its clock, state, next and last run.
import { h, api, short, errorBox, pageHead, readout, pill, emptyState, stamp, until } from "./dom.js";

export function mount(root) {
  // removing: the job whose remove waits for a Confirm remove click. Remove has no undo (exo-desktop #55).
  // sending: the job whose remove is in flight. Its step stays, with both buttons disabled: Cancel cannot stop it.
  let jobs = [], err = "", msg = "", loaded = false, removing = null, sending = null;
  const list = h("div", { class: "xc-jobs" });
  const field = (label, el, hint) => h("label", { class: "xc-fl" }, h("span", { class: "xc-fl-k" }, label), el,
    hint ? h("span", { class: "xc-fl-hint" }, hint) : null);
  const f = {
    title: h("input", { class: "xc-field", placeholder: "Morning brief", "aria-label": "Title" }),
    when: h("input", { class: "xc-field", placeholder: "0 9 * * * or every 30m", "aria-label": "When" }),
    tier: h("select", { class: "xc-field", "aria-label": "Tier" }, ["fast", "standard", "deep"].map((t) => h("option", { value: t }, t))),
    prompt: h("textarea", { class: "xc-input", rows: "4", placeholder: "What should it do each time?", "aria-label": "Prompt" }),
  };
  const form = h("form", { class: "xc-sched-form", onsubmit: (e) => { e.preventDefault(); add(); } },
    h("p", { class: "xc-k" }, "New job"),
    field("Title", f.title, "optional"),
    h("div", { class: "xc-fl-row" }, field("When", f.when, "cron, or every 30m / 2h"), field("Tier", f.tier)),
    field("Prompt", f.prompt),
    h("div", { class: "xc-btnrow" }, h("button", { class: "xc-btn xc-primary", type: "submit" }, "Add job")));
  const note = h("div", { class: "xc-sched-note", role: "status" });
  const counts = h("div", { class: "xc-phead-read" });
  root.replaceChildren(h("div", { class: "xc-pg" }, pageHead({ eyebrow: "03 / Schedule", title: "Schedule",
    lede: "Prompts on a clock. Each run is a fresh session; its result appears in Tasks.", aside: counts }), note,
    h("div", { class: "xc-sched" }, list, h("aside", { class: "xc-sched-side" }, form))));

  const jid = (j) => j.job_id || j.id;
  const isPaused = (j) => j.paused === true || j.status === "paused" || j.enabled === false;
  // A draw replaces every button, so the focused one leaves the page. draw(j) then focuses j's Cancel, or its remove.
  function draw(focus) {
    let target = null;
    const mark = (j, b) => { if (j === focus) target = b; return b; };
    const live = jobs.filter((j) => !isPaused(j));
    const next = live.map((j) => j.next_run).filter((x) => x && !isNaN(Date.parse(x))).sort((a, b) => Date.parse(a) - Date.parse(b))[0];
    counts.replaceChildren(readout([["Jobs", String(jobs.length)], ["Active", String(live.length), live.length ? "on" : "off"],
      ["Next run", next ? until(next) : "none", next ? "queue" : "off"]]));
    note.replaceChildren(...[err ? errorBox(err) : null, msg ? h("div", { class: "xc-note" }, msg) : null].filter(Boolean));
    if (!loaded) { list.replaceChildren(h("div", { class: "xc-loading" }, "Reading the schedule...")); return; }
    list.replaceChildren(...(jobs.length ? jobs.map((j) => {
      const paused = isPaused(j), name = j.title || short(j.prompt, 60) || jid(j);
      const time = (label, iso) => h("div", null, h("dt", null, label),
        h("dd", null, iso ? [h("span", null, stamp(iso) || String(iso)), h("i", null, until(iso))] : h("span", { class: "xc-faint" }, "not yet")));
      return h("article", { class: "xc-job", "data-s": paused ? "pause" : "on" },
        h("header", { class: "xc-job-top" }, h("code", { class: "xc-cron", title: "when it runs" }, j.when || ""),
          paused ? pill("paused", "pause") : pill("active", "on")),
        h("h3", null, name),
        j.prompt ? h("p", { class: "xc-job-prompt" }, short(j.prompt, 240)) : null,
        h("dl", { class: "xc-job-when" }, time("Next", paused ? "" : j.next_run), time("Last", j.last_run),
          h("div", null, h("dt", null, "Tier"), h("dd", null, h("span", null, j.tier || "fast")))),
        // The confirm is drawn in the page: a desktop web view with no confirm handler answers window.confirm with
        // Cancel. e.detail > 1 is the second click of a double click on "remove", not a confirmation.
        h("div", { class: "xc-btnrow", onkeydown: (e) => { if (e.key === "Escape" && removing === jid(j)) ask(j, null); } },
          removing === jid(j) || sending === jid(j) ? [
          h("span", { role: "status", "aria-live": "polite" }, "Remove " + name + "? It cannot be undone."),
          h("button", { class: "xc-btn xc-danger", disabled: sending === jid(j), onclick: (e) => { if (e.detail > 1 || removing !== jid(j)) return; edit("remove", j); } }, "Confirm remove"),
          mark(j, h("button", { class: "xc-btn", disabled: sending === jid(j), onclick: () => ask(j, null) }, "Cancel"))] : [
          h("button", { class: "xc-btn", onclick: () => edit(paused ? "resume" : "pause", j) }, paused ? "resume" : "pause"),
          h("button", { class: "xc-btn", onclick: () => edit("run_now", j) }, "run now"),
          mark(j, h("button", { class: "xc-btn xc-danger", onclick: () => ask(j, jid(j)) }, "remove"))]));
    }) : [emptyState({ k: "No jobs yet", title: "Nothing runs on its own yet.",
      body: "A job is a prompt on a clock: a morning brief at 07:00, the tests every six hours, a weekly digest.",
      hint: "Add the first one with the form: a cron line such as 0 9 * * 1-5, or every 30m." })]));
    if (target) target.focus();
  }
  // Open the step on j's card (id) or close it (null). Focus goes to Cancel, the safe default for a step that cannot
  // be undone, and back to remove when it closes.
  function ask(j, id) { removing = id; draw(j); }

  async function load() {
    try { jobs = (await api("GET", "/api/schedule")).jobs || []; err = ""; }
    catch (e) { err = e.status === 501 ? "The core does not run schedules yet." : e.message; }
    loaded = true;
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
  const DONE = { pause: "Paused.", resume: "Resumed.", run_now: "Run started; it appears in Tasks.", remove: "Removed." };
  // A remove keeps its step until the POST lands. Closing it at once would put pause, run now and remove back under
  // the pointer, and the second click of a double click on Confirm remove would land on one of them.
  async function edit(action, j) {
    if (action === "remove") sending = jid(j);
    removing = null; draw();
    await post({ action, job_id: jid(j) }, DONE[action] || action + " done.");
    if (sending === jid(j)) { sending = null; draw(); }
  }

  load(); draw();
  return () => {};
}
