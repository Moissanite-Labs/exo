// The chat view: sessions on the left, a terminal-like transcript in the middle with approval cards and a
// composer, and the side panel area on the right. All state comes from /api/chat/* and the /api/events stream.
import { h, api, short, when, partsText, tag, empty } from "./dom.js";
import * as bus from "./bus.js";
import { renderPanel } from "./panels.js";
import { createHero } from "./hero.js";
import { attachComposer } from "./composer.js";

export function mount(root) {
  let sessions = [], current = null, items = [], busy = false, loadedSeq = 0, loading = false, pending = [];
  let approvals = {}, steers = {}, gone = new Set(), panelsOpen = bus.openPanels(), notice = "", model = "", cost = null;
  let requested = "", ctxTokens = null, cacheShare = null, ctx = null, findings = null, calls = [], costSeen = false;   // instruments from model.call and usage.updated
  const seen = new Set();   // panels already reported as opened
  const landed = new Set();   // user messages the core has added to a conversation

  const listEl = h("div", { class: "xc-sessions" });
  const logEl = h("div", { class: "xc-log", role: "log", "aria-live": "polite" });
  const apprEl = h("div", { class: "xc-approvals" });
  const steerEl = h("div", { class: "xc-steers", "aria-label": "Queued steer messages" });
  const statusEl = h("div", { class: "xc-strip", "aria-label": "Status" });
  const sideEl = h("aside", { class: "xc-side" });
  const input = h("textarea", { class: "xc-input", rows: "2", placeholder: "Ask, or give a task. Enter sends, Shift+Enter adds a line.", "aria-label": "Message" });
  const sendBtn = h("button", { class: "xc-btn xc-primary", onclick: () => send() }, "Send");
  const stopBtn = h("button", { class: "xc-btn", onclick: () => interrupt() }, "Stop");
  const promptEl = h("span", { class: "xc-cp", "aria-hidden": "true" }, "\u203A");
  const inputWrap = h("div", { class: "xc-inwrap" }, input);
  const composer = h("div", { class: "xc-composer" }, promptEl, inputWrap, h("div", { class: "xc-btnrow" }, sendBtn, stopBtn));
  const help = attachComposer({ input, composer, getSession: () => current,
    reducedMotion: window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
    if (e.key === "ArrowUp" && !input.value && lastSteer() && !e.defaultPrevented) { e.preventDefault(); e.stopImmediatePropagation(); unsteer(lastSteer()); }
  }, true);
  const newBtn = h("button", { class: "xc-btn xc-primary", onclick: () => create() }, "New thread");
  const heroCanvas = h("canvas", { class: "xc-hero-canvas", "aria-hidden": "true" });
  const heroText = h("div", { class: "xc-hero-text" });
  const heroEl = h("div", { class: "xc-hero", "aria-hidden": "false" }, h("div", { class: "xc-hero-grid" }), heroCanvas, heroText);
  const hero = createHero(heroCanvas);
  let heroOn = false, heroTimer = 0;
  const stage = h("div", { class: "xc-stage" }, heroEl, logEl);
  const setupBanner = h("div", { class: "xc-setup", hidden: true }, h("span", null, "No model provider is set up yet."),
    h("a", { href: "#providers" }, "Set up providers"));
  root.replaceChildren(h("div", { class: "xc-chat" },
    h("nav", { class: "xc-chat-left" }, newBtn, h("div", { class: "xc-label" }, "Threads"), listEl),
    h("main", { class: "xc-chat-main" }, setupBanner, stage, apprEl, statusEl, steerEl, help.guideEl, composer, help.menuEl),
    sideEl));

  if (sessionStorage.getItem("xc-token")) {   // quiet check: skipped on any error
    api("GET", "/api/providers").then((d) => { if (d && d.ready === false) setupBanner.hidden = false; }).catch(() => {});
  }

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

  let lastEvent = 0;
  function handle(ev) {
    if (ev.seq) lastEvent = Math.max(lastEvent, ev.seq);
    if (loading) { pending.push(ev); return; }
    if (ev.seq <= loadedSeq && ev.session === current) return;
    const mine = ev.session === current;
    const b = ev.body || {};
    if (ev.type === "message.completed" && b.message && b.message.role === "user") {   // a steer that lands is no longer pending
      landed.add(b.message.id);   // the event can beat the reply to the send
      if (steers[b.message.id]) { delete steers[b.message.id]; drawSteers(); }
    }
    if (ev.type === "turn.completed" && Object.keys(steers).length) { for (const k of Object.keys(steers)) if (steers[k].session === ev.session) delete steers[k]; drawSteers(); }
    switch (ev.type) {
      case "steer.cancelled": if (steers[b.message_id]) { delete steers[b.message_id]; drawSteers(); } return;
      case "session.created": case "session.updated": refreshSessions(); return;
      case "approval.requested": if (gone.has(b.request_id)) return; approvals[b.request_id] = { ...b, session: ev.session, seq: ev.seq }; drawApprovals(); drawSessions(); return;
      case "approval.resolved": gone.add(b.request_id); delete approvals[b.request_id]; drawApprovals(); drawSessions(); return;
      case "ui.panel": panelsOpen = bus.openPanels(); drawSide(); return;
      case "notice":
        if (mine && /^tip\b/i.test(b.text || "")) { push({ kind: "sys", id: "tip" + ev.seq, tone: "tip", text: b.text.replace(/^tip:?\s*/i, "") }); return; }
        flash(b.text); return;
      default: break;
    }
    if (!mine) return;
    switch (ev.type) {
      case "turn.started": busy = true; help.note({ busy: true }); if (b.model) { model = b.model; requested = b.model; } drawStatus(); break;
      case "turn.completed": {
        busy = false; drawStatus(); loadContext();
        const tm = b.timing || {};
        help.note({ busy: false, turns: items.filter((i) => i.kind === "user").length, longTurn: (tm.wall_ms || 0) - (tm.tool_ms || 0) - (tm.approval_ms || 0) >= 60000 });
        break;
      }
      case "usage.updated": {
        if (b.session_total) { costSeen = true; cost = b.session_total.cost_usd == null ? null : b.session_total.cost_usd; }   // null = unpriced, never $0
        if (b.model && !model) model = b.model;
        instruments(b.usage); drawStatus(); break;
      }
      case "model.call":
        help.note({ providerError: !!b.error });
        if (b.route) model = b.route;
        if (b.requested) requested = b.requested;
        if (b.usage) instruments(b.usage);
        calls.unshift({ route: b.route || "", requested: b.requested || "", ttft: b.ttft_ms, ms: b.total_ms, cache: b.usage ? cacheOf(b.usage) : null, cost: b.usage ? b.usage.cost_usd : undefined, error: b.error });
        calls = calls.slice(0, 6);
        drawStatus(); drawSide(true); break;
      case "job.updated": {
        if (b.state === "done") help.note({ jobDone: b.job_id });
        const bits = [b.state || "running"];
        if (b.exit_code != null && b.exit_code !== 0) bits.push("exit " + b.exit_code);
        if (b.elapsed_ms) bits.push(b.elapsed_ms >= 1000 ? (b.elapsed_ms / 1000).toFixed(1) + "s" : b.elapsed_ms + "ms");
        sys("job", short(b.command || b.job_id, 60) + " \u00B7 " + bits.join(" \u00B7 ") + (b.detail ? " \u00B7 " + short(b.detail, 80) : ""), "mem");
        break;
      }
      case "memory.summarized":
        loadContext();
        sys("memory", "log " + b.start + "-" + b.end + " folded \u00B7 " + b.before_tokens + " \u2192 " + b.after_tokens + " tok", "mem"); break;
      case "context.compacted":
        sys("context", "compacted \u00B7 " + b.before_tokens + " \u2192 " + b.after_tokens + " tok", "mem"); break;
      case "peer.message": push({ kind: "peer", id: b.msg_id || "peer" + items.length, sender: b.sender, text: b.text, trust: b.trust, mode: b.mode }); break;
      case "taste.updated": push({ kind: "taste", id: "taste-" + b.rule_id + "-" + ev.seq, action: b.action, text: b.text, path: b.path }); break;
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

  // What the next call carries (input + cached) and how much of it came from the cache.
  function instruments(u) {
    if (!u) return;
    const total = (u.input_tokens || 0) + (u.cache_read_tokens || 0) + (u.cache_write_tokens || 0);
    if (total > 0) { ctxTokens = total; cacheShare = (u.cache_read_tokens || 0) / total; }
  }
  function cacheOf(u) {
    const t = (u.input_tokens || 0) + (u.cache_read_tokens || 0) + (u.cache_write_tokens || 0);
    return t > 0 ? (u.cache_read_tokens || 0) / t : null;
  }
  // /context and /findings are read-only reports the core answers itself; the numbers feed the instruments.
  async function runCommand(name, args) {
    const d = await api("POST", "/api/chat/sessions/" + encodeURIComponent(current) + "/command", { name, args: args || "" });
    return d.output;
  }
  function parseContext(t) {
    const n = (re) => { const m = re.exec(t); return m ? Number(m[1]) : 0; };
    const nodes = /Tree nodes: (\d+) done, (\d+) pending/.exec(t);
    return { system: n(/system\s+(\d+)/), tree: n(/summary tree\s+(\d+)/), notes: n(/other threads\s+(\d+)/), tail: n(/recent tail\s+(\d+)/), total: n(/total\s+(\d+)/),
      summaries: n(/(\d+) summaries over/), nodes: n(/summaries over (\d+) nodes/), done: nodes ? +nodes[1] : 0, pending: nodes ? +nodes[2] : 0, log: n(/Log: (\d+) entries/), frontier: n(/summarized up to (\d+)/) };
  }
  async function loadContext() {
    const id = current;
    if (!id) return;
    try {
      const out = await runCommand("context");
      if (id !== current || typeof out !== "string") return;
      ctx = parseContext(out); ctxTokens = ctx.total || ctxTokens;
      drawStatus(); drawSide(true); if (!items.length) drawHero();
    } catch (e) { /* older core: the instruments fall back to what the events carry */ }
    try {
      const out = await runCommand("findings", "count");
      if (id === current && out && typeof out.open === "number") { findings = out.open; help.note({ findings }); drawStatus(); }
    } catch (e) { /* no findings count */ }
  }
  function sys(label, text, tone) { push({ kind: "sys", id: "sys" + items.length + "-" + Date.now(), label, text, tone }); }

  // ---------------------------------------------------------------- drawing

  function stick() { if (logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 120) logEl.scrollTop = logEl.scrollHeight; }
  function push(it) {
    it.fresh = true;
    items.push(it);
    if (items.length === 1) { drawLog(); return; }
    logEl.appendChild(itemEl(it)); stick();
  }
  function drawHero() {
    const want = items.length === 0;
    if (want) {
      clearTimeout(heroTimer);
      const s = sessions.find((x) => x.id === current);
      const chip = (k, l) => h("span", { class: "xc-chipk" }, h("b", null, k), l);
      const line = (k, v) => h("div", { class: "xc-hl" }, h("span", { class: "xc-i" }, k), h("span", { class: "xc-v" }, v));
      heroText.replaceChildren(
        h("div", { class: "xc-hero-title" }, "E X O"),
        h("div", { class: "xc-hero-sub" }, current ? "ready \u00B7 nothing in this thread yet" : "no thread open \u00B7 pick one or start a new thread", h("span", { class: "xc-caret" })),
        h("div", { class: "xc-hlines" },
          line("agent", (s && (s.agent || s.seat)) || "exo"),
          line("model", model || (s && s.model) || "not set"),
          line("memory", ctx ? ctx.summaries + " summaries \u00B7 " + ctx.nodes + " nodes \u00B7 log " + ctx.log + " entries" : "reading\u2026")),
        h("div", { class: "xc-chips" }, chip("\u21B5", "send"), chip("\u21E7\u21B5", "new line"), chip("/", "context \u00B7 findings \u00B7 trace"), chip("y n a", "approvals")));
      heroEl.classList.remove("out"); heroEl.hidden = false;
      if (!heroOn) { heroOn = true; hero.start(); }
    } else if (heroOn) {
      heroOn = false; heroEl.classList.add("out");
      heroTimer = setTimeout(() => { heroEl.hidden = true; hero.stop(); }, 600);
    }
  }
  function redraw(it) { const old = it.el; if (old && old.isConnected) { old.replaceWith(itemEl(it)); } else drawLog(); }   // keep the old node: itemEl points it.el at the new one
  function drawLog() {
    logEl.replaceChildren(...items.map(itemEl));
    drawHero();
    logEl.scrollTop = logEl.scrollHeight;
  }

  function itemEl(it) {
    let el;
    const fresh = it.fresh ? " xc-in" : "";
    it.fresh = false;
    if (it.kind === "user") {
      el = h("div", { class: "xc-msg xc-user" + fresh }, h("span", { class: "xc-prompt" }, "\u203A"), h("span", { class: "xc-text" }, it.text));
    } else if (it.kind === "assistant") {
      it.textEl = h("div", { class: "xc-text" }, it.text);
      el = h("div", { class: "xc-msg xc-asst" + (it.streaming ? " xc-streaming" : "") + fresh }, it.textEl, it.streaming ? null : feedbackBar(it));
    } else if (it.kind === "sys") {
      el = h("div", { class: "xc-msg xc-sysline" + (it.tone === "tip" ? " xc-tip" : "") + fresh },
        it.tone === "tip" ? "tip: " + it.text : [h("span", { class: "xc-k" }, it.label), " \u00B7 " + it.text]);
    } else if (it.kind === "report") {
      el = h("section", { class: "xc-msg xc-pn" + fresh }, h("div", { class: "xc-ph" }, it.title), h("pre", { class: "xc-report" }, it.text));
    } else if (it.kind === "peer") {
      el = h("section", { class: "xc-msg xc-pn xc-peer" + fresh },
        h("div", { class: "xc-ph" }, "Peer \u00B7 " + (it.sender || "unknown"), it.trust ? tag(it.trust) : null, it.mode ? tag(it.mode) : null),
        h("div", { class: "xc-pb xc-text" }, it.text),
        h("div", { class: "xc-pm" }, "from another session, not from you"));
    } else if (it.kind === "taste") {
      el = h("section", { class: "xc-msg xc-pn" + fresh },
        h("div", { class: "xc-ph" }, "Taste \u00B7 " + (it.action || "updated")),
        it.text ? h("div", { class: "xc-pb" }, it.text) : null,
        it.path ? h("div", { class: "xc-pm" }, it.path) : null);
    } else {
      const args = short(Object.values(it.args || {}).map((v) => (typeof v === "string" ? v : JSON.stringify(v))).join(" "), 120);
      const glyph = it.status === "running" ? "\u25CF" : it.status === "error" ? "\u00D7" : "\u00B7";
      el = h("details", { class: "xc-msg xc-tool-item xc-ts-" + it.status + fresh },
        h("summary", null, h("span", { class: "xc-tg" }, glyph), h("span", { class: "xc-tname" }, it.name), h("span", { class: "xc-targs" }, args),
          it.status === "running" ? h("span", { class: "xc-wire" }) : null,
          it.ms ? h("span", { class: "xc-tms" }, it.ms >= 1000 ? (it.ms / 1000).toFixed(1) + "s" : it.ms + "ms") : null),
        h("pre", { class: "xc-pre-body" }, (it.args && Object.keys(it.args).length ? JSON.stringify(it.args, null, 1) + "\n\n" : "") + (it.output || (it.status === "running" ? "running" : "(no output)"))));
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
    const cell = (label, ...v) => h("span", null, h("span", { class: "xc-i" }, label), h("span", { class: "xc-v" }, ...v));
    const out = [h("span", { class: "xc-state" + (busy ? " on" : "") }, h("span", { class: "xc-dot" + (busy ? " on" : "") }), busy ? "working" : "idle")];
    if (model) {
      const fell = requested && requested !== model;
      const m = cell("model", model);
      m.classList.add("xc-model");
      if (fell) m.appendChild(h("span", { class: "xc-via", title: "asked " + requested }, "\u2190 fallback"));
      out.push(m);
    }
    if (ctxTokens != null) {
      const pct = Math.min(100, Math.round(ctxTokens / 2000));   // against a nominal 200k window
      out.push(h("span", { class: "xc-opt" }, h("span", { class: "xc-i" }, "memory"),
        h("span", { class: "xc-gauge" + (pct > 80 ? " hot" : ""), title: "context for the next call, nominal 200k window" }, h("i", { style: "width:" + pct + "%" })),
        h("span", { class: "xc-v" }, fmtTok(ctxTokens))));
    }
    if (cacheShare != null) out.push(h("span", { class: "xc-opt" }, h("span", { class: "xc-i" }, "cache"), h("span", { class: "xc-v" }, Math.round(cacheShare * 100) + "%")));
    if (findings != null) out.push(h("span", { class: "xc-opt" }, h("span", { class: "xc-i" }, "findings"), h("span", { class: "xc-v" + (findings ? " xc-via" : "") }, String(findings))));
    if (cost != null || costSeen) out.push(cell("cost", cost != null ? money(cost) : "unknown"));
    out.push(busy ? h("span", { class: "xc-wire", "aria-hidden": "true" }) : h("span", { class: "xc-spacer" }));
    if (notice) out.push(h("span", { class: "xc-flash" }, notice));
    statusEl.replaceChildren(...out);
    stopBtn.disabled = !busy;
    promptEl.textContent = busy ? "steer \u203A" : "\u203A";
    composer.classList.toggle("xc-steer", busy);
    const live = document.getElementById("xc-live");
    if (live) live.classList.toggle("on", busy);
  }
  function money(v) { return "$" + v.toFixed(v < 1 ? 3 : 2); }
  function fmtTok(n) { return n >= 10000 ? Math.round(n / 1000) + "k" : n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n); }
  function drawWhere() {
    const el = document.getElementById("xc-where");
    if (!el) return;
    const s = sessions.find((x) => x.id === current);
    el.replaceChildren(...(s ? [h("b", null, s.agent || s.seat || "exo"), " \u00B7 " + short(s.title || s.id, 36)] : []));
  }

  function drawSessions() {
    const waiting = new Set(Object.values(approvals).map((a) => a.session));
    listEl.replaceChildren(...(sessions.length ? sessions.map((s) =>
      h("button", { class: "xc-sess" + (s.id === current ? " on" : ""), onclick: () => open(s.id) },
        h("span", { class: "xc-sess-title" }, short(s.title || s.id, 40)),
        h("span", { class: "xc-dim" }, [s.status === "live" ? "running" : "", waiting.has(s.id) ? h("span", { class: "xc-needs" }, "needs you") : "", when(s.updated)].filter(Boolean).flatMap((x, i) => (i ? [" \u00B7 ", x] : [x]))))) : [empty("No threads yet.")]));
    drawWhere();
  }

  const HOTKEYS = { allow: "y", deny: "n", allow_always: "a", always: "a" };
  function keyFor(o, taken) {
    let k = HOTKEYS[o] || o[0] || "?";
    for (const c of o) { if (!taken.has(k)) break; k = c; }
    taken.add(k);
    return k;
  }
  function drawApprovals() {
    const mine = Object.values(approvals).filter((a) => a.session === current);
    apprEl.replaceChildren(...mine.map((a) => {
      const taken = new Set();
      const opts = (a.options || ["allow", "deny", "allow_always"]).map((o) => [o, keyFor(o, taken)]);
      return h("article", { class: "xc-pn xc-gate xc-in xc-risk-" + a.risk, "data-approval": a.request_id },
        h("div", { class: "xc-ph" }, "Approval \u00B7 " + (a.risk || "medium") + " risk"),
        h("div", { class: "xc-pb" }, a.summary),
        a.call ? h("div", { class: "xc-pm" }, a.call.name + " " + short(JSON.stringify(a.call.arguments || {}), 200)) : null,
        h("div", { class: "xc-keys" }, opts.map(([o, k]) =>
          h("button", { class: "xc-key" + (o === "allow" ? " xc-key-go" : ""), onclick: () => answer(a, o) }, h("b", null, k), o.replace("_", " ")))));
    }));
    apprEl.style.paddingBottom = mine.length ? "12px" : "";
  }
  function onKey(e) {
    if (e.metaKey || e.ctrlKey || e.altKey || /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || "")) return;
    const a = Object.values(approvals).find((x) => x.session === current);
    if (!a) return;
    const taken = new Set();
    for (const o of (a.options || ["allow", "deny", "allow_always"])) if (keyFor(o, taken) === e.key.toLowerCase()) { e.preventDefault(); answer(a, o); return; }
  }
  document.addEventListener("keydown", onKey);

  function gaugeRow(label, n, max, hot) {
    return h("div", { class: "xc-gr" }, h("span", { class: "xc-i" }, label), h("span", { class: "xc-gauge" + (hot ? " hot" : "") }, h("i", { style: "width:" + Math.max(n ? 2 : 0, Math.min(100, Math.round(100 * n / max))) + "%" })), h("span", { class: "xc-v" }, fmtTok(n)));
  }
  function instrumentColumn() {
    const mem = h("section", { class: "xc-ins" }, h("div", { class: "xc-label" }, "Memory"));
    if (ctx) {
      const max = Math.max(ctx.total, 1);
      mem.append(h("div", { class: "xc-big" }, h("span", null, fmtTok(ctx.total)), h("span", { class: "xc-i" }, "tokens on the next call")),
        gaugeRow("system", ctx.system, max), gaugeRow("tree", ctx.tree, max), gaugeRow("threads", ctx.notes, max), gaugeRow("tail", ctx.tail, max),
        h("div", { class: "xc-pm" }, "tree \u00B7 " + ctx.summaries + " summaries over " + ctx.nodes + " nodes" + (ctx.pending ? " \u00B7 " + ctx.pending + " pending" : "")),
        h("div", { class: "xc-pm" }, "log \u00B7 " + ctx.log + " entries, folded up to " + ctx.frontier));
    } else mem.append(h("div", { class: "xc-pm" }, ctxTokens != null ? fmtTok(ctxTokens) + " tokens on the last call" : "no report yet"));
    const cl = h("section", { class: "xc-ins" }, h("div", { class: "xc-label" }, "Last calls"));
    if (calls.length) calls.forEach((k) => cl.append(h("div", { class: "xc-call" + (k.error ? " xc-bad" : "") },
      h("span", { class: "xc-v" }, short((k.route || k.requested || "no route").split(":").pop(), 22)),
      h("span", { class: "xc-pm" }, [k.ttft != null ? "ttft " + k.ttft + "ms" : "", k.cache != null ? "cache " + Math.round(k.cache * 100) + "%" : "", k.cost === undefined ? "" : k.cost === null ? "cost unknown" : money(k.cost)].filter(Boolean).join(" \u00B7 ")),
      k.requested && k.route && k.requested !== k.route ? h("span", { class: "xc-via" }, "\u2190 fallback") : null)));
    else cl.append(h("div", { class: "xc-pm" }, "no calls yet in this thread"));
    return [mem, cl];
  }
  function drawSide(onlyInstruments) {
    const mine = panelsOpen.filter((p) => !p.session || p.session === current);
    if (onlyInstruments && mine.length) return;
    sideEl.replaceChildren(...(mine.length ? mine.map((p) => { const n = renderPanel(p, { act: panelAct, dismiss: panelDismiss }); if (!seen.has(p.panel_id)) n.classList.add("xc-in"); return n; }) : instrumentColumn()));
    sideEl.classList.toggle("xc-has", mine.length > 0);
    for (const p of mine) if (!seen.has(p.panel_id)) { seen.add(p.panel_id); respondPanel(p, "opened", {}, true); }
  }

  // ---------------------------------------------------------------- actions

  async function refreshSessions() {
    try { sessions = (await api("GET", "/api/chat/sessions")).sessions; drawSessions(); } catch (e) { fail(e); }
  }

  async function open(id) {
    current = id; loading = true; pending = []; items = []; busy = false; cost = null; requested = ""; ctxTokens = null; cacheShare = null; ctx = null; findings = null; calls = []; costSeen = false; steers = {}; flash("");
    drawSessions(); drawLog(); drawApprovals(); drawSide(); drawSteers();
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
    drawLog(); drawStatus(); loadContext();
    const buffered = pending; pending = [];
    buffered.forEach(handle);
    syncApprovals();
  }

  // The server's list is the truth (it asks the core): replace what we hold, so a card the core no longer has goes (D-045).
  async function syncApprovals() {
    const stamp = lastEvent;
    try {
      const a = await api("GET", "/api/section/approvals");
      const fresh = {};
      for (const x of (a.pending || [])) if (!gone.has(x.request_id)) fresh[x.request_id] = { ...x, session: x.session };
      for (const [id, x] of Object.entries(approvals)) if (!fresh[id] && x.seq > stamp) fresh[id] = x;   // arrived by event while we asked
      approvals = fresh;
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
    const typo = help.typo(text);
    if (typo) {   // D-037: a mistyped command gets a suggestion, not a message to the agent
      flash("unknown command " + text.split(/\s/)[0] + ". did you mean /" + typo.guess + "? It is in the box: Enter runs it.");
      input.value = typo.line; input.setSelectionRange(input.value.length, input.value.length); input.dispatchEvent(new Event("input"));
      return;
    }
    if (/^\/new\s*$/.test(text)) { input.value = ""; help.sent(text); create(); return; }
    const slash = /^\/(context|findings|trace)\b\s*(.*)$/.exec(text);
    if (slash && current) {
      input.value = ""; help.sent(text);
      try {
        const out = await runCommand(slash[1], slash[2]);
        if (slash[1] === "context" && typeof out === "string") { ctx = parseContext(out); ctxTokens = ctx.total || ctxTokens; drawStatus(); drawSide(true); }
        push({ kind: "report", id: "rep" + Date.now(), title: slash[1] + (slash[1] === "context" ? " \u00B7 next call" : ""), text: typeof out === "string" ? out : JSON.stringify(out, null, 1) });
      } catch (e) { fail(e); }
      return;
    }
    try {
      if (!current) {
        const d = await api("POST", "/api/chat/sessions", { title: short(text, 60) });
        await refreshSessions();
        await open(d.session.id);
      }
      input.value = ""; help.sent(text);
      const steering = busy, sid = current;
      const d = await api("POST", "/api/chat/sessions/" + encodeURIComponent(sid) + "/send", { text, mode: steering ? "steer" : "send" });
      if (steering && d && d.message_id && !landed.has(d.message_id)) { steers[d.message_id] = { id: d.message_id, text, session: sid }; drawSteers(); }
      flash("");
    } catch (e) { fail(e); }
  }

  // Queued steers (D-045): a message sent while a turn runs waits for the next tool round. Until it lands the
  // user can pull it back: the x on its chip, or Up in an empty box, puts the text back to edit.
  function lastSteer() { const mine = Object.values(steers).filter((x) => x.session === current); return mine[mine.length - 1] || null; }
  function drawSteers() {
    const mine = Object.values(steers).filter((x) => x.session === current);
    steerEl.replaceChildren(...mine.map((x) => h("div", { class: "xc-steer-chip", "data-steer": x.id },
      h("span", { class: "xc-i" }, "steer queued"), h("span", { class: "xc-text" }, short(x.text, 140)),
      h("button", { class: "xc-btn xc-x", title: "Pull this back into the box (Up in an empty box does the same)", "aria-label": "Cancel queued steer",
        onclick: () => unsteer(x) }, "\u00D7"))));
  }
  async function unsteer(x) {
    try {
      const d = await api("POST", "/api/chat/sessions/" + encodeURIComponent(x.session) + "/unsteer", { message_id: x.id });
      delete steers[x.id]; drawSteers();
      if ((d.cancelled || []).length) { input.value = input.value.trim() ? x.text + "\n" + input.value : x.text; input.focus(); input.dispatchEvent(new Event("input")); }
      else flash("that message already landed");
    } catch (e) { fail(e); }
  }

  async function interrupt() {
    try { await api("POST", "/api/chat/sessions/" + encodeURIComponent(current) + "/interrupt", {}); } catch (e) { fail(e); }
  }

  async function answer(a, decision) {
    try { await api("POST", "/api/approvals/" + encodeURIComponent(a.request_id), { decision }); delete approvals[a.request_id]; gone.add(a.request_id); drawApprovals(); drawSessions(); }
    catch (e) {
      if (e.status === 404) { delete approvals[a.request_id]; gone.add(a.request_id); drawApprovals(); drawSessions(); flash("that approval was already closed"); }   // the core no longer holds it
      else fail(e);
    }
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

  const offBus = bus.on(handle);
  let resumed = 0;
  const offState = bus.onState((st) => { if (st.resumed !== resumed) { resumed = st.resumed; if (current) syncApprovals(); } });
  const off = () => { offBus(); offState(); help.stop(); document.removeEventListener("keydown", onKey); const l = document.getElementById("xc-live"); if (l) l.classList.remove("on"); };
  drawLog(); drawStatus(); drawApprovals(); drawSide();
  refreshSessions().then(() => { if (!current && sessions.length) open(sessions[0].id); });
  return off;
}
