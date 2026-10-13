/* Fixtures for the x0 function tests: a fixed clock, a memory store, a fake GitHub, signed requests and
 * webhook payload builders. No network. */
import { readConfig } from "../../site/api/_lib/egg/config.mjs";
import { MemoryStore } from "../../site/api/_lib/egg/store.mjs";
import { sign } from "../../site/api/_lib/egg/crypto.mjs";
import { loadState } from "../../site/api/_lib/egg/http.mjs";

export const REPO_ID = 4242;
export const BOT = { id: 999, login: "ion-review", type: "User" };
export const CORE = { id: 100, login: "core-one", type: "User" };
export const T0 = Date.parse("2026-10-20T12:00:00Z");
export const OLD = Date.parse("2020-01-01T00:00:00Z");

export function makeDeps(over = {}) {
  const clock = { t: T0 };
  const cfg = readConfig(Object.assign({
    EGG_WEBHOOK_SECRET: "test-webhook-secret", EGG_MEDITATE_SECRET: "test-meditate-secret", EGG_HASH_KEY: "test-hash-key",
    EGG_REPO_ID: String(REPO_ID), EGG_REPO: "aska-digital/exo", EGG_CORE_ACCOUNTS: "100,101,core-two",
    EGG_REVIEWER_BOT_ID: String(BOT.id), CRON_SECRET: "test-cron-secret", EGG_STORE: "memory"
  }, over.env || {}));
  const ages = {}, rockets = new Set(), calls = { rockets: 0 };
  const github = {
    async rocketReviews(ids) { calls.rockets++; return new Set(ids.filter((i) => rockets.has(i))); },
    async accountCreated(id) { return id in ages ? ages[id] : OLD; }
  };
  const store = "store" in over ? over.store : new MemoryStore(() => clock.t);
  return { cfg, store, github, now: () => clock.t, clock, ages, rockets, calls };
}

export function user(id, login) { return { id, login: login || "user" + id, type: "User" }; }

let dn = 0;
export function hookReq(deps, event, payload, o = {}) {
  const body = typeof payload === "string" ? payload : JSON.stringify(payload);
  const headers = { "content-type": "application/json", "x-github-event": event, "x-github-delivery": o.delivery || "d-" + (++dn) };
  if (o.sig !== null) headers["x-hub-signature-256"] = o.sig || sign(deps.cfg.webhookSecret, body);
  return new Request("https://site.test/api/egg/webhook", { method: "POST", headers, body });
}

let nn = 0;
export function medReq(deps, body, o = {}) {
  const b = Object.assign({ ts: Math.floor(deps.now() / 1000), nonce: "nonce-" + (++nn) + "-abcdef" }, body);
  const raw = JSON.stringify(b);
  return new Request("https://site.test/api/egg/meditate", {
    method: "POST",
    headers: { "content-type": "application/json", "x-egg-signature-256": o.sig || sign(o.secret || deps.cfg.meditateSecret, raw) },
    body: raw
  });
}

export function feedReq(since, ip) {
  const u = "https://site.test/api/egg/feed" + (since ? "?since=" + encodeURIComponent(since) : "");
  return new Request(u, { headers: ip ? { "x-forwarded-for": ip } : {} });
}

const iso = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
const repo = { id: REPO_ID, full_name: "aska-digital/exo" };

export function issue(action, n, by, o = {}) {
  return {
    action, repository: repo, sender: o.sender || by, label: o.label,
    issue: { number: n, user: by, title: o.title || "Secret title " + n, body: o.body || "", labels: o.labels || [],
      created_at: iso(o.created || T0) }
  };
}
export function pr(action, n, by, o = {}) {
  return {
    action, repository: repo, sender: o.sender || by, label: o.label,
    pull_request: { number: n, user: by, title: o.title || "PR title " + n, body: o.body || "", labels: o.labels || [],
      merged: !!o.merged, merged_at: o.merged ? iso(o.mergedAt || T0) : null, created_at: iso(o.created || T0) }
  };
}
export function review(n, prAuthor, by, id, o = {}) {
  return {
    action: "submitted", repository: repo, sender: by,
    review: { id, node_id: "PRR_" + id, user: by, body: o.body || "Looks right", submitted_at: iso(o.at || T0) },
    pull_request: { number: n, user: prAuthor }
  };
}
export function release(tag) {
  return { action: "published", repository: repo, sender: CORE, release: { tag_name: tag, published_at: iso(T0) } };
}

export async function state(deps) { return loadState(deps.store); }
export async function feed(deps, handleFeed, since) { const r = await handleFeed(feedReq(since), deps); return r.json(); }
