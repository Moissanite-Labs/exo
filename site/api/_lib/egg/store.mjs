/* One small key-value interface for the x0 functions (docs/LIVE-EGG.md section 3.7).
 *
 *   get(key)                         -> string | null
 *   set(key, value, {nx, px})        -> true if written (false when nx and the key exists)
 *   del(key)                         -> undefined
 *   incr(key, by, {px})              -> the new integer; px sets the expiry when the key is created
 *
 * MemoryStore is for tests and local runs. UpstashStore speaks the Upstash Redis REST protocol, which is what the
 * Vercel Marketplace's Redis (Upstash) provides; it needs no package. The choice is made in getStore() from config.
 */
export class MemoryStore {
  constructor(clock = () => Date.now()) { this.m = new Map(); this.clock = clock; }
  _live(key) {
    const e = this.m.get(key);
    if (e && e.exp && e.exp <= this.clock()) { this.m.delete(key); return null; }
    return e || null;
  }
  async get(key) { const e = this._live(key); return e ? e.v : null; }
  async set(key, value, o = {}) {
    if (o.nx && this._live(key)) return false;
    this.m.set(key, { v: String(value), exp: o.px ? this.clock() + o.px : 0 });
    return true;
  }
  async del(key) { this.m.delete(key); }
  async incr(key, by = 1, o = {}) {
    const e = this._live(key);
    const n = (e ? parseInt(e.v, 10) || 0 : 0) + by;
    this.m.set(key, { v: String(n), exp: e ? e.exp : (o.px ? this.clock() + o.px : 0) });
    return n;
  }
}

export class UpstashStore {
  constructor(url, token, fetchImpl = globalThis.fetch) {
    this.url = url.replace(/\/$/, ""); this.token = token; this.fetch = fetchImpl;
  }
  async cmd(args) {
    const r = await this.fetch(this.url, {
      method: "POST",
      headers: { authorization: "Bearer " + this.token, "content-type": "application/json" },
      body: JSON.stringify(args)
    });
    if (!r.ok) throw new Error("store " + r.status);
    const j = await r.json();
    if (j.error) throw new Error("store error");
    return j.result;
  }
  async get(key) { return this.cmd(["GET", key]); }
  async set(key, value, o = {}) {
    const a = ["SET", key, String(value)];
    if (o.nx) a.push("NX");
    if (o.px) a.push("PX", String(o.px));
    return (await this.cmd(a)) === "OK";
  }
  async del(key) { await this.cmd(["DEL", key]); }
  async incr(key, by = 1, o = {}) {
    const n = await this.cmd(["INCRBY", key, String(by)]);
    if (o.px && n === by) await this.cmd(["PEXPIRE", key, String(o.px)]);
    return n;
  }
}

let shared = null;
/* The store named by config. With no REST URL and no explicit EGG_STORE=memory there is no store: the functions
   answer 503 rather than silently keep state in one instance's memory. */
export function getStore(cfg) {
  if (shared) return shared;
  if (cfg.store === "memory") return (shared = new MemoryStore());
  if (cfg.kvUrl && cfg.kvToken) return (shared = new UpstashStore(cfg.kvUrl, cfg.kvToken));
  return null;
}
export function setStoreForTests(s) { shared = s; }

/* A short lock, so two hooks never race on the state record. The state is one JSON document. */
export async function withLock(store, name, fn, { clock = () => Date.now(), waitMs = 4000, holdMs = 8000 } = {}) {
  const key = "egg:lock:" + name, start = clock();
  for (let i = 0; ; i++) {
    if (await store.set(key, "1", { nx: true, px: holdMs })) break;
    if (clock() - start > waitMs) throw new Error("lock busy");
    await new Promise((r) => setTimeout(r, Math.min(200, 20 * (i + 1))));
  }
  try { return await fn(); } finally { await store.del(key); }
}
