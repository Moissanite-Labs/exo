// The chat view: sessions on the left, a terminal-like transcript in the middle with approval cards and a
// composer, and the side panel area on the right. All state comes from /api/chat/* and the /api/events stream.
import { h, api, short, when, partsText, tag, empty } from "./dom.js";
import * as bus from "./bus.js";
import { renderPanel } from "./panels.js";

export function mount(root) {
  let sessions = [], current = null, items = [], busy = false, loadedSeq = 0, loading = false, pending = [];
  let approvals = {}, panelsOpen = bus.openPanels(), notice = "", model = "", cost = null;
  const seen = new Set();   // panels already reported as opened

  const listEl = h("div", { class: "xc-sessions" });
  const logEl = h("div", { class: "xc-log", role: "log", "aria-live": "polite" });
  const apprEl = h("div", { class: "xc-approvals" });
  const statusEl = h("div", { class: "xc-status" });
  const sideEl = h("aside", { class: "xc-side" });
  const input = h("textarea", { class: "xc-input", rows: "2", placeholder: "Message. Enter sends, Shift+Enter adds a line.", "aria-label": "Message" });
  const sendBtn = h("button", { class: "xc-btn", onclick: () => send() }, "Send");
  const stopBtn = h("button", { class: "xc-btn", onclick: () => interrupt() }, "Stop");
  const composer = h("div", { class: "xc-composer" }, input, h("div", { class: "xc-btnrow" }, sendBtn, stopBtn));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  });
  const newBtn = h("button", { class: "xc-btn", onclick: () => create() }, "New session");
  root.replaceChildren(h("div", { class: "xc-chat" },
    h("nav", { class: "xc-chat-left" }, newBtn, listEl),
    h("main", { class: "xc-chat-main" }, logEl, apprEl, statusEl, composer),
    sideEl));

  // ---------------------------------------------------------------- items

  function flash(msg) { notice = msg || ""; drawStatus(); }
  function fail(e) { flash(e && e.message ? e.message : String(e)); }

  function fromMessages(msgs) {
    const out = [];
    const byCall = {};
    for (const m of msgs) {
      if (m.role === "user" || m.role === "assistant") {
        const text = partsText(m.content);
        if (text || m.role === "user") out.push({ kind: m.role, id: m.id || "m" + out.length, text });
        for (const c of m.tool_calls || []) {
          const it = { kind: "tool", id: c.id, name: c.name, args: c.arguments || {}, output: "", status: "done" };
          byCall[c.id] = it;
          out.push(it);
        }
      } else if (m.role === "tool") {
        for (const r of m.tool_results || []) {
          const it = byCall[r.call_id];
          if (it) { it.output = partsText(r.content); it.status = r.is_error ? "error" : "done"; }
        }
      }
    }
    return out;
  }

  function find(id) { return items.find((i) => i.id === id); }

  // ---------------------------------------------------------------- events

  function handle(ev) {
    if (loading) { pending.push(ev); return; }
    if (ev.seq <= loadedSeq && ev.session === current) return;
    const mine = ev.session === current;
    const b = ev.body || {};
    switch (ev.type) {
      case "session.created": case "session.updated": refreshSessions(); return;
      case "approval.requested": approvals[b.request_id] = { ...b, session: ev.session }; drawApprovals(); drawSessions(); return;
      case "approval.resolved": delete approvals[b.request_id]; drawApprovals(); drawSessions(); return;
      case "ui.panel": panelsOpen = bus.openPanels(); drawSide(); return;
      case "notice": flash(b.text); return;
      default: break;
    }
    if (!mine) return;
    switch (ev.type) {
      case "turn.started": busy = true; model = b.model || model; drawStatus(); break;
      case "turn.completed": busy = false; drawStatus(); break;
      case "usage.updated": if (b.session_total) cost = b.session_total.cost_usd; if (b.model) model = b.model; drawStatus(); break;
      case "message.delta": delta(b); break;
      case "message.completed": completed(b.message || {}); break;
      case "tool.started": if (!find(b.call.id)) push({ kind: "tool", id: b.call.id, name: b.call.name, args: b.call.arguments || {}, output: "", status: "running", risk: b.risk }); break;
      case "tool.output": { const t = find(b.call_id); if (t) { t.output += b.text; redraw(t); } break; }
      case "tool.completed": {
        const t = find(b.result.call_id);
        if (t) { t.output = partsText(b.result.content); t.status = b.result.is_error ? "error" : "done"; t.ms = b.duration_ms; redraw(t); }
        break;
      }
      case "error": flash(b.message); break;
      default: break;
    }
  }

  function delta(b) {
    let it = find(b.message_id);
    if (!it) { it = { kind: "assistant", id: b.message_id, text: "", streaming: true }; push(it); }
    it.text += b.text;
    if (it.textEl) it.textEl.textContent = it.text; else redraw(it);
    stick();
  }

  function completed(m) {
    if (m.role === "user") { if (!find(m.id)) push({ kind: "user", id: m.id, text: partsText(m.content) }); return; }
    if (m.role === "assistant") {
      const text = partsText(m.content);
      const it = find(m.id);
      if (it) { it.text = text || it.text; it.streaming = false; redraw(it); }
      else if (text) push({ kind: "assistant", id: m.id, text });
      for (const c of m.tool_calls || []) if (!find(c.id)) push({ kind: "tool", id: c.id, name: c.name, args: c.arguments || {}, output: "", status: "running" });
    } else if (m.role === "tool") {
      for (const r of m.tool_results || []) {
        const t = find(r.call_id);
        if (t) { t.output = partsText(r.content); t.status = r.is_error ? "error" : "done"; redraw(t); }
      }
    }
  }

  // ---------------------------------------------------------------- drawing

  function stick() { if (logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 120) logEl.scrollTop = logEl.scrollHeight; }
  function push(it) {
    items.push(it);
    if (items.length === 1) { drawLog(); return; }
    logEl.appendChild(itemEl(it)); stick();
  }
  function redraw(it) { if (it.el && it.el.isConnected) { const n = itemEl(it); it.el.replaceWith(n); } else drawLog(); }
  function drawLog() {
    logEl.replaceChildren(...(items.length ? items.map(itemEl) : [empty(current ? "No messages yet. Say something." : "Pick a session or start a new one.")]));
    logEl.scrollTop = logEl.scrollHeight;
  }

  function itemEl(it) {
    let el;
    if (it.kind === "user") {
      el = h("div", { class: "xc-msg xc-user" }, h("span", { class: "xc-prompt" }, "> "), h("span", { class: "xc-text" }, it.text));
    } else if (it.kind === "assistant") {
      it.textEl = h("div", { class: "xc-text" }, it.text);
      el = h("div", { class: "xc-msg xc-asst" + (it.streaming ? " xc-streaming" : "") }, it.textEl, it.streaming ? null : feedbackBar(it));
    } else {
      const args = short(JSON.stringify(it.args), 140);
      el = h("details", { class: "xc-msg xc-tool-item xc-ts-" + it.status },
        h("summary", null, h("span", { class: "xc-prompt" }, it.status === "running" ? "… " : it.status === "error" ? "! " : "• "),
          it.name + " ", h("span", { class: "xc-dim" }, args), it.ms ? h("span", { class: "xc-dim" }, " " + it.ms + "ms") : null),
        h("pre", { class: "xc-pre-body" }, it.output || (it.status === "running" ? "running" : "(no output)")));
    }
    it.el = el;
    return el;
  }

  function feedbackBar(it) {
    const done = it.verdict;
    const btn = (v, label) => h("button", { class: "xc-btn xc-fb" + (done === v ? " on" : ""),
      onclick: () => (v === "edit" ? (it.editing = !it.editing, redraw(it)) : feedback(it, v, "")) }, label);
    const bar = h("div", { class: "xc-feedback" }, btn("accept", "accept"), btn("edit", "edit"), btn("reject", "reject"),
      done ? h("span", { class: "xc-dim" }, "sent: " + done) : null);
    if (!it.editing) return bar;
    const box = h("textarea", { class: "xc-input", rows: "4", "aria-label": "Better answer" });
    box.value = it.edit != null ? it.edit : it.text;
    return h("div", null, bar, h("div", { class: "xc-edit" }, box, h("div", { class: "xc-btnrow" },
      h("button", { class: "xc-btn", onclick: () => { it.edit = box.value; feedback(it, "edit", box.value); } }, "Send edit"),
      h("button", { class: "xc-btn", onclick: () => { it.editing = false; redraw(it); } }, "Cancel"))));
  }

  function drawStatus() {
    const bits = [busy ? "working" : "idle"];
    if (model) bits.push(model);
    if (cost != null) bits.push("$" + cost.toFixed(cost < 1 ? 3 : 2));
    statusEl.replaceChildren(h("span", null, bits.join(" · ")), notice ? h("span", { class: "xc-flash" }, notice) : null);
    stopBtn.disabled = !busy;
  }

  function drawSessions() {
    const waiting = new Set(Object.values(approvals).map((a) => a.session));
    listEl.replaceChildren(...(sessions.length ? sessions.map((s) =>
      h("button", { class: "xc-sess" + (s.id === current ? " on" : ""), onclick: () => open(s.id) },
        h("span", { class: "xc-sess-title" }, short(s.title || s.id, 40)),
        h("span", { class: "xc-dim" }, [s.status === "live" ? "running" : "", waiting.has(s.id) ? "needs you" : "", when(s.updated)].filter(Boolean).join(" · ")))) : [empty("No sessions yet.")]));
  }

  function drawApprovals() {
    const mine = Object.values(approvals).filter((a) => a.session === current);
    apprEl.replaceChildren(...mine.map((a) => h("article", { class: "xc-card xc-approval xc-risk-" + a.risk },
      h("div", { class: "xc-card-top" }, h("strong", null, a.summary), tag(a.risk, a.risk === "high" ? "red" : "amber")),
      a.call ? h("div", { class: "xc-tool" }, a.call.name + " " + short(JSON.stringify(a.call.arguments || {}), 200)) : null,
      h("div", { class: "xc-btnrow" }, (a.options || ["allow", "deny", "allow_always"]).map((o) =>
        h("button", { class: "xc-btn", onclick: () => answer(a, o) }, o.replace("_", " ")))))));
  }

  function drawSide() {
    const mine = panelsOpen.filter((p) => !p.session || p.session === current);
    sideEl.replaceChildren(...(mine.length ? mine.map((p) => renderPanel(p, { act: panelAct, dismiss: panelDismiss })) : [h("div", { class: "xc-dim xc-side-empty" }, "Panels the model opens show here.")]));
    sideEl.classList.toggle("xc-has", mine.length > 0);
    for (const p of mine) if (!seen.has(p.panel_id)) { seen.add(p.panel_id); respondPanel(p, "opened", {}, true); }
  }

  // ---------------------------------------------------------------- actions

  async function refreshSessions() {
    try { sessions = (await api("GET", "/api/chat/sessions")).sessions; drawSessions(); } catch (e) { fail(e); }
  }

  async function open(id) {
    current = id; loading = true; pending = []; items = []; busy = false; cost = null; flash("");
    drawSessions(); drawLog(); drawApprovals(); drawSide();
    try {
      const d = await api("GET", "/api/chat/sessions/" + encodeURIComponent(id));
      if (current !== id) return;
      items = fromMessages(d.messages || []);
      loadedSeq = d.seq || 0;
      const live = sessions.find((s) => s.id === id);
      busy = !!(live && live.status === "live");
      model = (d.session && d.session.model) || "";
    } catch (e) { fail(e); }
    loading = false;
    drawLog(); drawStatus();
    const buffered = pending; pending = [];
    buffered.forEach(handle);
    try {
      const a = await api("GET", "/api/section/approvals");
      for (const x of (a.pending || [])) approvals[x.request_id] = { ...x, session: x.session };
      drawApprovals(); drawSessions();
    } catch (e) { /* approvals still arrive by event */ }
  }

  async function create() {
    try {
      const d = await api("POST", "/api/chat/sessions", {});
      await refreshSessions();
      if (d.session) open(d.session.id);
    } catch (e) { fail(e); }
  }

  async function send() {
    const text = input.value.trim();
    if (!text) return;
    try {
      if (!current) {
        const d = await api("POST", "/api/chat/sessions", { title: short(text, 60) });
        await refreshSessions();
        await open(d.session.id);
      }
      input.value = "";
      await api("POST", "/api/chat/sessions/" + encodeURIComponent(current) + "/send", { text, mode: busy ? "steer" : "send" });
      flash("");
    } catch (e) { fail(e); }
  }

  async function interrupt() {
    try { await api("POST", "/api/chat/sessions/" + encodeURIComponent(current) + "/interrupt", {}); } catch (e) { fail(e); }
  }

  async function answer(a, decision) {
    try { await api("POST", "/api/approvals/" + encodeURIComponent(a.request_id), { decision }); delete approvals[a.request_id]; drawApprovals(); drawSessions(); } catch (e) { fail(e); }
  }

  async function feedback(it, verdict, text) {
    try {
      await api("POST", "/api/feedback", { session: current, verdict, message_id: it.id, text });
      it.verdict = verdict; it.editing = false; redraw(it); flash("");
    } catch (e) { fail(e); }
  }

  async function respondPanel(p, action, value, quiet) {
    try { await api("POST", "/api/ui/respond", { panel_id: p.panel_id, action, value }); }
    catch (e) { if (!quiet) fail(e); }
  }
  function panelAct(p, value) { respondPanel(p, "acted", value); }
  async function panelDismiss(p) {
    await respondPanel(p, "dismissed", {});
    panelsOpen = panelsOpen.filter((x) => x.panel_id !== p.panel_id);
    drawSide();
  }

  // ---------------------------------------------------------------- start

  const off = bus.on(handle);
  drawLog(); drawStatus(); drawApprovals(); drawSide();
  refreshSessions().then(() => { if (!current && sessions.length) open(sessions[0].id); });
  return off;
}
