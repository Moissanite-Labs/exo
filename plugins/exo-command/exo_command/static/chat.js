import { desktopPreference, hasDesktopPreferences, saveDesktopPreferences } from "./desktop_prefs.js";
// The chat view: sessions on the left, a terminal-like transcript in the middle with approval cards and a
// composer, and the side panel area on the right. All state comes from /api/chat/* and the /api/events stream.
import { h, api, short, when, partsText, tag, empty } from "./dom.js";
import * as bus from "./bus.js";
import { renderMarkdown } from "./markdown.js";
import { renderPanel } from "./panels.js";
import { attachComposer } from "./composer.js";
import { attachApprovals } from "./approvals.js";
import { getLayout, setLayout, ordered, homeScroll, nearHome, columnOrder, LAYOUTS } from "./layout.js";

import { contextMetrics, mergeTranscript, slashInput, commandUsage, ingressItem } from "./chat_state.js";
import * as permissions from "./permissions.js";

// Page-lifetime intent only: never write prompts to browser storage.
const drafts = new Map();
let lastThread = null;
const draftListeners = new Set();
const changedDraft = (id, restore = false, owner = null) => { for (const f of draftListeners) f(id, restore, owner); };
function draft(id) {
  if (!drafts.has(id)) drafts.set(id, { text: "", start: 0, end: 0, revision: 0, pending: false, failures: [] });
  return drafts.get(id);
}

export function mount(root) {
  let mounted = true, navigation = 0, homeRevision = 0;
  const savedThread = desktopPreference("thread"), savedScroll = desktopPreference("scroll");
  let pendingReading = null, transcriptLoaded = false;
  function saveReading() {
    if (mounted && current && !loading && transcriptLoaded) saveDesktopPreferences({ thread: current, scroll: logEl.scrollHeight > logEl.clientHeight ? Math.min(1, Math.max(0, logEl.scrollTop / (logEl.scrollHeight - logEl.clientHeight))) : 0 });
  }
  let sessions = [], current = null, items = [], busy = false, loadedSeq = 0, loading = false, pending = [];
  let approvals = {}, steers = {}, gone = new Set(), panelsOpen = bus.openPanels(), notice = "", routeNotice = null, model = "", cost = null;
  let capacity = null, capacities = {}, snapshotGeneration = 0, pickerGeneration = 0, changingModel = false;
  let requested = "", ctxTokens = null, cacheShare = null, ctx = null, findings = null, calls = [], costSeen = false;   // instruments from model.call and usage.updated
  const seen = new Set();   // panels already reported as opened
  const landed = new Set();   // user messages the core has added to a conversation
  let layout = desktopPreference("layout") || getLayout();   // "inverse" (box on top, newest first) or "classic"; D-046

  const listEl = h("div", { class: "xc-sessions", id: "xc-conversation-list" });
  let threadQuery = "", threadFilter = "all";
  const searchInput = h("input", { class: "xc-thread-search", id: "xc-thread-query", type: "search", placeholder: "Title, ID or agent",
    "aria-label": "Find a loaded conversation", "aria-controls": "xc-conversation-list", "aria-describedby": "xc-thread-scope",
    oninput: () => { threadQuery = searchInput.value; drawSessions(); } });
  const searchClear = h("button", { class: "xc-btn xc-thread-clear", type: "button", hidden: true,
    "aria-label": "Clear conversation search", onclick: () => { searchInput.value = threadQuery = ""; drawSessions(); searchInput.focus(); } }, "Clear");
  const searchField = h("div", { class: "xc-thread-searchfield" }, h("label", { for: "xc-thread-query" }, "Find a conversation"), searchInput, searchClear);
  const threadFilterInput = h("select", { class: "xc-thread-filter", "aria-label": "Conversation status",
    onchange: () => { threadFilter = threadFilterInput.value; drawSessions(); } },
    h("option", { value: "all" }, "All conversations"), h("option", { value: "working" }, "Working"), h("option", { value: "attention" }, "Needs you"));
  const threadScope = h("p", { class: "xc-thread-scope", id: "xc-thread-scope", role: "status" });
  let renameTarget = null, renameSaving = false, renameActive = true;
  const renameButtons = new Map(), conversationButtons = new Map();
  const renameInput = h("input", { type: "text", "aria-label": "Thread title", autocomplete: "off", maxlength: "200" });
  const renameError = h("div", { role: "alert", class: "xc-rename-error" });
  const renameSave = h("button", { type: "submit", class: "xc-btn xc-primary" }, "Save");
  const renameCancel = h("button", { type: "button", class: "xc-btn", onclick: () => closeRename() }, "Cancel");
  const renameEditor = h("form", { class: "xc-rename-editor", hidden: true, "aria-label": "Rename thread",
    onsubmit: (e) => { e.preventDefault(); saveRename(); },
    onkeydown: (e) => { if (e.key === "Escape") { e.preventDefault(); closeRename(); } } },
    h("label", null, "Thread title", renameInput), renameError, h("div", { class: "xc-btnrow" }, renameSave, renameCancel));
  function beginRename(s) {
    if (renameSaving) return;
    renameTarget = s.id; renameInput.value = s.title || ""; renameError.textContent = "";
    renameEditor.hidden = false; renameInput.focus(); renameInput.select();
  }
  function closeRename() {
    if (renameSaving) return;
    const id = renameTarget; renameTarget = null; renameEditor.hidden = true; renameInput.value = "";
    renameError.textContent = ""; (renameButtons.get(id) || searchInput).focus();
  }
  async function saveRename() {
    if (!renameTarget || renameSaving) return;
    const title = renameInput.value.trim(), id = renameTarget;
    if (!title) { renameError.textContent = "Enter a thread title."; renameInput.focus(); return; }
    renameSaving = true; renameInput.disabled = renameSave.disabled = renameCancel.disabled = true;
    renameError.textContent = "Saving…";
    try {
      await api("POST", "/api/chat/sessions/" + encodeURIComponent(id) + "/rename", { title });
      if (!renameActive) return;
      sessions = sessions.map((s) => s.id === id ? { ...s, title } : s); drawSessions();
      renameSaving = false; closeRename();
    } catch (e) {
      if (renameActive) renameError.textContent = e && e.message ? e.message : "Could not rename this thread.";
    } finally {
      renameSaving = false; renameInput.disabled = renameSave.disabled = renameCancel.disabled = false;
      if (renameActive && !renameEditor.hidden) renameInput.focus();
    }
  }
  const logEl = h("div", { class: "xc-log", id: "xc-transcript", role: "log", "aria-label": "Conversation messages", "aria-live": "polite", tabindex: "0" });
  const latestBtn = h("button", { class: "xc-btn xc-latest", type: "button", hidden: true, "aria-controls": "xc-transcript",
    onclick: () => { home(); logEl.focus(); } }, "Show latest messages");
  for (const kind of ["wheel", "touchstart", "pointerdown", "keydown"]) logEl.addEventListener(kind, () => { pendingReading = null; }, { passive: true });
  logEl.addEventListener("scroll", () => { ++homeRevision; updateLatest(); saveReading(); });
  const apprEl = h("div", { class: "xc-approvals" });
  const steerEl = h("div", { class: "xc-steers", "aria-label": "Queued steer messages" });
  const statusEl = h("div", { class: "xc-strip", "aria-label": "Status" });
  const sideEl = h("aside", { class: "xc-side" });
  const input = h("textarea", { class: "xc-input", rows: "2", placeholder: "Ask, or give a task. Enter sends, Shift+Enter adds a line.", "aria-label": "Message" });
  const recoveryEl = h("div", { class: "xc-draft-recovery", "aria-live": "polite" });
  const sendBtn = h("button", { class: "xc-btn xc-primary", onclick: () => send() }, "Send");
  const stopBtn = h("button", { class: "xc-btn", onclick: () => interrupt() }, "Stop");
  const promptEl = h("span", { class: "xc-cp", "aria-hidden": "true" }, "\u203A");
  const modelPicker = h("div", { class: "xc-model-picker", hidden: true, "aria-label": "Model picker" });
  modelPicker.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); closePicker(); } });
  const inputWrap = h("div", { class: "xc-inwrap" }, input);
  const composer = h("div", { class: "xc-composer" }, promptEl, inputWrap, h("div", { class: "xc-btnrow" }, sendBtn, stopBtn), recoveryEl, modelPicker);
  const help = attachComposer({ input, composer, getSession: () => current,
    reducedMotion: window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches });
  function saveDraft() {
    const d = draft(current);
    if (d.text !== input.value) d.revision++;
    Object.assign(d, { text: input.value, start: input.selectionStart, end: input.selectionEnd });
  }
  function restoreDraft() {
    const d = draft(current);
    input.value = d.text; input.setSelectionRange(d.start, d.end);
    if (help.restore) help.restore();
    drawDraft();
  }
  function drawDraft() {
    const d = draft(current);
    sendBtn.disabled = d.pending;
    sendBtn.textContent = d.pending ? "Sending…" : "Send";
    recoveryEl.replaceChildren(...(d.pending ? [h("span", null, "Sending. You can keep writing.")] : []),
      ...d.failures.map(f => h("div", { class: "xc-draft-failed" },
        h("span", null, "Send was not confirmed: " + f.error + ". Check the thread before sending again."),
        h("button", { class: "xc-btn", onclick: () => {
          saveDraft();
          if (!f.restored) { d.text = d.text ? f.text + "\n" + d.text : f.text; d.start = d.end = d.text.length; d.revision++; }
          d.failures = d.failures.filter(x => x !== f); restoreDraft(); input.focus();
        } }, f.restored ? "Keep editing" : "Restore text"),
        h("button", { class: "xc-btn", onclick: () => { d.failures = d.failures.filter(x => x !== f); drawDraft(); } }, "Dismiss"))));
  }
  const changed = (id, restore, owner) => {
    if (current !== id) return;
    if (owner) { input.value = ""; open(owner); refreshSessions(); }
    else if (restore) restoreDraft();
    else drawDraft();
  };
  draftListeners.add(changed);
  input.addEventListener("input", saveDraft);
  input.addEventListener("select", saveDraft);
  input.addEventListener("keyup", saveDraft);
  const approvalView = attachApprovals({ root: apprEl, composer: input, answer });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
    if (e.key === "ArrowUp" && !input.value && lastSteer() && !e.defaultPrevented) { e.preventDefault(); e.stopImmediatePropagation(); unsteer(lastSteer()); }
  }, true);
  const newBtn = h("button", { class: "xc-btn xc-new-thread", onclick: () => create() }, "New thread");
  const layoutBtn = h("button", { class: "xc-btn xc-layout", onclick: () => switchLayout(layout === "inverse" ? "classic" : "inverse") });
  const heroText = h("div", { class: "xc-hero-text" });
  const heroEl = h("div", { class: "xc-hero" }, heroText);
  const stage = h("div", { class: "xc-stage" }, heroEl, logEl, latestBtn);
  const setupBanner = h("div", { class: "xc-setup", hidden: true }, h("span", null, "No model provider is set up yet."),
    h("a", { href: "#providers" }, "Set up providers"));
  const mainEl = h("section", { class: "xc-chat-main", "aria-label": "Conversation" });
  const parts = { banner: setupBanner, stage, approvals: apprEl, status: statusEl, steer: steerEl, guide: help.guideEl, composer, menu: help.menuEl };
  function arrange() {   // the real DOM order, so the tab order matches what is on screen
    mainEl.replaceChildren(...columnOrder(layout).map((k) => parts[k]));
    mainEl.classList.toggle("xc-inverse", layout === "inverse");
    layoutBtn.textContent = layout === "inverse" ? "Layout: newest on top" : "Layout: classic";
    layoutBtn.title = "Switch the chat layout (also /layout inverse|classic). Saved in this browser.";
    layoutBtn.setAttribute("aria-pressed", layout === "inverse" ? "true" : "false");
  }
  function switchLayout(v) {
    if (!LAYOUTS.includes(v)) return;
    pendingReading = null;
    layout = v; setLayout(v); saveDesktopPreferences({ layout: v, scroll: null }); arrange(); drawLog();
  }
  arrange();
  const narrowThreads = window.matchMedia ? window.matchMedia("(max-width: 760px)") : null;
  const mobileThreads = () => !!(narrowThreads && narrowThreads.matches);
  const conversationTitle = h("span", { class: "xc-conversation-current" });
  const conversationSummary = h("summary", { class: "xc-conversation-summary" }, h("span", null, "Conversations"), conversationTitle);
  const threadDetails = h("details", { class: "xc-conversations", open: !mobileThreads() }, conversationSummary,
    searchField, threadFilterInput, threadScope, renameEditor, listEl, layoutBtn);
  function adaptThreadChooser() {
    if (!mobileThreads()) threadDetails.open = true;
    else if (!threadDetails.contains(document.activeElement)) threadDetails.open = false;
  }
  threadDetails.addEventListener("toggle", () => { if (!mobileThreads() && !threadDetails.open) threadDetails.open = true; });
  if (narrowThreads && narrowThreads.addEventListener) narrowThreads.addEventListener("change", adaptThreadChooser);
  root.replaceChildren(h("div", { class: "xc-chat" },
    h("nav", { class: "xc-chat-left", "aria-label": "Conversations" }, newBtn, threadDetails), mainEl, sideEl));

  if (sessionStorage.getItem("xc-token")) {   // quiet check: skipped on any error
    api("GET", "/api/providers").then((d) => { if (d && d.ready === false) setupBanner.hidden = false; }).catch(() => {});
  }

  // ---------------------------------------------------------------- items

  function flash(msg) { notice = msg || ""; routeNotice = null; drawStatus(); }
  function fail(e) { flash(e && e.message ? e.message : String(e)); }

  // A context note (meta.note: other threads' news) is for the model, not a message the user typed (D-028), so the
  // transcript leaves it out, as the terminal client does.
  function isNote(m) { return !!(m && m.meta && m.meta.note); }

  function fromMessages(msgs) {
    for (const m of msgs) if (m.role === "user" && m.id) { landed.add(m.id); delete steers[m.id]; }
    drawSteers();
    const out = [];
    const byCall = {};
    for (const m of msgs) {
      if (isNote(m)) continue;
      if (m.role === "user" || m.role === "assistant") {
        const text = partsText(m.content);
        if (text || m.role === "user") out.push(ingressItem(m) || { kind: m.role, id: m.id || "m" + out.length, text, complete: true });
        for (const c of m.tool_calls || []) {
          const it = { kind: "tool", id: c.id, name: c.name, args: c.arguments || {}, output: "", status: "running" };
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
  // One card per peer, bus or job message: peer.message and the stored message.completed share its msg_id and come
  // in either order. The second adds what the first lacked: the stored kind (Bus, Job) or the delivery mode.
  function card(it) {
    const old = find(it.id);
    if (!old) { push(it); return; }
    old.label = old.label || it.label; old.mode = old.mode || it.mode; redraw(old);
  }

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
    switch (ev.type) {
      case "steer.cancelled": if (steers[b.message_id]) { delete steers[b.message_id]; drawSteers(); } return;
      case "session.created": case "session.updated": refreshSessions(); return;
      case "approval.requested": if (gone.has(b.request_id)) return; approvals[b.request_id] = { ...b, session: ev.session, seq: ev.seq }; drawApprovals(); drawSessions(); return;
      case "approval.resolved": gone.add(b.request_id); delete approvals[b.request_id]; drawApprovals(); drawSessions(); return;
      case "ui.panel": panelsOpen = bus.openPanels(); drawSide(); return;
      case "notice":
        if (!mine) return;
        if (mine && /^tip\b/i.test(b.text || "")) { push({ kind: "sys", id: "tip" + ev.seq, tone: "tip", text: b.text.replace(/^tip:?\s*/i, "") }); return; }
        flash(b.text);
        if (b.source === "model_route" && b.call_id) routeNotice = b.call_id;
        return;
      default: break;
    }
    if (!mine) return;
    switch (ev.type) {
      case "turn.started": busy = true; help.note({ busy: true }); if (b.model) { model = b.model; requested = b.model; capacity = capacities[model] || null; } drawStatus(); break;
      case "turn.completed": {
        if (routeNotice) flash("");
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
        if (routeNotice && b.call_id === routeNotice && !b.error && b.stop_reason !== "interrupted") flash("");
        // The error and its attempts, so the tip can tell a refusal from a transport failure (#756, #855).
        help.note({ providerError: b.error || "", providerAttempts: b.error && Array.isArray(b.attempts) ? b.attempts : [] });
        if (b.route) { model = b.route; capacity = capacities[model] || null; }
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
      case "peer.message": card({ kind: "peer", id: b.msg_id || "peer" + items.length, sender: b.sender, text: b.text, trust: b.trust, mode: b.mode }); break;
      case "taste.updated": push({ kind: "taste", id: "taste-" + b.rule_id + "-" + ev.seq, action: b.action, text: b.text, path: b.path }); break;
      case "message.delta": delta(b); break;
      case "message.completed": { const it = ingressItem(b.message); if (it) card(it); else completed(b.message || {}); break; }
      case "tool.started": if (!find(b.call.id)) push({ kind: "tool", id: b.call.id, name: b.call.name, args: b.call.arguments || {}, output: "", status: "running", risk: b.risk }); break;
      case "tool.output": { const t = find(b.call_id); if (t && t.status === "running") { t.output += b.text; redraw(t); } break; }
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
    if (it.complete) return;
    it.text += b.text;
    if (it.textEl) keepReading(() => { it.textEl.textContent = it.text; }, aboveViewport(it.el)); else redraw(it);
  }

  function completed(m) {
    if (isNote(m)) return;
    if (m.role === "user") { if (!find(m.id)) push({ kind: "user", id: m.id, text: partsText(m.content) }); return; }
    if (m.role === "assistant") {
      const text = partsText(m.content);
      const it = find(m.id);
      if (it) {
        const keep = nearHome(layout, logEl.scrollTop, logEl.scrollHeight, logEl.clientHeight);   // judged before the redraw: a rendered table is taller than its streamed text
        it.text = text || it.text; it.streaming = false; it.complete = true; redraw(it);
        if (keep) { home(); scheduleHome(); }
      }
      else if (text) push({ kind: "assistant", id: m.id, text, complete: true });
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
  function applyContext(out) {
    const metrics = contextMetrics(out);
    if (!metrics) return;
    ctx = metrics; ctxTokens = ctx.total; capacity = ctx.capacity;
    capacities[ctx.model] = ctx.capacity;
    drawStatus(); drawSide(true);
  }
  async function loadContext() {
    const id = current;
    if (!id) return;
    try {
      const out = await runCommand("context", "json");
      if (id !== current) return;
      applyContext(out);
      drawStatus(); drawSide(true); if (!items.length) drawHero();
    } catch (e) { /* older core: the instruments fall back to what the events carry */ }
    try {
      const out = await runCommand("findings", "count");
      if (id === current && out && typeof out.open === "number") { findings = out.open; help.note({ findings }); drawStatus(); }
    } catch (e) { /* no findings count */ }
  }
  function sys(label, text, tone) { push({ kind: "sys", id: "sys" + items.length + "-" + Date.now(), label, text, tone }); }

  // ---------------------------------------------------------------- drawing

  function updateLatest() {
    latestBtn.hidden = !items.length || nearHome(layout, logEl.scrollTop, logEl.scrollHeight, logEl.clientHeight);
  }
  function home() { ++homeRevision; logEl.scrollTop = homeScroll(layout, logEl.scrollHeight, logEl.clientHeight); updateLatest(); }
  function scheduleHome() {
    const revision = homeRevision, owner = navigation, session = current, mode = layout, top = logEl.scrollTop;
    requestAnimationFrame(() => {
      if (mounted && revision === homeRevision && owner === navigation && session === current && mode === layout && top === logEl.scrollTop) home();
    });
  }
  function aboveViewport(element) {
    return element && element.getBoundingClientRect && logEl.getBoundingClientRect
      ? element.getBoundingClientRect().bottom <= logEl.getBoundingClientRect().top : layout === "inverse";
  }
  function keepReading(change, above = false) {
    const keep = nearHome(layout, logEl.scrollTop, logEl.scrollHeight, logEl.clientHeight);
    const top = logEl.scrollTop, height = logEl.scrollHeight;
    change();
    if (keep) home();
    else { ++homeRevision; logEl.scrollTop = top + (above ? logEl.scrollHeight - height : 0); }
    updateLatest();
  }
  function push(it) {
    it.fresh = true;
    items.push(it);
    if (items.length === 1) { drawLog(); return; }
    keepReading(() => {
      if (layout === "inverse") logEl.prepend(itemEl(it)); else logEl.appendChild(itemEl(it));
    }, layout === "inverse");
  }
  function drawHero() {
    const want = items.length === 0;
    heroEl.hidden = !want;
    if (want) {
      const chip = (key, label) => h("span", { class: "xc-chipk" }, h("b", null, key), label);
      heroText.replaceChildren(
        h("h2", { class: "xc-hero-title" }, "What are we working on?"),
        h("div", { class: "xc-hero-sub" }, "Describe the outcome, ask a question, or bring a task."),
        h("div", { class: "xc-chips" }, chip("Enter", "send"), chip("Shift+Enter", "new line"), chip("/", "commands")));
    }
  }
  function redraw(it) { const old = it.el; if (old && old.isConnected) { keepReading(() => old.replaceWith(itemEl(it)), aboveViewport(old)); } else drawLog(); }   // keep the old node: itemEl points it.el at the new one
  function drawLog() {
    logEl.replaceChildren(...ordered(items, layout).map(itemEl));
    drawHero();
    home();
    scheduleHome();   // finish initial positioning only if no newer reading, layout or navigation intent won
  }

  function itemEl(it) {
    let el;
    const fresh = it.fresh ? " xc-in" : "";
    it.fresh = false;
    if (it.kind === "user") {
      el = h("div", { class: "xc-msg xc-user" + fresh }, h("span", { class: "xc-prompt" }, "\u203A"), h("span", { class: "xc-text" }, it.text));
    } else if (it.kind === "assistant") {
      it.textEl = h("div", { class: "xc-text" }, it.streaming ? it.text : markdown(it.text));
      el = h("div", { class: "xc-msg xc-asst" + (it.streaming ? " xc-streaming" : "") + fresh }, it.textEl, it.streaming ? null : feedbackBar(it));
    } else if (it.kind === "sys") {
      el = h("div", { class: "xc-msg xc-sysline" + (it.tone === "tip" ? " xc-tip" : "") + fresh },
        it.tone === "tip" ? "tip: " + it.text : [h("span", { class: "xc-k" }, it.label), " \u00B7 " + it.text]);
    } else if (it.kind === "report") {
      el = h("section", { class: "xc-msg xc-pn" + fresh }, h("div", { class: "xc-ph" }, it.title), h("pre", { class: "xc-report" }, it.text));
    } else if (it.kind === "peer") {
      el = h("section", { class: "xc-msg xc-pn xc-peer" + fresh },
        h("div", { class: "xc-ph" }, (it.label || "Peer") + " \u00B7 " + (it.sender || "unknown"), it.trust ? tag(it.trust) : null, it.mode ? tag(it.mode) : null),
        h("div", { class: "xc-pb xc-text" }, it.text),
        h("div", { class: "xc-pm" }, it.label === "Bus" ? "from another agent seat, not from you" : it.label === "Job" ? "an automatic notice about a background job, not from you" : "from another session, not from you"));
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
      const pct = capacity ? Math.min(100, Math.round(100 * ctxTokens / capacity)) : 0;
      out.push(h("span", { class: "xc-opt" }, h("span", { class: "xc-i" }, "memory"),
        h("span", { class: "xc-gauge" + (pct > 80 ? " hot" : ""), title: capacity ? "context capacity: " + fmtTok(capacity) + " tokens" : "context capacity unknown" }, h("i", { style: "width:" + pct + "%" })),
        h("span", { class: "xc-v" }, fmtTok(ctxTokens) + (capacity ? " / " + fmtTok(capacity) : " · capacity unknown"))));
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
    const pendingApprovals = Object.values(approvals);
    const waiting = new Set(pendingApprovals.map(a => a.session));
    const globalWaiting = pendingApprovals.some(a => !a.session);
    const focused = document.activeElement;
    const focusedConversation = [...conversationButtons].find(([, button]) => button === focused);
    const focusedRename = [...renameButtons].find(([, button]) => button === focused);
    const query = threadQuery.trim().toLowerCase();
    const shown = sessions.filter(s => (!query || [s.title, s.id, s.agent, s.seat].some(value => String(value || "").toLowerCase().includes(query)))
      && (threadFilter === "all" || (threadFilter === "working" ? s.status === "live" : globalWaiting || waiting.has(s.id))));
    searchClear.hidden = !threadQuery;
    threadScope.textContent = shown.length + " of " + sessions.length + " loaded conversations";
    renameButtons.clear(); conversationButtons.clear();
    listEl.replaceChildren(...(shown.length ? shown.map((s) => {
      const button = h("button", { class: "xc-btn xc-rename", type: "button", "aria-label": "Rename " + (s.title || s.id), onclick: () => beginRename(s) }, "Rename");
      renameButtons.set(s.id, button);
      const conversationButton = h("button", { class: "xc-sess" + (s.id === current ? " on" : ""), onclick: () => chooseConversation(s.id),
        title: "Open conversation", "aria-current": s.id === current ? "true" : null,
        ondblclick: () => beginRename(s) },
        h("span", { class: "xc-sess-title" }, short(s.title || s.id, 40)),
        h("span", { class: "xc-dim" }, [s.status === "live" ? "running" : "", globalWaiting || waiting.has(s.id) ? h("span", { class: "xc-needs" }, "needs you") : "", when(s.updated)].filter(Boolean).flatMap((x, i) => (i ? [" \u00B7 ", x] : [x]))));
      conversationButtons.set(s.id, conversationButton);
      return h("div", { class: "xc-session-row" }, conversationButton, button);
    }) : [h("div", { class: "xc-thread-empty" }, h("p", null, sessions.length ? "No loaded conversations match." : "No conversations yet."),
      sessions.length ? h("button", { class: "xc-btn", onclick: () => {
        searchInput.value = threadQuery = ""; threadFilterInput.value = threadFilter = "all"; drawSessions(); searchInput.focus();
      } }, "Show all conversations") : null)]));
    if (focusedConversation) (conversationButtons.get(focusedConversation[0]) || searchInput).focus({ preventScroll: true });
    else if (focusedRename) (renameButtons.get(focusedRename[0]) || searchInput).focus({ preventScroll: true });
    const selected = sessions.find(s => s.id === current);
    conversationTitle.textContent = selected ? short(selected.title || selected.id, 40) : "Choose a conversation";
    drawWhere();
  }

  function drawApprovals() {
    const mine = Object.values(approvals).filter((a) => !a.session || a.session === current);   // no session: every thread, as in the terminal client
    approvalView.draw(mine);
  }

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

  // Session lists can answer out of order: one asked before a thread was created must not replace one asked after it.
  let listAsked = 0, listShown = 0;
  async function refreshSessions() {
    const asked = ++listAsked;
    try {
      const fresh = (await api("GET", "/api/chat/sessions")).sessions;
      if (asked > listShown) { listShown = asked; sessions = fresh; drawSessions(); }
    } catch (e) { fail(e); }
  }

  function chooseConversation(id) {
    const opened = open(id);  // restore the chosen draft synchronously; loading must never steal focus later
    if (mobileThreads()) {
      threadDetails.open = false;
      input.focus({ preventScroll: true });
    }
    return opened;
  }

  async function open(id, restoreScroll = null) {
    saveReading();
    if (typeof restoreScroll !== "number") saveDesktopPreferences({ thread: id, scroll: null });
    transcriptLoaded = false;
    saveDraft(); ++navigation; lastThread = id;
    pendingReading = typeof restoreScroll === "number" ? { id, fraction: restoreScroll, navigation } : null;
    modelPicker.hidden = true; ++pickerGeneration;
    modelPicker.replaceChildren();
    const generation = ++snapshotGeneration, had = Object.keys(steers);
    capacity = null;
    current = id; loading = true; pending = []; items = []; busy = false; cost = null; requested = ""; ctxTokens = null; cacheShare = null; ctx = null; findings = null; calls = []; costSeen = false; flash("");
    restoreDraft();
    drawSessions(); drawLog(); drawApprovals(); drawSide(); drawSteers();
    try {
      const d = await api("GET", "/api/chat/sessions/" + encodeURIComponent(id));
      if (current !== id || generation !== snapshotGeneration) return;
      items = fromMessages(d.messages || []);
      transcriptLoaded = true;
      dropSteers(id, had, d.steers);
      loadedSeq = d.seq || 0;
      const live = sessions.find((s) => s.id === id);
      busy = !!(live && live.status === "live");
      model = (d.session && d.session.model) || "";
    } catch (e) {
      if (!mounted || current !== id || generation !== snapshotGeneration) return;
      fail(e);
    }
    if (!mounted || current !== id || generation !== snapshotGeneration) return;
    loading = false;
    drawLog(); drawStatus(); loadContext();
    const buffered = pending; pending = [];
    buffered.forEach(handle);
    syncApprovals();
    restoreReading(id, generation);
  }

  function restoreReading(id, generation) {
    if (!pendingReading || !transcriptLoaded || !mounted || current !== id || pendingReading.id !== id || pendingReading.navigation !== navigation || snapshotGeneration !== generation) return;
    ++homeRevision;
    logEl.scrollTop = pendingReading.fraction * Math.max(0, logEl.scrollHeight - logEl.clientHeight);
    pendingReading = null;
    updateLatest(); saveReading();
  }

  async function recover() {
    const id = current, generation = ++snapshotGeneration, cutoff = bus.lastSeq(), had = Object.keys(steers), asked = ++listAsked;
    loading = true;
    try {
      const freshSessions = (await api("GET", "/api/chat/sessions")).sessions || [];
      const d = id ? await api("GET", "/api/chat/sessions/" + encodeURIComponent(id)) : null;
      if (id !== current || generation !== snapshotGeneration) return;
      if (asked > listShown) { listShown = asked; sessions = freshSessions; }
      if (d) {
        items = mergeTranscript(fromMessages(d.messages || []), items);
        transcriptLoaded = true;
        dropSteers(id, had, d.steers);
        loadedSeq = Math.min(cutoff, d.seq || 0);
        busy = sessions.some((s) => s.id === id && s.status === "live");
        model = d.session.model || model;
      }
      panelsOpen = bus.openPanels();
    } catch (e) { fail(e); }
    finally {
      if (id === current && generation === snapshotGeneration) {
        const reading = hasDesktopPreferences() && !pendingReading && !nearHome(layout, logEl.scrollTop, logEl.scrollHeight, logEl.clientHeight) ? logEl.scrollTop : null;
        loading = false; drawSessions(); drawLog(); drawStatus(); drawSide();
        const buffered = pending; pending = []; buffered.forEach(handle);
        syncApprovals(); loadContext();
        if (reading !== null) { ++homeRevision; logEl.scrollTop = reading; updateLatest(); saveReading(); }
        restoreReading(id, generation);
      }
    }
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

  // D-069: rename is command.run "rename" on the core (principal only); the session.updated event redraws the list
  async function rename(id, title) {
    title = (title || "").trim();
    if (!id || !title) return;
    try {
      await api("POST", "/api/chat/sessions/" + encodeURIComponent(id) + "/rename", { title });
      await refreshSessions();
    } catch (e) { fail(e); }
  }

  async function create(kind = "") {
    saveDraft(); const nav = ++navigation;
    try {
      const d = await api("POST", "/api/chat/sessions", { kind });
      await refreshSessions();
      if (d.session && mounted && nav === navigation) chooseConversation(d.session.id);
    } catch (e) { fail(e); }
  }

  function closePicker() { modelPicker.hidden = true; ++pickerGeneration; input.focus(); }

  // One model change at a time: send() and the picker wait for it, so a second Enter is not a second models.set.
  async function chooseModel(ref, defaults, sid) {
    changingModel = true;
    try {
      if (defaults) await api("POST", "/api/providers/default", { ref, role: "main" });
      else await api("POST", "/api/chat/sessions/" + encodeURIComponent(sid) + "/model", { model: ref });
    } catch (e) {   // D-034: provider settings answer the loopback page only; /model still works from here
      const lan = defaults && e.status === 403 && !["127.0.0.1", "localhost", "[::1]"].includes(location.hostname);
      throw lan ? Error("/default saves provider settings, which only a page opened at 127.0.0.1 or localhost may change (D-034). /model works here.") : e;
    } finally { changingModel = false; }
    if (!defaults && current === sid) { model = ref; capacity = capacities[ref] || null; requested = ""; drawStatus(); }
    closePicker();
    await refreshSessions();
    flash((defaults ? "Default model: " : "Main model: ") + ref);
  }

  async function modelCommand(args, defaults = false) {
    const sid = current, generation = ++pickerGeneration;   // a later /model or /default replaces a slow list
    if (!defaults && !sid) { flash("open a thread first for /model; /default sets the model for new threads"); return; }
    try {
      if (args) { await chooseModel(args, defaults, sid); return true; }
      const d = await api("GET", "/api/chat/models");
      if (sid !== current || generation !== pickerGeneration) return;
      const rows = [...(Array.isArray(d.models) ? d.models : []), ...(Array.isArray(d.available) ? d.available : [])];
      const routes = [...new Set(rows.map(m => m && m.route).filter(ref => typeof ref === "string" && ref))];
      if (!routes.length) { flash("No models listed. Open Providers to set up a provider."); return; }
      const select = h("select", { class: "xc-field", "aria-label": defaults ? "Default model" : "Main model" },
        ...routes.map(ref => h("option", { value: ref, selected: ref === model }, ref)));
      select.value = routes.includes(model) ? model : routes[0];
      const apply = h("button", { class: "xc-btn", onclick: async () => {
        if (sid !== current) { modelPicker.hidden = true; return; }
        if (changingModel) return;
        apply.disabled = true;
        try { await chooseModel(select.value, defaults, sid); } catch (e) { fail(e); }
        finally { apply.disabled = false; }
      } }, defaults ? "Set default" : "Use model");
      modelPicker.replaceChildren(h("label", null, defaults ? "Default model for new threads" : "Main model", select),
        h("p", null, "Changes the shared main route; existing fallbacks remain."), apply,
        h("button", { class: "xc-btn", onclick: closePicker }, "Cancel"));
      modelPicker.hidden = false;
      select.focus();
      return true;
    } catch (e) { if (args || generation === pickerGeneration) fail(e); return false; }   // a newer picker owns the strip
  }

  async function send() {
    const text = input.value.trim();
    if (!text || draft(current).pending) return;
    if (changingModel) { flash("changing the model; send again when it is done"); return; }
    const command = slashInput(text);
    // Without the command names a /name line could be a command or a typo of one: kept until they load (#146).
    if (command && !help.ready(command.name)) { flash("commands are still loading; the line is in the box, send it again in a moment"); return; }
    const typo = command && help.typo(text);
    if (typo && typo.here) {   // D-037: a mistyped command gets a suggestion, not a message to the agent
      flash("unknown command " + text.split(/\s/)[0] + ". did you mean /" + typo.guess + "? It is in the box: Enter runs it.");
      input.value = typo.line; input.setSelectionRange(input.value.length, input.value.length); input.dispatchEvent(new Event("input"));
      return;
    }
    const where = typo ? help.elsewhere(typo.guess) : command ? help.elsewhere(command.name) : "";
    if (where) {   // a core or terminal-client command this page does not run, or a near typo of one (#146): kept
      flash((typo ? "unknown command " + text.split(/\s/)[0] + ". did you mean /" + typo.guess + "? It" : "/" + command.name) +
        (where === "core" ? " runs in exo tui or exo chat" : " runs in the terminal client (exo tui)") +
        ", not here. Type / to see what runs here, or reword the line to send it as a message.");
      return;
    }
    if (command && commandUsage(command)) { flash(commandUsage(command)); return; }
    if (command && ["model", "default"].includes(command.name)) {
      let args = command.args, defaults = command.name === "default";
      if (command.name === "model" && /^default(?:\s|$)/.test(args)) { defaults = true; args = args.slice(7).trim(); }
      if (args && (args.startsWith("-") || /\s/.test(args))) { flash("usage: /model [provider:model] or /model default [provider:model]"); return; }
      if (await modelCommand(args, defaults)) { if (input.value.trim() === text) input.value = ""; help.sent(text); }
      return;
    }
    const lay = command && command.name === "layout" ? [null, command.args] : null;
    if (lay) {
      input.value = ""; help.sent(text);
      if (!lay[1]) flash("layout: " + layout + ". /layout inverse puts the box on top with the newest message under it; /layout classic is the usual order.");
      else if (LAYOUTS.includes(lay[1].toLowerCase())) { switchLayout(lay[1].toLowerCase()); flash("layout: " + layout + " (saved)"); }
      else flash("usage: /layout inverse|classic");
      return;
    }
    if (command && command.name === "rename") {
      if (!current) { flash("open a thread first for /rename"); return; }
      input.value = ""; help.sent(text); rename(current, command.args); return;
    }
    if (command && command.name === "new") { input.value = ""; help.sent(text); create(command.args); return; }
    if (command && command.name === "permissions") {   // D-065: the mode lives in the core; the banner follows it
      input.value = ""; help.sent(text);
      const mode = command.args === "status" ? "" : command.args;
      try {
        const out = mode ? await permissions.set(mode) : permissions.describe(await permissions.refresh());
        push({ kind: "report", id: "rep" + Date.now(), title: "permissions", text: typeof out === "string" ? out : permissions.describe(permissions.current()) });
      } catch (e) { fail(e); }
      return;
    }
    const slash = command && ["context", "findings", "trace"].includes(command.name) ? [null, command.name, command.args] : null;
    if (slash) {
      if (!current) { flash("open a thread first for /" + slash[1]); return; }
      input.value = ""; help.sent(text);
      try {
        const out = await runCommand(slash[1], slash[1] === "context" ? "json" : slash[2]);
        if (slash[1] === "context") applyContext(out);
        push({ kind: "report", id: "rep" + Date.now(), title: slash[1] + (slash[1] === "context" ? " \u00B7 next call" : ""), text: typeof out === "string" ? out : (out && out.text) || JSON.stringify(out, null, 1) });
      } catch (e) { fail(e); }
      return;
    }
    saveDraft();
    let origin = current, nav = navigation;
    const steering = busy, intent = draft(origin);
    intent.pending = true;
    input.value = ""; saveDraft(); help.sent(text); drawDraft();
    const revision = intent.revision;
    let sid = origin;
    try {
      if (!sid) {
        const d = await api("POST", "/api/chat/sessions", { title: short(text, 60) });
        sid = d.session.id;
        // Once created, this conversation owns both the request and anything typed behind it.
        if (mounted && current === origin) saveDraft();
        drafts.set(sid, intent); drafts.delete(origin);
        const show = mounted && nav === navigation && current === origin;
        // A remounted composer can still own the null slot while this old request finishes.
        changedDraft(origin, false, sid);
        origin = sid;
        if (show && current === sid) nav = navigation;
        await refreshSessions();
      }
      const d = await api("POST", "/api/chat/sessions/" + encodeURIComponent(sid) + "/send", { text, mode: steering ? "steer" : "send" });
      intent.pending = false; changedDraft(origin);
      if (steering && d && d.message_id && !landed.has(d.message_id)) { steers[d.message_id] = { id: d.message_id, text, session: sid }; if (mounted) drawSteers(); }
      if (mounted && nav === navigation && current === origin) {
        if (!origin) await open(sid);
        else { drawDraft(); flash(""); }
      }
    } catch (e) {
      if (mounted && current === origin) saveDraft();
      intent.pending = false;
      const restored = intent.revision === revision && !intent.text;
      if (restored) Object.assign(intent, { text, start: text.length, end: text.length });
      intent.failures.push({ text, restored, error: e && e.message ? e.message : String(e) });
      changedDraft(origin, true);
    }
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
  // A chip the core no longer queues (a restart lost the message, D-045) goes when the thread is read again. Only a
  // chip drawn before the read was asked for can be judged by it; a core without the list leaves them all.
  function dropSteers(sid, had, queued) {
    if (!Array.isArray(queued)) return;
    for (const k of had) if (steers[k] && steers[k].session === sid && !queued.includes(k)) delete steers[k];
    drawSteers();
  }
  async function unsteer(x) {
    try {
      const d = await api("POST", "/api/chat/sessions/" + encodeURIComponent(x.session) + "/unsteer", { message_id: x.id });
      delete steers[x.id]; drawSteers();
      if ((d.cancelled || []).length) {
        if (mounted && current === x.session) saveDraft();
        const target = draft(x.session);
        target.text = target.text.trim() ? x.text + "\n" + target.text : x.text;
        target.start = target.end = target.text.length; target.revision++;
        changedDraft(x.session, true);
        if (mounted && current === x.session) input.focus();
      } else if (mounted && current === x.session) flash("no longer queued");
    } catch (e) { fail(e); }
  }

  async function interrupt() {
    try { await api("POST", "/api/chat/sessions/" + encodeURIComponent(current) + "/interrupt", {}); } catch (e) { fail(e); }
  }

  async function answer(a, decision, note = "") {
    try { await api("POST", "/api/approvals/" + encodeURIComponent(a.request_id), { decision, note }); delete approvals[a.request_id]; gone.add(a.request_id); drawApprovals(); drawSessions(); }
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
    bus.forget(p.panel_id);   // the core sends no ui.panel close for this, so the next panel event or tab would bring it back
    panelsOpen = panelsOpen.filter((x) => x.panel_id !== p.panel_id);
    drawSide();
  }

  // ---------------------------------------------------------------- start

  api("GET", "/api/providers/models").then((d) => {
    for (const m of d.models || []) if (m.route) capacities[m.route] = m.context || null;
    capacity = capacities[model] || capacity; drawStatus();
  }).catch(() => {});
  const offBus = bus.on(handle);
  let synced = bus.lastSeq() ? -1 : 0;
  const offState = bus.onState((st) => {
    if (st.synced !== synced) { synced = st.synced; recover(); }
  });
  const off = () => { saveReading(); if (hasDesktopPreferences()) window.removeEventListener("pagehide", saveReading); if (narrowThreads && narrowThreads.removeEventListener) narrowThreads.removeEventListener("change", adaptThreadChooser); saveDraft(); renameActive = false; renameInput.value = ""; mounted = false; draftListeners.delete(changed); ++navigation; ++snapshotGeneration; offBus(); offState(); help.stop(); approvalView.stop(); const l = document.getElementById("xc-live"); if (l) l.classList.remove("on"); };
  drawLog(); drawStatus(); drawApprovals(); drawSide();
  const asked = sessionStorage.getItem("xc-open");   // the Tasks page's Open button names a subagent's session (#97)
  sessionStorage.removeItem("xc-open");
  restoreDraft();
  if (hasDesktopPreferences()) window.addEventListener("pagehide", saveReading);
  const startingNavigation = navigation;
  refreshSessions().then(() => {
    if (!mounted || current || navigation !== startingNavigation) return;
    if (asked) { open(asked); return; }
    if (draft(null).text || draft(null).pending) return;
    if (hasDesktopPreferences()) {
      if (savedThread && sessions.some(s => s.id === savedThread)) open(savedThread, savedScroll);
      else saveDesktopPreferences({ thread: null, scroll: null });
    } else if (sessions.length) open(sessions.some(s => s.id === lastThread) ? lastThread : sessions[0].id);
  });
  return off;
}

// A completed reply as Markdown, or its plain text if the renderer fails: one reply must never blank the thread.
function markdown(text) {
  try { return renderMarkdown(text); } catch (e) { return text; }
}
