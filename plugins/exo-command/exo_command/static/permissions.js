// The permission mode banner (D-065). While the core's gate is in bypass, every page shows "permissions: bypass"
// with how many calls it auto-approved. The state lives in the core; this only reads it (GET /api/permissions)
// every few seconds and after any turn ends, and switches it (POST /api/permissions) when the principal asks.
import { h, api } from "./dom.js";
import * as bus from "./bus.js";

export const SUMMARY = "bypass auto-approves everything except changes to Exo's own records and reading secrets";
let state = { mode: "ask", auto_approved: 0, available: true };
const listeners = new Set();

export function current() { return state; }
export function onChange(fn) { listeners.add(fn); fn(state); return () => listeners.delete(fn); }

function apply(next) {
  if (!next || typeof next !== "object") return;
  const changed = next.mode !== state.mode || next.auto_approved !== state.auto_approved || next.available !== state.available;
  state = { ...state, ...next };
  if (changed) listeners.forEach((f) => f(state));
}

export async function refresh() {
  try { apply(await api("GET", "/api/permissions")); } catch (e) { /* the banner keeps its last state */ }
  return state;
}

// mode is "bypass" or "ask". Only the principal's page may turn bypass on; the core refuses anyone else.
export async function set(mode) {
  const d = await api("POST", "/api/permissions", { mode });
  apply(d.permissions);
  return d.output;
}

// The text a report shows for /permissions with no argument.
export function describe(s) {
  if (s.mode !== "bypass") return "permissions: ask. The gate asks you before risky calls." +
    (s.available === false ? " Bypass is not available with a required tool executor (Exo OS)." : " /permissions bypass turns asking off for this run of the core.");
  return "permissions: bypass. " + SUMMARY + ".\nauto-approved " + (s.auto_approved || 0) + " calls this session. " +
    "Deny rules still block. Every auto-approved call is in the ledger as decided_by: bypass.\n/permissions ask turns asking back on.";
}

export function banner() {
  const count = h("span", { class: "xc-perm-n" });
  const el = h("div", { class: "xc-perm", role: "status", "aria-live": "polite", hidden: true, title: SUMMARY },
    h("strong", null, "permissions: bypass"), h("span", { class: "xc-perm-what" }, SUMMARY), count,
    h("button", { class: "xc-btn", onclick: () => set("ask").catch(() => {}) }, "Ask again"));
  onChange((s) => {
    el.hidden = s.mode !== "bypass";
    count.textContent = (s.auto_approved || 0) + " auto-approved";
  });
  return el;
}

let timer = 0;
export function start(every = 3000) {
  if (timer) return;
  refresh();
  timer = setInterval(refresh, every);
  bus.on((ev) => { if (ev.type === "turn.completed" || ev.type === "tool.completed") refresh(); });
}
