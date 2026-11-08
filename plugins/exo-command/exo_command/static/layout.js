// Chat layout (D-046). Pure functions plus one saved choice, so a test can run them in node.
//   inverse: the message box is at the top, the newest item sits directly under it and older ones go down.
//   classic: the box is at the bottom and the newest item is at the bottom.
// The experiment ships with `inverse` as the default. To end it, change DEFAULT_LAYOUT to "classic": a person who
// picked a layout (the button or /layout) keeps their pick, because it is saved in this browser (localStorage).
export const DEFAULT_LAYOUT = "inverse";
export const LAYOUTS = ["inverse", "classic"];
const KEY = "xc-layout";

export function getLayout(store) {
  try {
    const v = (store || localStorage).getItem(KEY);
    return LAYOUTS.includes(v) ? v : DEFAULT_LAYOUT;
  } catch (e) { return DEFAULT_LAYOUT; }
}

export function setLayout(v, store) {
  if (!LAYOUTS.includes(v)) return false;
  try { (store || localStorage).setItem(KEY, v); } catch (e) { /* private mode: the choice lasts this page only */ }
  return true;
}

// Items are kept oldest first; this is the order to draw them in. Inside one message the text still reads
// top to bottom, because only whole items are reordered.
export function ordered(items, layout) { return layout === "inverse" ? items.slice().reverse() : items.slice(); }

// Where the log sits when it opens or a new item arrives: the top for inverse, the bottom for classic.
export function homeScroll(layout, scrollHeight, clientHeight) {
  return layout === "inverse" ? 0 : Math.max(0, scrollHeight - clientHeight);
}

// Is the reader close enough to home that new content should keep them there?
export function nearHome(layout, scrollTop, scrollHeight, clientHeight, slack = 120) {
  return Math.abs(scrollTop - homeScroll(layout, scrollHeight, clientHeight)) < slack;
}

// The order of the parts of the chat column. The tab order follows the page, so it is the real DOM order.
export function columnOrder(layout) {
  return layout === "inverse"
    ? ["banner", "status", "guide", "composer", "menu", "steer", "approvals", "stage"]
    : ["banner", "stage", "approvals", "status", "steer", "guide", "composer", "menu"];
}

// Flow mode: the active chat fills the window (only the transcript and the composer stay). One class on
// document.body, saved in this browser under "xc-flow". Pure helpers here; app.js wires the button and keys.
export const FLOW_KEY = "xc-flow";
export const FLOW_CLASS = "xc-flow";

export function getFlow(store) {
  try { return (store || localStorage).getItem(FLOW_KEY) === "1"; } catch (e) { return false; }
}

export function setFlow(on, store) {
  try { (store || localStorage).setItem(FLOW_KEY, on ? "1" : "0"); } catch (e) { /* private mode: lasts this page only */ }
  return !!on;
}

// Cmd/Ctrl+Shift+F toggles. Alt is left alone so it never collides with an input method or an OS shortcut.
export function isFlowToggle(e) {
  return !!e && String(e.key).toLowerCase() === "f" && !!e.shiftKey && (!!e.metaKey !== !!e.ctrlKey) && !e.altKey && !e.isComposing;
}

// Anything that owns Escape first: an open modal, listbox, picker or menu. Then Esc is not ours.
export const FLOW_ESC_BLOCKERS = '[role="dialog"]:not([hidden]), [role="alertdialog"]:not([hidden]), [aria-modal="true"], '
  + '.xc-rename-editor:not([hidden]), .xc-menu:not([hidden]), .xc-model-picker:not([hidden]), [role="menu"]:not([hidden]), [role="listbox"]:not([hidden]), dialog[open]';

export function isFlowExit(e, doc) {
  if (!e || e.key !== "Escape" || e.defaultPrevented || e.isComposing || e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return false;
  if (e.target && e.target.closest && e.target.closest(".xc-approvals")) return false;   // a card uses Esc to cancel
  return !(doc || document).querySelector(FLOW_ESC_BLOCKERS);
}
