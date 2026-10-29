// Search never changes a model. Only Enter, a result click or the explicit action applies its exact route.
import { h } from "./dom.js";
let nextId = 0;
const compact = value => value.toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu, "");

export function createModelPicker({ label = "Model", rows, selected = "", applyLabel = "Use model", note = "", onApply, onCancel }) {
  const id = "xc-model-options-" + (++nextId);
  let models = [], visible = [], active = 0, pending = false, loading = rows == null, error = "", notice = "";
  let current = selected;   // the route the caller holds now; it can move while this picker stays open
  const search = h("input", { class: "xc-field xc-model-search", type: "search", role: "combobox", "aria-label": label + " search",
    "aria-autocomplete": "list", "aria-controls": id, "aria-expanded": "true", autocomplete: "off", spellcheck: "false",
    placeholder: "Type a model or provider name…" });
  const list = h("div", { class: "xc-model-options", id, role: "listbox", "aria-label": label });
  const status = h("p", { class: "xc-model-status", role: "status", "aria-live": "polite" });
  const apply = h("button", { class: "xc-btn xc-primary", type: "button", onclick: submit }, applyLabel);
  const element = h("div", { class: "xc-search-model-picker" }, h("label", null, label, search),
    note ? h("p", { class: "xc-model-note" }, note) : null, list, status,
    h("div", { class: "xc-btnrow" }, apply, onCancel ? h("button", { class: "xc-btn", type: "button", onclick: cancel }, "Cancel") : null));

  function cancel() { if (!pending && onCancel) onCancel(); }
  function draw() {
    const terms = search.value.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
    visible = models.filter(m => terms.every(t => m.search.includes(t) || m.compact.includes(compact(t))))
      .sort((a, b) => Number(b.route.toLocaleLowerCase() === search.value.toLocaleLowerCase().trim())
        - Number(a.route.toLocaleLowerCase() === search.value.toLocaleLowerCase().trim()));
    active = Math.max(0, Math.min(active, visible.length - 1));
    list.replaceChildren(...visible.map((m, i) => h("button", { class: "xc-model-option" + (i === active ? " active" : ""),
      type: "button", role: "option", id: id + "-" + i, tabindex: "-1", "aria-selected": i === active ? "true" : "false",
      onmousedown: e => e.preventDefault(), onclick: () => { active = i; submit(); } },
    h("span", { class: "xc-model-name" }, m.name, m.route === current ? h("span", { class: "xc-model-current" }, "Current") : null),
    h("span", { class: "xc-model-route" }, m.route),
    m.group === "available" ? h("span", { class: "xc-model-tag" }, "available") : null)));
    search.setAttribute("aria-activedescendant", visible.length ? id + "-" + active : "");
    element.setAttribute("aria-busy", pending || loading ? "true" : "false");
    apply.disabled = pending || loading || !visible.length;
    search.disabled = pending;
    status.textContent = pending ? "Changing model…" : error || notice || (loading ? "Loading models…" : !models.length
      ? "No models listed. Open Providers to set up a provider." : !visible.length ? "No matching models. Try another name or provider."
      : visible.length + " model" + (visible.length === 1 ? "" : "s") + ". ↑ ↓ to move, Enter to " + applyLabel.toLowerCase() + ".");
  }
  async function submit() {
    if (pending || loading || !visible.length) return;
    const route = visible[active].route;
    pending = true; error = ""; notice = ""; draw();
    try { const result = await onApply(route); if (typeof result === "string") notice = result; }
    catch (e) { error = e.message || "The model could not be changed. Try again."; }
    finally { pending = false; draw(); if (element.isConnected && !element.closest?.("[hidden]")) search.focus(); }
  }
  search.addEventListener("input", () => { active = 0; error = ""; notice = ""; draw(); });
  search.addEventListener("keydown", e => {
    if (e.isComposing) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!visible.length || pending) return;
      active = (active + (e.key === "ArrowDown" ? 1 : -1) + visible.length) % visible.length;
      draw();
      list.children[active]?.scrollIntoView?.({ block: "nearest" });
    } else if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); submit(); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); if (onCancel) cancel(); else { search.value = ""; active = 0; draw(); apply.focus(); } }
  });
  function setRows(rows) {
    const unique = new Map();
    for (const m of rows || []) {
      if (!m || typeof m.route !== "string" || !m.route || unique.has(m.route)) continue;
      const name = [m.name, m.label, m.model].find(value => typeof value === "string" && value)
        || m.route.split(":").slice(1).join(":") || m.route;
      const text = [m.route, name, m.provider || "", m.id || ""].join(" ").toLocaleLowerCase();
      // The caller says which list a row came from, so a route that no role uses yet still says "available".
      unique.set(m.route, { route: m.route, name, search: text, compact: compact(text), group: m.group === "available" ? "available" : "" });
    }
    models = [...unique.values()]; loading = false; error = "";
    active = Math.max(0, models.findIndex(m => m.route === current)); draw();
  }
  if (rows != null) setRows(rows); else draw();
  // setSelected moves the "Current" mark without touching the search, the focus or the row the arrow keys are on;
  // the caller redraws its own frame and must never rebuild this picker to show a new selection.
  return { element, focus: () => search.focus(), setRows,
    setSelected: route => { if (typeof route === "string" && route) current = route; },
    setError: message => { loading = false; error = message; models = []; draw(); } };
}
