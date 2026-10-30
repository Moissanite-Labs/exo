import { h, api } from "./dom.js";
const TIERS = ["fast", "standard", "deep"];

// Separate from provider redraws: testing a key never discards a model draft.
export function workerModels(root) {
  let alive = true, state = null, busy = false;
  const fields = {};
  const status = h("p", { role: "status", "aria-live": "polite" }, "Loading worker models...");
  const effective = h("div", { class: "xc-prov-def-note" });
  const catalog = h("datalist", { id: "worker-model-catalog" });
  const save = h("button", { type: "submit", class: "xc-btn xc-primary", disabled: true }, "Save worker models");
  const reload = h("button", { type: "button", class: "xc-btn", onclick: load }, "Reload settings");
  const rows = TIERS.map((tier) => {
    const primary = h("input", { class: "xc-field", type: "text", list: "worker-model-catalog",
      "aria-label": tier + " preferred model", placeholder: "Search or type provider:model", disabled: true,
      autocomplete: "off", spellcheck: "false" });
    const fallbacks = h("textarea", { class: "xc-field", rows: "2", "aria-label": tier + " fallback models",
      placeholder: "Optional fallback models, one per line", spellcheck: "false", disabled: true });
    fields[tier] = { primary, fallbacks };
    return h("div", { class: "xc-worker-tier" }, h("h3", null, tier[0].toUpperCase() + tier.slice(1)),
      h("label", null, "Preferred model", primary), h("label", null, "Fallbacks, in order", fallbacks));
  });
  root.replaceChildren(h("section", { class: "xc-workers", "aria-label": "Worker models" },
    h("h2", null, "Worker models"),
    h("p", null, "Choose models for fast, standard and deep work. Leave a tier empty to inherit its fallback roles."),
    h("form", { onsubmit: submit }, rows, h("div", { class: "xc-prov-def-act" }, save, reload)), catalog, effective, status));

  function lock(value) {
    busy = value;
    save.disabled = value || !state?.editable;
    reload.disabled = value;
    for (const f of Object.values(fields)) {
      f.primary.disabled = value || !state?.editable;
      f.fallbacks.disabled = value || !state?.editable;
    }
  }
  function showState() {
    effective.replaceChildren(...TIERS.map((tier) => h("p", null,
      tier + " effective fallback order: " + (state.effective[tier].join(" → ") || "none"))));
  }
  async function load() {
    if (busy) return;
    lock(true);
    try {
      const next = await api("GET", "/api/providers/workers");
      if (!alive) return;
      state = next;
      for (const tier of TIERS) {
        fields[tier].primary.value = state.roles[tier][0] || "";
        fields[tier].fallbacks.value = state.roles[tier].slice(1).join("\n");
      }
      showState();
      status.textContent = state.reason || state.scope + " " + state.note;
    } catch (e) {
      if (alive) status.textContent = e.message || "Worker model controls need a newer core.";
    } finally { if (alive) lock(false); }
  }
  async function submit(event) {
    event.preventDefault();
    if (busy || !state?.editable) return;
    const roles = {};
    for (const tier of TIERS) {
      const f = fields[tier], primary = f.primary.value.trim();
      const fallbacks = f.fallbacks.value.split(/\r?\n/).map((r) => r.trim()).filter(Boolean);
      if (!primary && fallbacks.length) {
        status.textContent = "Choose a preferred model before adding fallbacks for " + tier + ".";
        f.primary.focus(); return;
      }
      roles[tier] = primary ? [primary, ...fallbacks] : [];
    }
    lock(true);
    try {
      const next = await api("POST", "/api/providers/workers", { roles, revision: state.revision });
      if (!alive) return;
      state = next;
      showState();
      status.textContent = "Saved and applied. " + state.note;
    } catch (e) {
      if (alive) status.textContent = e.message + " Your edits are still in the form.";
    } finally { if (alive) lock(false); }
  }
  load();
  api("GET", "/api/providers/models").then((data) => {
    if (!alive) return;
    const refs = [...new Set([...(data.available || []), ...(data.models || [])].map((r) => r.route).filter(Boolean))];
    catalog.replaceChildren(...refs.map((ref) => h("option", { value: ref }, ref)));
  }).catch(() => {}); // Typed refs work when catalog discovery fails.
  return () => { alive = false; };
}
