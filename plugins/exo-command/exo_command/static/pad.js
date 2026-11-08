// Pad view: the shared brainstorming pad as one plain textarea. Saves only on the Save button, never on a timer, and
// keeps the text in memory only (no storage). The pad is data the user and agent share, not instructions.
import { h, api, errorBox, pageHead } from "./dom.js";

export function mount(root) {
  let revision = 0, loaded = false, off = false, saving = false, armed = false, closed = false;
  let baseline = "";   // the text as last loaded or saved, to tell unsaved edits from none
  const area = h("textarea", { class: "xc-input xc-pad-text", rows: "18", spellcheck: "true", "aria-label": "Shared pad",
    disabled: true, oninput: () => { armed = false; paint(); } });
  const status = h("p", { class: "xc-note", role: "status", "aria-live": "polite" });
  const save = h("button", { class: "xc-btn xc-primary", type: "button", disabled: true, onclick: () => submit() }, "Save");
  const reload = h("button", { class: "xc-btn", type: "button", disabled: true, onclick: () => again() }, "Reload");
  const body = h("div", { class: "xc-pad" }, area, h("div", { class: "xc-btnrow" }, save, reload), status);
  const gate = h("div", { class: "xc-pad-gate", role: "status" });
  root.replaceChildren(h("div", { class: "xc-pg" }, pageHead({ eyebrow: "Notes", title: "Pad",
    lede: "One note shared with your agent. Both of you can edit it. Nothing is saved until you press Save." }), gate, body));

  const dirty = () => area.value !== baseline;
  function say(t) { status.textContent = t; }
  function paint() {
    // Off keeps an unsaved draft visible and read-only so it can still be copied.
    area.disabled = !loaded;
    area.readOnly = off;
    save.disabled = !loaded || off || saving || !dirty();
    // Reload stays usable after a failed first load so the user can retry in place.
    reload.disabled = saving;
    body.hidden = off && !dirty();
  }
  function refuse(t) { gate.replaceChildren(errorBox(t)); }

  async function load() {
    try {
      const got = await api("GET", "/api/pad");
      area.value = baseline = typeof got.text === "string" ? got.text : "";
      revision = Number.isInteger(got.revision) ? got.revision : 0;
      loaded = true; off = false; gate.replaceChildren();
      say(revision ? "Loaded revision " + revision + "." : "The pad is empty.");
    } catch (e) {
      // 501: the core has the pad off (config shared_pad.enabled), or does not know the command.
      if (e.status === 501) { off = true; loaded = true; gate.replaceChildren(h("p", { class: "xc-void-body" }, "The pad is not enabled.")); }
      else { refuse(e.message); }
    }
    if (!closed) paint();
  }

  async function again() {
    // Reload replaces the text, so edits not yet saved need a second click.
    if (dirty() && !armed) { armed = true; say("You have unsaved edits. Press Reload again to discard them."); return; }
    armed = false;
    say("Reading the pad...");
    await load();
  }

  async function submit() {
    if (saving || !dirty()) return;
    const sent = area.value;
    saving = true; armed = false; gate.replaceChildren(); paint(); say("Saving...");
    try {
      const got = await api("POST", "/api/pad", { text: sent, revision });
      revision = Number.isInteger(got.revision) ? got.revision : revision;
      baseline = sent;
      say("Saved as revision " + revision + ".");
    } catch (e) {
      // dom.api keeps the reply body on err.data. A 409 means the pad moved on; keep the text for the user to copy
      // and let Reload fetch the newer one.
      if (e.status === 409) say("The pad changed elsewhere, so this was not saved. Your text is still here; Reload shows the newer pad"
        + (e.data && Number.isInteger(e.data.revision) ? " (revision " + e.data.revision + ")." : "."));
      else if (e.status === 501) { off = true; gate.replaceChildren(h("p", { class: "xc-void-body" }, "The pad is not enabled.")); say(""); }
      else { refuse(e.message); say(""); }
    } finally { saving = false; if (!closed) paint(); }
  }

  paint();
  say("Reading the pad...");
  load();
  return () => { closed = true; };
}
