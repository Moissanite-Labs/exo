// One event stream for the page: /api/events relays every protocol event from the core.
// Listeners get plain event objects {seq, type, session, body, ts}. The browser reconnects by itself and
// resumes from the last id, so nothing is missed or repeated.
import { api, stream } from "./dom.js";

const listeners = new Set();
let source = null;
let panels = [];
let state = { connected: false, seq: 0 };
const stateListeners = new Set();

export function on(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function onState(fn) { stateListeners.add(fn); fn(state); return () => stateListeners.delete(fn); }
export function openPanels() { return panels.slice(); }
export function lastSeq() { return state.seq; }

function setState(patch) { state = { ...state, ...patch }; stateListeners.forEach((f) => f(state)); }

export async function start() {
  if (source) return;
  let since = 0;
  try {
    const d = await api("GET", "/api/chat/panels");
    panels = d.panels || [];
    since = d.seq || 0;
  } catch (e) { /* the stream still works without the panel list */ }
  setState({ seq: since });
  source = stream("/api/events?since=" + since);
  source.onopen = () => setState({ connected: true });
  source.onerror = () => setState({ connected: false });
  source.onmessage = (m) => {
    let ev;
    try { ev = JSON.parse(m.data); } catch (e) { return; }
    foldPanel(ev);
    setState({ seq: Math.max(state.seq, ev.seq || 0) });
    listeners.forEach((f) => { try { f(ev); } catch (e) { console.error(e); } });
  };
}

function foldPanel(ev) {
  if (ev.type !== "ui.panel") return;
  const b = ev.body || {};
  if (b.action === "close") { panels = panels.filter((p) => p.panel_id !== b.panel_id); return; }
  const i = panels.findIndex((p) => p.panel_id === b.panel_id);
  const cur = i >= 0 && b.action === "update" ? panels[i] : {};
  const next = { ...cur, ...b, session: ev.session, seq: ev.seq };
  if (i >= 0) panels[i] = next; else panels.push(next);
}
