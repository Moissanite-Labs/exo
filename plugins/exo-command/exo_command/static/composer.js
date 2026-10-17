// The web composer's help (D-037): a dropdown for "/" and arguments, @ files, ghost text from history, typo
// help and one dim guidance line. The logic is in complete.js; this file only draws and reads keys.
import { h, api } from "./dom.js";
import { suggest, completeText, ghost, closest, Guide } from "./complete.js";

const MENU_ROWS = 8;

export function attachComposer({ input, composer, getSession, reducedMotion }) {
  let cmds = [], core = [], client = [], loaded = false, stopped = false, loadingCmds = null, retries = 0, retryTimer = 0;
  let sug = { mode: "", items: [], start: 0, hint: "" }, sel = 0, closedFor = null, files = [], fileQ = null, fileTimer = 0;
  let browse = -1, draft = "", ghostInfo = { text: "", ok: false }, tip = null;
  const history = [];
  const guide = new Guide();
  const state = { providerError: false, jobDone: "", findings: 0, longTurn: false, turns: 0, busy: false };

  const menu = h("div", { class: "xc-menu", role: "listbox", "aria-label": "Suggestions", hidden: true });
  const guideEl = h("div", { class: "xc-guide", "aria-live": "off", hidden: true });
  const ghostEl = h("div", { class: "xc-ghost", "aria-hidden": "true" });
  input.parentNode.insertBefore(ghostEl, input);
  input.addEventListener("scroll", () => { ghostEl.scrollTop = input.scrollTop; });

  // The names typo help and the hold rule need. A failed load is asked again (backing off, and on the next send), so
  // one bad answer never leaves the page without them; until it loads, the page holds every /name line (#146).
  function loadCommands() {
    if (loaded || stopped || loadingCmds) return;
    clearTimeout(retryTimer);
    loadingCmds = api("GET", "/api/complete/commands").then((d) => {
      // No core names yet (a core restarting): ask again a few times before settling for this page's own names.
      if (d.core == null && retries < 6) throw new Error("the core's command names are not available yet");
      cmds = d.commands || []; core = d.core || []; client = d.client || []; loaded = true;
      guide.enabled = d.guidance !== false; pickTip(); refresh();
    }).catch(() => { retryTimer = setTimeout(loadCommands, Math.min(30000, 1000 * 2 ** retries++)); })
      .finally(() => { loadingCmds = null; });
  }
  loadCommands();

  function wantFiles() { return !!getSession(); }
  function atEnd() { return input.selectionStart === input.value.length && input.selectionEnd === input.value.length; }

  function loadFiles(q) {
    if (q === fileQ) return;
    fileQ = q;
    clearTimeout(fileTimer);
    fileTimer = setTimeout(async () => {
      try {
        const d = await api("GET", "/api/complete/files?session=" + encodeURIComponent(getSession() || "") + "&q=" + encodeURIComponent(q));
        if (q !== fileQ) return;
        files = (d.files || []).map((f) => ({ value: f.value, label: f.label, meta: f.meta }));
        if (sug.mode === "file") { sug.items = files; sel = 0; draw(); }
      } catch (e) { files = []; }
    }, 90);
  }

  function refresh() {
    const text = input.value;
    sug = browse >= 0 || !atEnd() ? { mode: "", items: [], start: 0, hint: "" } : suggest(text, cmds, wantFiles());
    if (sug.mode === "file") { sug.items = fileQ === sug.query ? files : []; loadFiles(sug.query); }
    sel = 0;
    ghostInfo = browse >= 0 ? { text: "", ok: false } : ghost(text, sug, history);
    draw();
  }

  function shown() { return sug.items.length > 0 && ["command", "arg", "file"].includes(sug.mode) && closedFor !== input.value; }

  function draw() {
    const text = input.value;
    ghostEl.replaceChildren(h("span", { class: "xc-ghost-typed" }, text), h("span", { class: "xc-ghost-dim" }, ghostInfo.text));
    ghostEl.hidden = !ghostInfo.text;
    if (sug.mode === "shell") { menu.replaceChildren(h("div", { class: "xc-menu-hint" }, sug.hint)); menu.hidden = false; return; }
    if (!shown()) { menu.hidden = true; menu.replaceChildren(); return; }
    const top = sug.items.length > MENU_ROWS ? Math.max(0, Math.min(sel - MENU_ROWS + 1, sug.items.length - MENU_ROWS)) : 0;
    const rows = sug.items.slice(top, top + MENU_ROWS).map((it, k) => {
      const on = top + k === sel;
      return h("div", { class: "xc-menu-row" + (on ? " on" : ""), role: "option", "aria-selected": on ? "true" : "false",
        onmousedown: (e) => { e.preventDefault(); take(it); } },
        h("span", { class: "xc-menu-name" }, it.label), it.args ? h("span", { class: "xc-menu-args" }, it.args) : null,
        it.meta ? h("span", { class: "xc-menu-desc" }, it.meta) : null);
    });
    menu.replaceChildren(...rows, h("div", { class: "xc-menu-hint" }, "tab completes · ↑↓ move · esc closes"));
    menu.hidden = false;
    const on = menu.querySelector(".on");
    if (on && on.scrollIntoView) on.scrollIntoView({ block: "nearest" });
  }

  function put(text) { input.value = text; input.setSelectionRange(text.length, text.length); refresh(); }
  function take(item) { put(completeText(input.value, sug, item)); input.focus(); }

  input.addEventListener("input", () => { browse = -1; closedFor = null; tip = tip && !input.value ? tip : null; drawTip(); refresh(); });
  input.addEventListener("click", refresh);
  input.addEventListener("keyup", (e) => { if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) refresh(); });

  // Runs before chat.js's own Enter handler (added first); stopImmediatePropagation keeps Enter from sending.
  input.addEventListener("keydown", (e) => {
    if (e.isComposing) return;
    const use = () => { e.preventDefault(); e.stopImmediatePropagation(); };
    if (shown()) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { use(); sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + sug.items.length) % sug.items.length; draw(); return; }
      if (e.key === "Tab") { use(); take(sug.items[sel]); return; }
      if (e.key === "Enter" && !e.shiftKey) {
        if (completeText(input.value, sug, sug.items[sel]).trim() !== input.value.trim()) { use(); take(sug.items[sel]); }
        return;
      }
      if (e.key === "Escape") { use(); closedFor = input.value; draw(); return; }
      return;
    }
    const first = !input.value.slice(0, input.selectionStart).includes("\n");
    if (e.key === "ArrowUp" && first && history.length) {
      use();
      if (browse < 0) { draft = input.value; browse = history.length; }
      if (browse > 0) { browse -= 1; input.value = history[browse]; input.setSelectionRange(input.value.length, input.value.length); refresh(); }
      return;
    }
    if (e.key === "ArrowDown" && browse >= 0 && !input.value.slice(input.selectionStart).includes("\n")) {
      use();
      browse += 1;
      if (browse >= history.length) { browse = -1; put(draft); } else { input.value = history[browse]; input.setSelectionRange(input.value.length, input.value.length); refresh(); }
      return;
    }
    if ((e.key === "ArrowRight" || e.key === "End") && ghostInfo.ok && atEnd()) { use(); put(input.value + ghostInfo.text); return; }
    if (e.key === "Tab" && !e.shiftKey) {
      if (ghostInfo.ok && atEnd()) { use(); put(input.value + ghostInfo.text); return; }
      if (!input.value && tip && tip.fill) { use(); put(tip.fill); return; }
    }
  }, true);

  // ---- guidance line: dim, one line, rate limited, nothing while a turn runs, no animation under reduced motion
  function drawTip() {
    const text = tip && !input.value ? tip.text + (tip.fill ? "  · tab fills it in" : "") : "";
    guideEl.textContent = text;
    guideEl.hidden = !text;
    guideEl.classList.toggle("xc-guide-still", !!reducedMotion);
  }
  function pickTip() {
    if (tip) return;
    tip = guide.pick(state);
    drawTip();
  }
  guideEl.addEventListener("click", () => { if (tip && tip.fill) { put(tip.fill); input.focus(); } });
  const timer = reducedMotion ? 0 : setInterval(pickTip, 10000);

  return {
    menuEl: menu, guideEl,
    // The page tells us what happened; we only keep a few flags.
    note(ev) { Object.assign(state, ev); pickTip(); },
    sent(text) {
      history.push(text); if (history.length > 200) history.shift();
      browse = -1; tip = null; drawTip();
      const m = /^\/([\w-]+)/.exec(text);
      if (m) guide.used(m[1]);
      if (/job_output/.test(text)) state.jobDone = "";
      refresh();
    },
    // Whether the command names have loaded; when they have not, this asks again.
    ready() { loadCommands(); return loaded; },
    // A mistyped /command: the suggestion, or null when it is not a typo (so the line is a normal message).
    // Like the TUI, the names are this page's commands, the core's commands.list and the terminal client's own, and
    // only a simple lower-case name gets typo help (/Users and /v2 are text); here is false when the page does not run it.
    typo(text) {
      const m = /^\/([A-Za-z][\w-]*)(\s|$)/.exec(text), name = m && m[1].toLowerCase();
      if (!m || !loaded || cmds.some((c) => c.name === name) || core.includes(name) || client.includes(name)) return null;
      if (!/^[a-z][a-z-]*$/.test(m[1])) return null;
      const g = closest(name, [...cmds.map((c) => c.name), ...core, ...client]);
      return g ? { guess: g, line: "/" + g + text.slice(m[0].length - m[2].length), here: cmds.some((c) => c.name === g) } : null;
    },
    known(name) { return cmds.some((c) => c.name === name); },
    // Where a command this page does not run lives: "core" (commands.list, so exo chat runs it too) or "client" (the
    // terminal client's own, such as /help or /quit); "" when the page runs it or nobody does. Held, never sent (#146).
    elsewhere(name) {
      if (cmds.some((c) => c.name === name)) return "";
      return core.includes(name) ? "core" : client.includes(name) ? "client" : "";
    },
    stop() { stopped = true; clearInterval(timer); clearTimeout(retryTimer); },
  };
}
