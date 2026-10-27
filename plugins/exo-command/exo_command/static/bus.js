// One event stream for the page: /api/events relays every protocol event from the core.
// Listeners get plain event objects {seq, type, session, body, ts}. The browser reconnects by itself and
// resumes from the last id; expired ring cursors replay the core log and refresh durable state.
import { api, stream } from "./dom.js";

const listeners = new Set();
let source = null;
let panels = [], panelBuffer = null;
let state = { connected: false, transport_connected: false, core_state: "connecting", seq: 0, denied: false, resumed: 0, synced: 0, gaps: 0 };
const stateListeners = new Set();

export function on(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function onState(fn) { stateListeners.add(fn); fn(state); return () => stateListeners.delete(fn); }
export function openPanels() { return panels.slice(); }
// The user dismissed it: the server has dropped it (ui.respond) and the core sends no ui.panel close for that.
export function forget(panelId) { panels = panels.filter((p) => p.panel_id !== panelId); }
export function lastSeq() { return state.seq; }

function setState(patch) {
  state = { ...state, ...patch };
  state.connected = state.transport_connected && state.core_state === "connected";
  stateListeners.forEach((f) => f(state));
}

export async function start() {
  if (source) return;
  let since = 0;
  try {
    const d = await api("GET", "/api/chat/panels");
    panels = d.panels || [];
    since = d.seq || 0;
  } catch (e) { /* the stream still works without the panel list */ }
  setState({ seq: since });
  connect();
}

export async function restart() {
  clearTimeout(retryTimer);
  if (source) source.close();
  source = null; panels = [];
  setState({ transport_connected: false, core_state: "connecting", denied: false, seq: 0 });
  await start();
}

// A browser EventSource reconnects by itself after a dropped connection, but gives up for good on an HTTP error
// (403 after the server restarted with a new page token, 502 while the core restarts). Then we open a new one
// ourselves from the last seq we saw, so the page is never left listening to nothing (D-045).
let retry = 0, retryTimer = 0;
function connect() {
  clearTimeout(retryTimer);
  if (source) source.close();
  source = stream("/api/events?since=" + state.seq);
  const connection = source;
  source.onopen = () => { if (source !== connection) return; retry = 0; const back = state.denied || !state.connected; setState({ transport_connected: true, core_state: "connecting", denied: false, resumed: back ? state.resumed + 1 : state.resumed }); };
  source.onerror = () => {
    if (source !== connection) return;
    setState({ transport_connected: false });
    if (source && source.readyState === 2) {   // CLOSED: the browser will not retry
      retry += 1;
      retryTimer = setTimeout(async () => {
        try { await api("GET", "/api/health"); } catch (e) { if (source === connection && e.status === 403) setState({ denied: true }); }
        if (source === connection && !state.denied) connect();
      }, Math.min(15000, 500 * 2 ** Math.min(retry, 5)));
    }
  };
  source.addEventListener("core-state", (m) => {
    if (source !== connection) return;
    let data;
    try { data = JSON.parse(m.data); } catch (e) { return; }
    if (!data || !["connecting", "connected", "unavailable"].includes(data.state)) return;
    setState({ core_state: data.state });
  });
  source.addEventListener("gap", (m) => {
    if (source !== connection) return;
    let gap;
    try { gap = JSON.parse(m.data); } catch (e) { return; }
    setState({ gaps: state.gaps + 1, ...(gap.reset ? { seq: 0 } : {}) });
  });
  source.addEventListener("synced", async () => {
    const buffered = []; panelBuffer = buffered;
    try {
      const d = await api("GET", "/api/chat/panels");
      if (source !== connection) return;
      panels = d.panels || [];
    } catch (e) { /* replayed panels still work */ }
    finally { if (panelBuffer === buffered) panelBuffer = null; }
    if (source !== connection) return;
    buffered.forEach(foldPanel);
    setState({ synced: state.synced + 1 });
  });
  source.onmessage = (m) => {
    if (source !== connection) return;
    let ev;
    try { ev = JSON.parse(m.data); } catch (e) { return; }
    if (ev.seq && ev.seq <= state.seq) return;   // a replay of what this page already handled
    if (panelBuffer && ev.type === "ui.panel") panelBuffer.push(ev);
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
