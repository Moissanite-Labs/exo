// Future MAIN-turn effort only. No browser preference can claim a core setting was applied.
import { h, api } from "./dom.js";

export function mount(root) {
  let alive = true, data = null, saving = false, message = "", generation = 0, confirmReset = false;
  async function load() {
    const mine = ++generation;
    try {
      const result = await api("GET", "/api/reasoning");
      if (!alive || mine !== generation) return;
      data = result; message = "";
    } catch (e) {
      if (!alive || mine !== generation) return;
      data = null; message = e.message;
    }
    draw();
  }
  async function mutate(request) {
    if (saving || !data) return;
    const mine = ++generation;
    confirmReset = false; saving = true; message = "Saving..."; draw();
    try {
      const result = await api("POST", "/api/reasoning", request);
      if (!alive || mine !== generation) return;
      data = result; message = "Saved for future MAIN turns in this core and seat. An active turn keeps its existing setting.";
    } catch (e) {
      if (!alive || mine !== generation) return;
      message = e.message;
    }
    saving = false; draw();
  }
  function cancelReset() {
    confirmReset = false; draw(); root.querySelector("[data-reset-efforts]")?.focus();
  }
  function draw() {
    if (!alive) return;
    const unavailable = data?.preferences_available === false;
    const choices = h("select", { class: "xc-field", "aria-label": "Reasoning effort", disabled: saving || !data?.supported || unavailable },
      h("option", { value: "", selected: !data?.level }, "Inherit existing settings"),
      (data?.levels || []).map((level) => h("option", { value: level, selected: data?.level === level }, level === "none" ? "none (off)" : level)));
    const latest = data?.last_call?.requested ? data.last_call : null;
    const sent = latest?.sent || (latest?.status?.startsWith("unsupported") ? "provider default (no override)" :
      latest?.status === "not sent" ? "not sent" : "not recorded or confirmed");
    root.replaceChildren(h("section", { class: "xc-panel", "aria-label": "Main model reasoning" },
      h("h2", null, "Main model reasoning"),
      h("p", null, "Saved default for this model route in the selected core and seat. Applies only to future MAIN turns; active turns, scouts and other roles stay unchanged."),
      data ? h("p", null, "Core seat: ", data.seat, ". Current main route: ", h("b", null, data.route)) : null,
      data ? h("p", null, data.reason) : null,
      data?.warning ? h("p", { role: "alert" }, data.warning) : null,
      h("label", null, "Effort ", choices),
      h("button", { type: "button", class: "xc-btn", disabled: saving || !data || unavailable || (!data.supported && !data.level),
        onclick: () => mutate({ route: data.route, level: choices.value || null }) }, "Save main-route default"),
      h("button", { type: "button", class: "xc-btn", disabled: saving, onclick: load }, "Refresh reasoning"),
      h("button", { type: "button", class: "xc-btn", disabled: saving || !data, "data-reset-efforts": "",
        onclick: () => { confirmReset = true; draw(); root.querySelector("[data-confirm-reset]")?.focus(); } }, "Reset saved efforts"),
      confirmReset ? h("div", { role: "group", "aria-label": "Confirm effort reset",
        onkeydown: (e) => { if (e.key === "Escape") { e.preventDefault(); cancelReset(); } } },
        h("p", { role: "alert" }, "Reset all saved effort preferences for this core seat?"),
        h("button", { type: "button", class: "xc-btn", "data-confirm-reset": "", onclick: () => mutate({ action: "reset" }) }, "Confirm reset saved efforts"),
        h("button", { type: "button", class: "xc-btn", onclick: cancelReset }, "Cancel reset")) : null,
      latest ? h("p", null, "Last main call: requested ", latest.requested || "inherit", "; sent to adapter ", sent,
        " on ", latest.route || "no completed route", ". ", latest.status || "") : h("p", null, "Last main call: no recorded effort selection."),
      h("p", { class: "xc-meta" }, "Sent to adapter reports request options, not confirmed delivery or measured computation."),
      h("p", { role: "status" }, message || (data ? "No settings change occurs until Save or Reset." : "Checking core support..."))));
  }
  draw(); load();
  return () => { alive = false; generation++; };
}
