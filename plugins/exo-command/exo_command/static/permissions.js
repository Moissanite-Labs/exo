// Permission status and explicit changes are owned and authenticated by the core.
import { h, api } from "./dom.js";
import * as bus from "./bus.js";

export const FULL_CORE_SUMMARY = "all gate asks, deny rules, posting holds and secret/store floors are skipped in all sessions, including delegated and background sessions; calls without a thread still ask";
export const FULL_SUMMARY = "all gate asks, deny rules, posting holds and secret/store floors are skipped for all tool calls in this thread, including background messages; other threads and calls without a thread still ask";
export const SUMMARY = "bypass auto-approves everything except changes to Exo's own records and reading secrets";
let state = { mode: "ask", auto_approved: 0, available: true };
const listeners = new Set();

export function current() { return state; }
export function onChange(fn) { listeners.add(fn); fn(state); return () => listeners.delete(fn); }

function apply(next) {
  if (!next || typeof next !== "object") return;
  const changed = next.mode !== state.mode || next.auto_approved !== state.auto_approved || next.available !== state.available || next.full_session !== state.full_session || next.full_auto_approved !== state.full_auto_approved;
  state = { ...state, ...next };
  if (changed) listeners.forEach((f) => f(state));
}

export async function refresh() {
  try { apply(await api("GET", "/api/permissions")); } catch (e) { /* the banner keeps its last state */ }
  return state;
}

// Full applies to one explicitly selected session. The core authenticates every mode change.
export async function set(mode, session) {
  const d = await api("POST", "/api/permissions", { mode, ...(mode === "full" ? { session } : {}) });
  apply(d.permissions);
  return d.output;
}

// The text a report shows for /permissions with no argument.
export function describe(s) {
  if (s.mode === "full") return (s.full_session === "*" ? "permissions: full for all sessions. " + FULL_CORE_SUMMARY : "permissions: full for " + s.full_session + ". " + FULL_SUMMARY) +
    ". Executor and control-plane authorization still apply. Restart clears full mode. /permissions ask restores asking. Pending approvals still need an answer.";
  if (s.mode !== "bypass") return "permissions: ask. The gate asks you before risky calls." +
    (s.available === false ? " Bypass is not available with a required tool executor (Exo OS)." : " /permissions bypass turns asking off for this run of the core.");
  return "permissions: bypass. " + SUMMARY + ".\nauto-approved " + (s.auto_approved || 0) + " calls this session. " +
    "Deny rules still block. Every auto-approved call is in the ledger as decided_by: bypass.\n/permissions ask turns asking back on.";
}

export function banner() {
  const count = h("span", { class: "xc-perm-n" });
  const label = h("strong", null, "permissions: bypass");
  const summary = h("span", { class: "xc-perm-what" }, SUMMARY);
  const el = h("div", { class: "xc-perm", role: "status", "aria-live": "polite", hidden: true, title: SUMMARY },
    label, summary, count,
    h("button", { class: "xc-btn", onclick: () => set("ask").catch(() => {}) }, "Ask again"));
  onChange((s) => {
    el.hidden = !["bypass", "full"].includes(s.mode);
    label.textContent = s.mode === "full" ? (s.full_session === "*" ? "permissions: full for all sessions" : "permissions: full only " + s.full_session) : "permissions: bypass";
    summary.textContent = s.mode === "full" ? (s.full_session === "*" ? FULL_CORE_SUMMARY : FULL_SUMMARY) : SUMMARY;
    el.title = summary.textContent;
    count.textContent = (s.mode === "full" ? s.full_auto_approved || 0 : s.auto_approved || 0) + " auto-approved";
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
