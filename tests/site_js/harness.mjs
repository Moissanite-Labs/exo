/* A tiny browser stand-in for site/brand/egg.js and egg-feed.js: a stub 2D context that accepts every call,
 * a manual clock for timers, and a document with visibility. Runs the real files in a vm context. */
import { readFileSync } from "node:fs";
import vm from "node:vm";

function ctx2d() {
  const grad = { addColorStop() {} };
  return new Proxy({}, {
    get(t, k) {
      if (k in t) return t[k];
      if (k === "createRadialGradient" || k === "createLinearGradient") return () => grad;
      return () => {};
    },
    set(t, k, v) { t[k] = v; return true; }
  });
}
function canvas() { return { width: 300, height: 300, getContext: () => ctx2d(), getBoundingClientRect: () => ({ width: 300, height: 300 }) }; }

export function makeWindow({ reduce = false } = {}) {
  let now = 0, seq = 0;
  const timers = new Map(), listeners = {};
  const document = {
    hidden: false, readyState: "complete",
    createElement: () => canvas(),
    querySelectorAll: () => [],
    addEventListener(t, f) { (listeners[t] = listeners[t] || []).push(f); },
    removeEventListener() {}
  };
  const win = {
    document, console, Math, JSON, Date, Promise, Object, Array, Float32Array, Uint8Array, URL, Error, isNaN,
    devicePixelRatio: 1,
    matchMedia: () => ({ matches: reduce }),
    addEventListener() {}, removeEventListener() {},
    requestAnimationFrame: () => 0, cancelAnimationFrame() {},
    performance: { now: () => now },
    setTimeout(f, ms) { const id = ++seq; timers.set(id, { at: now + (ms || 0), f }); return id; },
    clearTimeout(id) { timers.delete(id); },
    location: { search: "", hostname: "localhost" }
  };
  win.window = win;
  vm.createContext(win);
  win.__advance = async (ms) => {                                      // run timers in order up to now + ms
    const end = now + ms;
    const flush = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };
    await flush();                                                     // let pending fetch chains settle at this time
    for (;;) {
      let next = null;
      for (const [id, t] of timers) if (t.at <= end && (!next || t.at < next[1].at)) next = [id, t];
      if (!next) break;
      timers.delete(next[0]); now = next[1].at; next[1].f();
      await flush();
    }
    now = end;
  };
  win.__fire = (type) => (listeners[type] || []).forEach((f) => f());
  win.__canvas = canvas;
  return win;
}

export function load(win, file) {
  vm.runInContext(readFileSync(new URL("../../site/" + file, import.meta.url), "utf8"), win, { filename: file });
}
