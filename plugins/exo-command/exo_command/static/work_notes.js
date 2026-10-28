// A read-only view over command.run work. The core retains principal, gate and executor authority.
import { h, api } from "./dom.js";

export function createWorkNotes({ onVisibility = () => {} } = {}) {
  let session = null, opened = false, mode = "show", generation = 0, stopped = false;
  const element = h("section", { class: "xc-work-notes xc-ins", hidden: true, "aria-label": "Working notes" });
  const button = h("button", { class: "xc-btn xc-work-open", "aria-expanded": "false", onclick: () => opened ? close() : show() }, "Working notes");
  const list = values => h("ul", null, (values || []).map(value => h("li", null, String(value))));
  const field = (label, value) => h("section", { class: "xc-work-field" }, h("h4", null, label),
    Array.isArray(value) ? (value.length ? list(value) : h("p", { class: "xc-dim" }, "None recorded")) : h("p", null, value || "None recorded"));
  // Keep controls mounted so loading and background refreshes do not discard keyboard focus.
  const contentEl = h("div", { class: "xc-work-content" });
  const currentButton = h("button", { class: "xc-btn", onclick: () => load("show") }, "Current notes");
  const historyButton = h("button", { class: "xc-btn", onclick: () => load("history") }, "History");
  element.replaceChildren(h("header", { class: "xc-work-head" }, h("h3", null, "Working notes"),
    h("button", { class: "xc-btn", onclick: close, "aria-label": "Close working notes" }, "Close")),
    h("p", { class: "xc-pm" }, "Agent-authored working claims. Sources show transcript records, not independent verification."),
    h("div", { class: "xc-btnrow" }, currentButton, historyButton,
      h("button", { class: "xc-btn", onclick: refresh }, "Refresh")), contentEl);
  function frame(...content) {
    element.hidden = !opened;
    button.setAttribute("aria-expanded", opened ? "true" : "false");
    currentButton.setAttribute("aria-pressed", mode === "show" ? "true" : "false");
    historyButton.setAttribute("aria-pressed", mode === "history" ? "true" : "false");
    contentEl.replaceChildren(...content);
  }
  function metadata(row) {
    return h("p", { class: "xc-work-meta" }, "Revision " + row.revision + (row.created ? " · " + new Date(typeof row.created === "number" ? row.created * 1000 : row.created).toLocaleString() : ""),
      row.stale ? h("strong", { class: "xc-via" }, " · Stale after transcript undo") : null);
  }
  function render(row) {
    if (!row || !Number.isInteger(row.revision) || row.revision < 0 || row.session !== session) throw Error("Working notes response did not match this conversation.");
    if (!row.revision || !row.document) { frame(h("p", { role: "status" }, "No working notes saved for this conversation. Ask the agent to maintain them if the feature is enabled.")); return; }
    const d = row.document;
    frame(metadata(row), row.stale ? h("p", { class: "xc-work-stale", role: "status" }, "These notes refer to an undone transcript. Review them and ask the agent to correct the notes in a message before reuse.") : null,
      field("Goal", d.goal), field("Constraints", d.constraints),
      h("section", { class: "xc-work-field" }, h("h4", null, "Steps"), (d.steps || []).length ? h("ol", null,
        d.steps.map(step => h("li", null, h("strong", null, ({ pending: "Pending", in_progress: "In progress", done: "Done", blocked: "Blocked" }[step.status] || step.status) + " · "), step.text))) : h("p", { class: "xc-dim" }, "None recorded")),
      field("Blockers", d.blockers), field("Next action", d.next_action),
      h("section", { class: "xc-work-field" }, h("h4", null, "Sources"), (d.evidence || []).length ? h("ul", null,
        d.evidence.map(source => h("li", null, source.kind + " · ", h("code", null, source.id), source.note ? " · " + source.note : ""))) : h("p", { class: "xc-dim" }, "None recorded")),
      row.basis_message ? h("p", { class: "xc-pm" }, "Transcript anchor: ", h("code", null, row.basis_message)) : null);
  }
  async function load(next = mode) {
    mode = next;
    const id = session, request = ++generation;
    if (!opened || stopped) return;
    if (!id) { frame(h("p", { role: "status" }, "Open a conversation to inspect its working notes.")); return; }
    frame(h("p", { role: "status" }, "Reading working notes…"));
    try {
      const result = await api("POST", "/api/chat/sessions/" + encodeURIComponent(id) + "/command", { name: "work", args: next });
      if (stopped || !opened || id !== session || request !== generation) return;
      if (next === "history") {
        const history = result.output && result.output.history;
        if (!Array.isArray(history) || history.some(row => row.session !== id)) throw Error("Working notes history did not match this conversation.");
        frame(h("h4", null, "Revision history"), history.length ? h("ol", { class: "xc-work-history" }, history.map(row => h("li", null, metadata(row)))) : h("p", null, "No working notes saved for this conversation."));
      } else render(result.output);
    } catch (error) {
      if (stopped || !opened || id !== session || request !== generation) return;
      frame(h("p", { role: "status", class: "xc-work-error" }, (error.status === 403 ? "Access refused: " : "Working notes unavailable: ") + error.message));
    }
  }
  function refresh() { if (opened && !stopped) return load(mode); }
  function show(next = "show") {
    opened = true; onVisibility();
    const request = load(next);
    (next === "history" ? historyButton : currentButton).focus();
    if (element.scrollIntoView) element.scrollIntoView({ block: "nearest" });
    return request;
  }
  function close() {
    const inside = element.contains && element.contains(document.activeElement);
    opened = false; onVisibility(); ++generation; element.hidden = true;
    button.setAttribute("aria-expanded", "false");
    if (inside) button.focus();
  }
  return { element, button, show, refresh, isOpen: () => opened,
    setSession(id) { session = id; ++generation; mode = "show"; if (opened) load(); },
    stop() { stopped = true; ++generation; },
  };
}
