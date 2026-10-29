// Local Exopet presentation. Growth comes only from the core's verified read; activity is selected-session data.
import { h, api } from "./dom.js";
import * as bus from "./bus.js";
import { createPetRenderer } from "./pet-renderer.js";

const KEY = "xc-pet-mode", MODES = ["full", "mini", "off"];
export function getPetMode(store) {
  try { const value = (store || localStorage).getItem(KEY); return MODES.includes(value) ? value : "mini"; } catch (_) { return "mini"; }
}
export function savePetMode(value, store) {
  const mode = MODES.includes(value) ? value : "mini";
  try { (store || localStorage).setItem(KEY, mode); } catch (_) { /* local preference still applies in this page */ }
  return mode;
}
const VERIFICATION_MESSAGES = {
  no_principal_store: "No principal store is configured. Personal growth is unavailable.",
  ledger_busy: "The local ledger is changing. Try another verification.",
  ledger_missing: "The local ledger is missing. Personal growth is unavailable.",
  unanchored: "The ledger has no verified anchor. Growth cannot be verified.",
  integrity_or_trust_failed: "Ledger integrity or trust verification failed. Growth is not updated.",
  authority_unavailable: "The local ledger authority is unavailable.",
  configuration_unavailable: "The local growth configuration is unavailable.",
  configuration_changed: "The local growth configuration changed. A fresh verification is required.",
  ledger_unreadable: "The local ledger could not be read.",
};
export function verificationMessage(reason) {
  return Object.hasOwn(VERIFICATION_MESSAGES, reason) ? VERIFICATION_MESSAGES[reason] : "Local growth could not be verified.";
}
export function newActivity() { return { running: null, phase: "thinking", tools: new Set(), approvals: new Set(), transient: null, job: false }; }
export function activityState(a) {
  if (a.transient === "error") return "error";
  if (a.approvals.size) return "waiting";
  if (a.tools.size) return "tool";
  if (a.running) return a.phase;
  return a.transient || (a.job ? "job" : "idle");
}
const LABELS = { idle: "Idle", thinking: "Working", answering: "Answering", tool: "Running a tool", waiting: "Waiting for approval",
  error: "An error was reported", done: "Turn completed", learning: "Taste updated", folding: "Memory folded", peer: "Peer message received", job: "Background job running" };

export function createPet({ authorized = () => true, onMode = () => {} } = {}) {
  let mode = getPetMode(), started = false, selected = null, a = newActivity(), connected = false, status = null, error = "";
  const waiters = new Set();
  let generation = 0, refreshTimer = 0, transientTimer = 0, idleTimer = 0, queued = false, request = null, lastRead = 0;
  let offState = null, media = null, observer = null, hosts = null, visible = new Map(), renderers = new Map(), idleStill = true;
  const glyph = h("span", { class: "xc-pet-glyph xc-pet-mini", role: "img", "aria-label": "Exopet" });
  const hero = h("div", { class: "xc-pet-figure xc-pet-hero", "aria-hidden": "true" });
  const figure = h("div", { class: "xc-pet-figure", "aria-hidden": "true" });
  const summary = h("div", { class: "xc-pet-growth" });
  const activity = h("p", { class: "xc-pet-activity" });
  const card = h("details", { class: "xc-pet-card", "aria-label": "Exopet", open: !!(window.matchMedia && window.matchMedia("(min-width: 1101px)").matches) },
    h("summary", { class: "xc-pet-card-summary" }, "Exopet details"), h("div", { class: "xc-pet-card-body" }, figure, activity, summary));
  const select = h("select", { class: "xc-pet-mode", "aria-label": "Exopet display", onchange: () => setMode(select.value) },
    ...[["full", "Full"], ["mini", "Mini"], ["off", "Off"]].map(([value, label]) => h("option", { value }, label)));
  select.value = mode;
  const control = h("label", { class: "xc-pet-control" }, h("span", null, "Exopet"), select);
  const allowed = () => started && mode !== "off" && authorized();
  const clear = name => { clearTimeout(name); };
  function label() { return !connected ? "Activity unavailable while reconnecting" : !selected ? "No conversation selected" : a.approvals.size ? LABELS.waiting : a.transient === "error" ? LABELS.error : a.running === null ? "Reading selected conversation" : LABELS[activityState(a)]; }
  function showGrowth() {
    const p = status && status.personal;
    if (!status) { summary.replaceChildren(h("p", { class: "xc-pet-note" }, error || "Verifying local growth…")); return; }
    const known = p && typeof p.xp === "number" && typeof p.level === "number";
    const verified = status.verification && status.verification.state === "verified";
    summary.replaceChildren(...[h("p", null, "Egg · community hatch pending"),
      known ? h("p", null, "Personal level " + p.level + " · " + p.xp + " XP banked") : h("p", null, "Personal growth unavailable"),
      typeof status.learned_count === "number" ? h("p", null, status.learned_count + " recorded promotions") : null,
      typeof status.progress === "number" ? h("progress", { class: "xc-pet-progress", max: "1", value: String(Math.max(0, Math.min(1, status.progress))), "aria-label": "Banked personal XP toward cell; community hatch still pending" }) : null,
      h("p", { class: "xc-pet-note" }, "Personal XP is banked. The form stays egg until the community hatch."),
      h("p", { class: "xc-pet-note" }, status.stale || error ? "Growth frozen at the last verified read." : verified ? "Growth verified locally." : verificationMessage(status.verification?.reason)),
      status.stale ? h("p", { class: "xc-pet-note xc-pet-verification" }, verificationMessage(status.verification?.reason)) : null,
      error ? h("p", { class: "xc-pet-note" }, error) : null].filter(Boolean));
  }
  function render() {
    if (!allowed()) return;
    const text = label(), state = activityState(a);
    glyph.setAttribute("aria-label", "Exopet: " + text); glyph.title = "Exopet: " + text;
    activity.textContent = text + (selected ? " · selected conversation" : "");
    glyph.hidden = false; card.hidden = hero.hidden = mode !== "full";
    showGrowth();
    for (const host of [glyph, ...(mode === "full" && hosts ? [hero, figure] : [])]) {
      if (!host.isConnected) continue;
      if (!renderers.has(host)) {
        renderers.set(host, createPetRenderer(host, { reducedMotion: !!(media && media.matches), mini: host === glyph }));
        if (observer) observer.observe(host);
      }
      const active = connected && !!selected && a.running !== null && !document.hidden && visible.get(host) !== false && !idleStill;
      renderers.get(host).render({ form: "egg", state, learned: status && status.verification?.state === "verified" && !status.stale ? status.learned_count || 0 : 0, active, reducedMotion: !!(media && media.matches) });
    }
  }
  function destroyFigures() { for (const renderer of renderers.values()) renderer.destroy(); renderers.clear(); visible.clear(); if (observer) observer.disconnect(); }
  function settle() { for (const resolve of waiters) resolve(); waiters.clear(); }
  function invalidate() {
    ++generation; settle(); queued = false; clear(refreshTimer); refreshTimer = 0;
    if (request) { clear(request.timeout); request.abort.abort(); request = null; }
  }
  function stopWatching() {
    invalidate(); clear(transientTimer); clear(idleTimer); transientTimer = idleTimer = 0;
    if (offState) offState(); offState = null;
    if (document.removeEventListener) document.removeEventListener("visibilitychange", visibility);
    card.removeEventListener("toggle", render);
    if (media && media.removeEventListener) media.removeEventListener("change", motion);
    media = null; destroyFigures(); observer = null;
  }
  function motion() { render(); }
  function visibility() { if (document.hidden) for (const renderer of renderers.values()) renderer.pause(); else render(); }
  function idleClock() {
    clear(idleTimer); idleTimer = 0;
    idleStill = !selected || a.running === null;
    if (!a.running && !a.tools.size && !a.approvals.size) idleTimer = setTimeout(() => { idleStill = true; render(); }, 30000);
  }
  function watch() {
    if (!allowed() || offState) return;
    media = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
    if (media && media.addEventListener) media.addEventListener("change", motion);
    if (document.addEventListener) document.addEventListener("visibilitychange", visibility);
    card.addEventListener("toggle", render);
    if (typeof IntersectionObserver !== "undefined") observer = new IntersectionObserver(entries => {
      for (const entry of entries) { visible.set(entry.target, entry.isIntersecting); if (!entry.isIntersecting) renderers.get(entry.target)?.pause(); }
      render();
    });
    offState = bus.onState(s => {
      if (connected !== !!s.connected || s.denied) { connected = !!s.connected && !s.denied; a = newActivity(); idleStill = true; render(); }
    });
  }
  async function read() {
    if (!allowed() || request) return;
    queued = false;
    const own = { generation, abort: new AbortController(), timeout: 0, timedOut: false };
    request = own; lastRead = Date.now();
    own.timeout = setTimeout(() => { own.timedOut = true; own.abort.abort(); }, 90000);
    try {
      const next = await api("GET", "/api/pet", undefined, own.abort.signal);
      if (request !== own || own.generation !== generation || !allowed()) return;
      if (!next || next.schema_version !== 1 || next.form !== "egg" || next.community?.state !== "pending") throw Error("Unsupported Exopet status");
      status = next; error = ""; render();
    } catch (e) {
      if (request === own && own.generation === generation && allowed()) { error = e.name === "AbortError" ? "Local growth read timed out." : e.status === 403 ? "Local growth read was refused." : e.status === 501 ? "This core does not support Exopet status." : "Local growth unavailable. Try again after the connection recovers."; render(); }
    } finally {
      clear(own.timeout);
      if (request === own) {
        request = null; settle();
        const again = queued && !own.timedOut; queued = false;
        if (again) refresh();  // a timeout waits for a new explicit event/request; never an automatic retry loop
      }
    }
  }
  function refresh() {
    if (!allowed()) return;
    queued = true;
    if (request || refreshTimer) return;
    refreshTimer = setTimeout(() => { refreshTimer = 0; read(); }, Math.max(0, 200 - (Date.now() - lastRead)));
  }
  function setMode(value) {
    mode = savePetMode(value); select.value = mode;
    stopWatching(); glyph.hidden = mode === "off" || !started || !authorized(); card.hidden = hero.hidden = mode !== "full" || !allowed();
    if (mode !== "off") { watch(); if (hosts) hosts.sync(); refresh(); idleClock(); render(); }
    onMode(mode);
  }
  function selectSession(id) {
    invalidate(); clear(transientTimer); clear(idleTimer); transientTimer = idleTimer = 0; a = newActivity(); selected = id || null; idleStill = true;
    if (allowed()) { refresh(); render(); }
  }
  function hydrate(snapshot) {
    if (snapshot.session !== selected || !allowed()) return;
    a.running = connected ? !!snapshot.running : null; a.phase = "thinking";
    a.tools = new Set(a.running ? snapshot.tools || [] : []);
    a.approvals = new Set(snapshot.approvals || []);
    idleClock(); render();
  }
  function approvals(rows) {
    if (!allowed()) return;
    a.approvals = new Set(selected ? rows.filter(row => !row.session || row.session === selected).map(row => row.request_id) : []);
    idleClock(); render();
  }
  function transient(state, milliseconds) {
    clear(transientTimer); a.transient = state;
    transientTimer = setTimeout(() => { a.transient = null; transientTimer = 0; idleClock(); render(); }, milliseconds);
  }
  function event(ev) {
    if (!allowed() || !connected) return;
    if (ev.type === "taste.updated" && (!ev.session || ev.session === selected)) refresh();
    if (!selected || ev.session !== selected) return;
    const b = ev.body || {};
    switch (ev.type) {
      case "turn.started": a.running = true; a.phase = "thinking"; a.tools.clear(); break;
      case "reasoning.delta": a.phase = "thinking"; break;
      case "message.delta": a.phase = "answering"; break;
      case "tool.started": if (b.call?.id) a.tools.add(b.call.id); break;
      case "tool.completed": a.tools.delete(b.result?.call_id); if (b.is_error || b.result?.is_error) transient("error", 1000); break;
      case "turn.completed": a.running = false; a.tools.clear(); if (b.error) transient("error", 1000); else if (b.stop_reason === "end") transient("done", 1000); refresh(); break;
      case "error": case "harness.error": transient("error", 1000); break;
      case "memory.summarized": case "context.compacted": transient("folding", 1000); break;
      case "taste.updated": transient("learning", 1000); break;
      case "peer.message": transient("peer", 2000); break;
      case "job.updated": a.job = b.status === "running"; break;
      // model.call is an outcome report, not proof that a new call is in flight.
    }
    idleClock(); render();
  }
  function attach(next) {
    hosts = next; next.hero.append(hero); next.card.append(card);
    if (allowed()) { next.sync(); render(); }
    return () => { hosts = null; selectSession(null); for (const host of [hero, figure]) { renderers.get(host)?.destroy(); renderers.delete(host); observer?.unobserve(host); } };
  }
  return { control, glyph, card, hero, event, hydrate, approvals, selectSession, refresh, attach, mode: () => mode,
    start() { started = true; watch(); refresh(); render(); onMode(mode); },
    suspend() { started = false; stopWatching(); status = null; error = ""; a = newActivity(); glyph.hidden = card.hidden = hero.hidden = true; },
    async inspect() {
      if (!allowed()) throw Error("Exopet is off; choose Mini or Full to read status.");
      const own = generation;
      await new Promise(resolve => { waiters.add(resolve); refresh(); });
      if (!allowed() || own !== generation) throw Error("Exopet context changed; try again.");
      if (error) throw Error(error);
      return status;
    },
  };
}
