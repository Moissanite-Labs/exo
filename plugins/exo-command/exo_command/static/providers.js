// Providers view: where model keys go, per-provider status, key save/test/remove, and the default model.
// A key value only ever lives in the password field. It is read once to build the request body, the field is
// cleared at once (also on error), and it is never logged, stored, put in a URL, or shown again.
import { h, api, tag, empty, errorBox } from "./dom.js";

const STORE_TEXT = {
  keychain: "the macOS Keychain",
  "secret-service": "the system keyring (Secret Service)",
  file: "a private keys file in your Exo home (mode 600)",
};

function chip(r) {
  const st = r.status || "";
  const lt = r.last_test || {};
  if (r.verified) return tag("verified" + (lt.latency_ms != null ? " · " + Math.round(lt.latency_ms) + " ms" : ""), "green");
  if (st.startsWith("failed")) return tag(st, "red");
  if (r.needs === "key") return tag(r.key === "found" ? "key found" : "no key", r.key === "found" ? "cyan" : "");
  if (r.needs === "cli") return tag(r.ready ? "cli installed" : "cli not installed", r.ready ? "cyan" : "");
  return tag(r.local ? (r.reachable ? "local · running" : "local · not running") : st, r.reachable ? "green" : "");
}

export function mount(root) {
  const body = h("div", { class: "xc-prov" });
  root.replaceChildren(body);
  let alive = true;

  if (!sessionStorage.getItem("xc-token")) {
    body.replaceChildren(h("section", { class: "xc-panel" }, h("header", { class: "xc-panel-head" }, h("h2", null, "Providers")),
      h("p", { class: "xc-prov-lead" }, "Open the link `exo web` printed in the terminal. It carries a page token that this page needs to set up providers.")));
    return () => { alive = false; };
  }

  const results = {};   // provider name -> {text, cls}; kept across redraws, never holds a key
  let data = null, models = null, err = "", picked = "";

  async function load() {
    try {
      data = await api("GET", "/api/providers");
      models = await api("GET", "/api/providers/models").catch(() => ({ models: [], available: [] }));
      err = "";
    } catch (e) { err = e.message; }
    if (alive) draw();
  }

  function show(name, text, cls) { results[name] = { text, cls }; if (alive) draw(); }

  async function save(r, input) {
    const body = { key: input.value };
    input.value = "";                                   // cleared before the request, so it is gone on any outcome
    if (!body.key.trim()) { show(r.provider, "Paste a key first.", "bad"); return; }
    try {
      const d = await api("POST", "/api/providers/" + encodeURIComponent(r.provider) + "/key", body);
      show(r.provider, "Stored in " + d.stored_in + "." + (d.shadowed_by_env ? " The env var " + d.var + " shadows it." : "") +
        (d.core_reloaded ? "" : " The core was not told to reload; restart it to use the key."), "ok");
    } catch (e) { show(r.provider, e.message, "bad"); }
    body.key = "";
    if (alive) load();
  }

  async function test(r) {
    show(r.provider, "Testing...", "");
    try {
      const d = await api("POST", "/api/providers/" + encodeURIComponent(r.provider) + "/test", {});
      show(r.provider, d.ok ? "Test ok in " + Math.round(d.latency_ms || 0) + " ms." : "Test failed: " + (d.error_class || "error") + (d.detail ? " " + d.detail : ""), d.ok ? "ok" : "bad");
    } catch (e) { show(r.provider, e.message, "bad"); }
    if (alive) load();
  }

  async function remove(r) {
    if (!confirm("Remove the stored key for " + r.provider + "?")) return;
    try {
      const d = await api("POST", "/api/providers/" + encodeURIComponent(r.provider) + "/remove", {});
      show(r.provider, "Removed from " + ((d.removed_from || []).join(", ") || "nothing") + "." + ((d.still_in_env || []).length ? " Still set in the environment: " + d.still_in_env.join(", ") + "." : ""), "ok");
    } catch (e) { show(r.provider, e.message, "bad"); }
    if (alive) load();
  }

  function row(r) {
    const meta = [];
    if (r.needs === "key") meta.push(r.key === "found" ? "key from " + (r.source || "?") : "key missing");
    if (r.key_env && r.key_env.length) meta.push(r.key_env.join(" / "));
    meta.push(r.models != null ? r.models + " models" : "models n/a");
    if (r.serves && r.serves.length) meta.push("serves " + r.serves.join(", "));
    const kids = [h("div", { class: "xc-prow-top" }, h("span", { class: "xc-prow-name" }, r.provider), chip(r),
      h("span", { class: "xc-prow-meta" }, meta.map((m) => h("span", null, m))), r.default ? h("span", { class: "xc-defmark" }, "default") : null)];
    if (r.needs === "key") {
      const input = h("input", { class: "xc-field", type: "password", autocomplete: "new-password", spellcheck: "false",
        autocapitalize: "off", placeholder: r.key === "found" ? "replace the key" : "paste the key", "aria-label": "API key for " + r.provider });
      const form = h("form", { class: "xc-prow-act", onsubmit: (e) => { e.preventDefault(); save(r, input); } }, input,
        h("button", { class: "xc-btn", type: "submit" }, "Save"),
        h("button", { class: "xc-btn", type: "button", onclick: () => test(r) }, "Test"),
        h("button", { class: "xc-btn", type: "button", onclick: () => remove(r) }, "Remove"));
      kids.push(form);
    } else {
      kids.push(h("div", { class: "xc-prow-act" }, h("button", { class: "xc-btn", type: "button", onclick: () => test(r) }, "Test")));
    }
    const res = results[r.provider];
    if (res) kids.push(h("div", { class: "xc-prov-res " + (res.cls || ""), role: "status" }, res.text));
    return h("div", { class: "xc-prow" + (r.default ? " def" : "") }, kids);
  }

  async function saveDefault() {
    try {
      const d = await api("POST", "/api/providers/default", { ref: picked, role: "main" });
      show("_default", "Default set to " + picked + (d.applied ? "." : ". Saved; the core will use it on next start."), "ok");
    } catch (e) { show("_default", e.message, "bad"); }
    if (alive) load();
  }

  function picker() {
    const routes = [];
    for (const m of (models && models.models) || []) if (m.route && !routes.includes(m.route)) routes.push(m.route);
    const more = [];
    for (const m of (models && models.available) || []) if (m.route && !routes.includes(m.route) && !more.includes(m.route)) more.push(m.route);
    if (!routes.length && !more.length) return null;
    if (!picked) picked = routes[0] || more[0];
    const opt = (v) => h("option", { value: v, selected: v === picked }, v);
    const sel = h("select", { class: "xc-field", "aria-label": "Default model", onchange: (e) => { picked = e.target.value; } },
      routes.length ? h("optgroup", { label: "configured" }, routes.map(opt)) : null,
      more.length ? h("optgroup", { label: "available" }, more.map(opt)) : null);
    const res = results._default;
    return h("div", { class: "xc-prov-def" }, h("h4", { style: "margin:0" }, "Default model"), sel,
      h("button", { class: "xc-btn xc-primary", type: "button", onclick: saveDefault }, "Save default"),
      res ? h("div", { class: "xc-prov-res " + (res.cls || ""), role: "status" }, res.text) : null);
  }

  function draw() {
    if (err) { body.replaceChildren(errorBox(err)); return; }
    if (!data) { body.replaceChildren(h("div", { class: "xc-loading" }, "Loading providers...")); return; }
    const rows = data.providers || [];
    const active = document.activeElement;
    const keep = active && active.tagName === "INPUT" && active.type === "password" && active.value ? active : null;
    if (keep) return;   // never redraw while a key is being typed
    body.replaceChildren(...[
      h("p", { class: "xc-prov-lead" }, "Keys are stored in " + (STORE_TEXT[data.store] || data.store || "the local store") +
        ". They are sent to this local server once, never shown again, and never kept in the browser."),
      data.ready ? null : h("div", { class: "xc-warn" }, "No model provider is ready yet. Add a key below."),
      rows.length ? h("div", { class: "xc-prov-list" }, rows.map(row)) : empty("No providers known."),
      picker()].filter(Boolean));
  }

  draw();
  load();
  return () => { alive = false; };
}
