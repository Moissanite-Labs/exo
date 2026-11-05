// Approval choices require deliberate card focus. A standing grant always has a separate confirmation.
import { h, short } from "./dom.js";

const CHOICES = [["allow", "o", "once"], ["allow_always", "a", "always"], ["deny", "d", "deny"]];
const editable = (el) => el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable);

export function attachApprovals({ root, composer, answer }) {
  const cards = new Map();
  const inside = (state) => state.el.contains(document.activeElement);

  function cancel(state, focus = true) {
    state.mode = "";
    drawChoices(state);
    if (focus) state.el.focus();
  }
  async function submit(state, decision, note = "") {
    if (state.busy || !state.req.options.includes(decision)) return;
    if (decision === "allow_always" && state.mode !== "confirm") return;
    state.busy = true;
    state.mode = "";
    const restore = inside(state);
    drawChoices(state);
    if (restore) state.el.focus();
    try { await answer(state.req, decision, note); }
    finally {
      state.busy = false;
      if (cards.has(state.req.request_id)) drawChoices(state);
    }
  }
  function choose(state, decision) {
    if (state.busy || !state.req.options.includes(decision)) return;
    if (decision === "allow") { submit(state, decision); return; }
    state.mode = decision === "allow_always" ? "confirm" : "note";
    drawChoices(state);
    state.actions.querySelector(state.mode === "confirm" ? "button" : "input").focus();
  }
  function drawChoices(state) {
    state.instructions.replaceChildren(h("span", null, state.mode === "confirm" ? "Enter confirms always; Esc cancels." :
      state.mode === "note" ? "Enter sends denial; Esc cancels." :
      "Then " + CHOICES.filter(([o]) => state.req.options.includes(o)).map(([o, k, label]) =>
        (o === "allow" ? k : k + ", Enter") + " " + label).join("; ") + ". Esc returns to your message."));
    const button = (label, action, key) => h("button", { type: "button", class: "xc-key", disabled: state.busy,
      "aria-keyshortcuts": key, onclick: action }, label);
    if (state.mode === "confirm") {
      state.actions.replaceChildren(
        h("span", { role: "status", "aria-live": "polite" }, "Allow always? Enter confirms; another key cancels. "),
        button("Confirm always", () => submit(state, "allow_always"), "Enter"),
        button("Cancel", () => cancel(state), "Escape"));
    } else if (state.mode === "note") {
      const note = h("input", { type: "text", "aria-label": "Reason to deny", placeholder: "Why deny? Empty is fine.",
        onkeydown: (e) => {
          if (e.isComposing) return;
          if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); submit(state, "deny", note.value.trim()); }
          if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); cancel(state); }
        } });
      state.actions.replaceChildren(note, button("Send deny", () => submit(state, "deny", note.value.trim()), "Enter"),
        button("Cancel", () => cancel(state), "Escape"));
    } else {
      state.actions.replaceChildren(...CHOICES.filter(([o]) => state.req.options.includes(o)).map(([o, k, label]) =>
        button(h("span", null, h("b", null, k), " " + label), () => choose(state, o), k)));
    }
  }
  function card(req) {
    const actions = h("div", { class: "xc-keys" });
    const instructions = h("span");
    const state = { req, actions, instructions, mode: "", busy: false };
    state.el = h("article", { class: "xc-pn xc-gate xc-in xc-risk-" + req.risk, tabindex: "0", role: "group",
      "aria-label": "Approval: " + req.summary, "data-approval": req.request_id,
      onclick: (e) => { if (!e.target.closest("button,input")) state.el.focus(); },
      onfocusout: () => queueMicrotask(() => { if (state.mode === "confirm" && !inside(state)) cancel(state, false); }),
      onkeydown: (e) => {
        if (e.isComposing || e.ctrlKey || e.metaKey || e.altKey || editable(e.target)) return;
        if (e.repeat) { e.preventDefault(); e.stopPropagation(); return; } // holding Enter is not a second confirmation
        if (state.mode === "confirm") {
          const focusedButton = e.target.closest("button");
          if (e.key === " " && focusedButton) return; // native button activation is an explicit confirmation/cancel
          if (e.key === "Enter") {
            e.preventDefault();
            if (focusedButton && focusedButton.getAttribute("aria-keyshortcuts") === "Escape") cancel(state);
            else submit(state, "allow_always");
          }
          else if (!(["Shift", "Control", "Alt", "Meta"].includes(e.key))) {
            cancel(state);
            if (e.key !== "Tab") e.preventDefault();
          }
          e.stopPropagation();
          return;
        }
        if (state.mode === "note") return;
        const choice = CHOICES.find(([, key]) => key === (e.key === "A" ? "a" : e.key));
        if (choice && state.req.options.includes(choice[0])) { e.preventDefault(); e.stopPropagation(); choose(state, choice[0]); }
        else if (e.key === "Escape") { e.preventDefault(); composer.focus(); }
      } },
      h("div", { class: "xc-ph" }, "Approval \u00B7 " + (req.risk || "medium") + " risk"),
      h("div", { class: "xc-pb" }, req.summary),
      req.call ? h("div", { class: "xc-pm" }, req.call.name + " " + short(JSON.stringify(req.call.arguments || {}), 200)) : null,
      h("div", { class: "xc-approval-help" },
        h("span", { class: "xc-approval-unfocused" }, h("b", null, "Ctrl+F"), " to focus the first approval. "),
        h("span", { class: "xc-approval-focused" }, "Approval focused. "),
        instructions),
      actions);
    drawChoices(state);
    return state;
  }
  function onKey(e) {
    if (e.key.toLowerCase() === "f" && e.ctrlKey && !e.metaKey && !e.altKey && !e.isComposing && cards.size) {
      e.preventDefault();
      cards.values().next().value.el.focus();
    }
  }
  document.addEventListener("keydown", onKey);
  return {
    draw(requests) {
      const ids = new Set(requests.map((a) => a.request_id));
      for (const [id, state] of cards) if (!ids.has(id)) {
        const restore = inside(state);
        state.el.remove(); cards.delete(id);
        if (restore) composer.focus();
      }
      for (const raw of requests) {
        const req = { ...raw, options: raw.options || ["allow", "allow_always", "deny"] };
        if (!cards.has(raw.request_id)) {
          const state = card(req);
          cards.set(raw.request_id, state); root.appendChild(state.el);
        } else {
          const state = cards.get(raw.request_id);
          const changed = JSON.stringify(req.options) !== JSON.stringify(state.req.options);
          state.req = req;
          if (changed) cancel(state, false);
        }
      }
      root.style.paddingBottom = requests.length ? "12px" : "";
    },
    stop() { document.removeEventListener("keydown", onKey); },
  };
}
