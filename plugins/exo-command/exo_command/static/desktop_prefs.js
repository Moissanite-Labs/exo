// Native installs persist display choices outside their disposable, port-scoped web storage.
// Browser sessions retain their existing behavior. Tokens and conversation text never cross this bridge.
const choices = { view: ['chat', 'tasks', 'schedule', 'command', 'providers'], layout: ['inverse', 'classic'], appearance: ['dark', 'light', 'system'], petMode: ['mini', 'full', 'off'] };
const source = typeof window !== 'undefined' && window.__exoDesktopPrefs;
const native = !!(source && typeof source === 'object' && !Array.isArray(source));
const values = {};
function valid(key, value) {
  if (Object.hasOwn(choices, key)) return choices[key].includes(value);
  if (key === 'thread') return value === null || typeof value === 'string' && /^[A-Za-z0-9_-]{1,200}$/.test(value);
  if (key === 'scroll') return value === null || typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1;
  return false;
}
if (native) {
  for (const [key, value] of Object.entries(source)) if (valid(key, value)) values[key] = value;
  // Native choices win over browser data left behind when an old loopback port is reused.
  for (const [key, fallback, storage] of [['layout', 'inverse', 'xc-layout'], ['appearance', 'dark', 'xc-appearance'], ['petMode', 'mini', 'xc-pet-mode']]) {
    try { localStorage.setItem(storage, values[key] || fallback); } catch (_) { /* native values still remain available */ }
  }
}
export function desktopPreference(key) { return native ? values[key] : undefined; }
export function hasDesktopPreferences() { return native; }
export function saveDesktopPreferences(patch) {
  if (!native) return;
  const safe = {};
  for (const [key, value] of Object.entries(patch)) if (valid(key, value)) { values[key] = value; safe[key] = value; }
  if (!Object.keys(safe).length) return;
  try { window.webkit?.messageHandlers?.exoDesktop?.postMessage(safe); } catch (_) { /* a closing native host must not break the page */ }
}
