// Layout metadata only: no conversation, draft, token, or arbitrary preference values.
export const PANE_IDS = Object.freeze(["threads", "conversation", "details"]);
export const DEFAULT_PANES = Object.freeze({ version: 1, order: PANE_IDS, threads: 224, details: 340, collapsed: [] });
const KEY = "exo-pane-layout-v1";
export function validateLayout(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).sort().join() !== "collapsed,details,order,threads,version") return null;
  if (value.version !== 1 || !Array.isArray(value.order) || value.order.length !== 3 || new Set(value.order).size !== 3 || value.order.some(x => !PANE_IDS.includes(x))) return null;
  if (!Array.isArray(value.collapsed) || new Set(value.collapsed).size !== value.collapsed.length || value.collapsed.some(x => !["threads", "details"].includes(x))) return null;
  if (![value.threads, value.details].every(x => Number.isInteger(x) && x >= 160 && x <= 520)) return null;
  return { version: 1, order: [...value.order], threads: value.threads, details: value.details, collapsed: [...value.collapsed] };
}
export function defaultLayout() { return validateLayout(DEFAULT_PANES); }
export async function loadPaneLayout() {
  try {
    const bridge = globalThis.webkit?.messageHandlers?.exoPaneLayout;
    if (bridge) return validateLayout(await bridge.postMessage({ action: "load" })) || defaultLayout();
    return validateLayout(JSON.parse(globalThis.localStorage?.getItem(KEY) || "null")) || defaultLayout();
  } catch { return defaultLayout(); }
}
export async function savePaneLayout(value) {
  const layout = validateLayout(value);
  if (!layout) throw new Error("Invalid pane layout");
  const bridge = globalThis.webkit?.messageHandlers?.exoPaneLayout;
  if (bridge) await bridge.postMessage({ action: "save", layout });
  else globalThis.localStorage?.setItem(KEY, JSON.stringify(layout));
  return layout;
}

// Moves existing nodes; never remounts the transcript, composer, or stream listeners.
export function attachPaneLayout(container, panes, toolbar) {
  let layout = defaultLayout(), stopped = false, revision = 0;
  const cleanup = [], original = PANE_IDS.map(id => panes[id]);
  container.classList.add("xc-pane-layout");
  const status = document.createElement("span"); status.setAttribute("role", "status");
  const persist = () => { revision++; status.textContent = ""; savePaneLayout(layout).catch(() => { if (!stopped) status.textContent = "Layout could not be saved."; }); };
  const apply = () => {
    for (const id of layout.order) {
      const pane = panes[id];
      container.appendChild(pane);
      pane.hidden = layout.collapsed.includes(id);
      pane.dataset.pane = id;
      if (id !== "conversation") pane.style.width = `${layout[id]}px`;
    }
    for (const range of toolbar.querySelectorAll("[data-pane-width]")) range.value = String(layout[range.dataset.paneWidth]);
    for (const button of toolbar.querySelectorAll("[data-pane-toggle]")) {
      const collapsed = layout.collapsed.includes(button.dataset.paneToggle);
      button.setAttribute("aria-pressed", String(!collapsed));
      button.textContent = `${collapsed ? "Show" : "Hide"} ${button.dataset.paneToggle}`;
    }
  };
  const change = fn => { fn(); apply(); persist(); };
  const button = (label, fn) => {
    const el = document.createElement("button"); el.type = "button"; el.className = "xc-btn";
    el.textContent = label; el.addEventListener("click", fn); toolbar.appendChild(el); return el;
  };
  toolbar.classList.add("xc-pane-controls"); toolbar.setAttribute("aria-label", "Pane layout");
  for (const id of ["threads", "details"]) {
    const toggle = button(`Hide ${id}`, () => change(() => { layout.collapsed = layout.collapsed.includes(id) ? layout.collapsed.filter(x => x !== id) : [...layout.collapsed, id]; }));
    toggle.dataset.paneToggle = id;
  }
  for (const id of PANE_IDS) {
    const group = document.createElement("span"); group.className = "xc-pane-adjust";
    const label = document.createElement("span"); label.textContent = id; group.appendChild(label);
    for (const delta of [-1, 1]) {
      const el = document.createElement("button"); el.type = "button"; el.className = "xc-btn";
      el.textContent = delta < 0 ? "←" : "→"; el.setAttribute("aria-label", `Move ${id} ${delta < 0 ? "left" : "right"}`);
      el.addEventListener("click", () => change(() => { const index = layout.order.indexOf(id), next = index + delta; if (next >= 0 && next < 3) [layout.order[index], layout.order[next]] = [layout.order[next], layout.order[index]]; }));
      group.appendChild(el);
    }
    if (id !== "conversation") {
      const range = document.createElement("input"); range.type = "range"; range.min = "160"; range.max = "520"; range.step = "4"; range.value = String(layout[id]);
      range.setAttribute("aria-label", `${id} pane width`); range.dataset.paneWidth = id;
      range.addEventListener("input", () => change(() => { layout[id] = Math.max(160, Math.min(520, Math.round(Number(range.value)))); }));
      group.appendChild(range);
      cleanup.push(() => range.remove());
    }
    toolbar.appendChild(group);
  }
  button("Reset panes", () => change(() => { layout = defaultLayout(); }));
  toolbar.appendChild(status);
  apply();
  const initialRevision = revision;
  loadPaneLayout().then(saved => { if (!stopped && revision === initialRevision) { layout = saved; apply(); } });
  return () => {
    stopped = true;
    for (const pane of original) { pane.hidden = false; pane.style.width = ""; delete pane.dataset.pane; container.appendChild(pane); }
    container.classList.remove("xc-pane-layout"); toolbar.replaceChildren();
    cleanup.forEach(fn => fn());
  };
}
