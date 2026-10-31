// Providers view: where model keys go, per-provider status, key save/test/remove, and the default model.
// Drawn as an instrument panel (D-061): one ruled row per provider, status in a pill whose led form carries it.
// A key value only ever lives in the password field. It is read once to build the request body, the field is
// cleared at once (also on error), and it is never logged, stored, put in a URL, or shown again.
import { h, api, errorBox, pageHead, readout, pill, emptyState } from "./dom.js";
import { workerModels } from "./worker_models.js";

const STORE_TEXT = {
  keychain: "the macOS Keychain",
  "secret-service": "the system keyring (Secret Service)",
  file: "a private keys file in your Exo home (mode 600)",
};

function chip(r) {
  const st = r.status || "";
  const lt = r.last_test || {};
  if (r.verified) return pill("verified" + (lt.latency_ms != null ? " · " + Math.round(lt.latency_ms) + " ms" : ""), "ok");
  if (st.startsWith("failed")) return pill(st, "fail");
  if (st === "no base_url") return pill(st, "off");  // nothing to call: the router skips it even with a key
  if (r.needs === "key") return pill(r.key === "found" ? "key found" : "no key", r.key === "found" ? "known" : "off");
  if (r.needs === "cli") return pill(r.ready ? "cli installed" : "cli not installed", r.ready ? "known" : "off");
  return pill(r.local ? (r.reachable ? "local · running" : "local · not running") : st, r.reachable ? "ok" : "off");
}

export function mount(root) {
  const body = h("div", { class: "xc-prov" });
  const workers = h("div", { class: "xc-prov" });
  const counts = h("div", { class: "xc-phead-read" });
  const workerShortcut = h("button", { class: "xc-btn", type: "button", onclick: () => {
    workers.scrollIntoView({ block: "start" });
    workers.querySelector("input")?.focus({ preventScroll: true });
  } }, "Worker models");
  root.replaceChildren(h("div", { class: "xc-pg" }, pageHead({ eyebrow: "05 / Providers", title: "Providers",
    lede: "Where model keys live, which providers answer, and the models for main and worker tasks.",
    aside: h("div", null, workerShortcut, counts) }), body, workers));
  let alive = true;

  if (!sessionStorage.getItem("xc-token")) {
    body.replaceChildren(emptyState({ k: "Page token missing", title: "Open the link exo web printed.",
      body: "It carries a page token that this page needs to set up providers. The bare address has none." }));
    return () => { alive = false; };
  }

  const results = {};   // provider name -> {text, cls}; kept across redraws, never holds a key
  const closeWorkers = workerModels(workers);
  let data = null, models = null, modelsError = null, err = "", picked = "";

  // D-091: a failed model list says which failure it was. A transport failure can be retried; an authority
  // refusal cannot, so it gets no button. A failed retry keeps the last good list, so the picker stays usable.
  async function loadModels() {
    try {
      const d = await api("GET", "/api/providers/models");
      if (d && d.error) modelsError = { text: d.error, cls: d.error_class || "other", retryable: d.retryable !== false };
      else { models = d; modelsError = null; }
    } catch (e) {
      modelsError = { text: e.message || "The model list could not be read.", cls: "other", retryable: true };
    }
  }

  async function load() {
    try {
      data = await api("GET", "/api/providers");
      err = "";
    } catch (e) { err = e.message; }
    await loadModels();
    if (alive) draw();
  }

  async function retryModels() {
    modelsError = null;
    if (alive) draw();
    await loadModels();
    if (alive) draw();
  }

  function show(name, text, cls) { results[name] = { text, cls }; if (alive) draw(); }

  async function save(r, input) {
    const body = { key: input.value };
    input.value = "";                                   // cleared before the request, so it is gone on any outcome
    if (!body.key.trim()) { show(r.provider, "Paste a key first.", "bad"); return; }
    try {
      const d = await api("POST", "/api/providers/" + encodeURIComponent(r.provider) + "/key", body);
      const warning = d.warning || (d.shadowed_by_env ? "The env var " + d.var + " shadows it." : "");
      show(r.provider, "Stored in " + d.stored_in + ". Effective source: " + (d.effective_source || "not confirmed") + "." +
        (warning ? " " + warning : "") +
        (d.core_reloaded ? "" : " The core was not told to reload; restart it to refresh credentials."), warning ? "bad" : "ok");
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
    const meta = [];   // [label, value]
    if (r.needs === "key") meta.push(["key", r.key === "found" ? "from " + (r.source || "?") : "missing"]);
    if (r.key_env && r.key_env.length) meta.push(["env", r.key_env.join(" / ")]);
    meta.push(["models", r.models != null ? String(r.models) : "n/a"]);
    if (r.serves && r.serves.length) meta.push(["serves", r.serves.join(", ")]);
    const kids = [h("div", { class: "xc-prow-top" },
      h("div", { class: "xc-prow-id" }, h("span", { class: "xc-prow-name" }, r.provider), r.default ? h("span", { class: "xc-defmark" }, "default") : null),
      h("div", { class: "xc-prow-st" }, chip(r)),
      h("dl", { class: "xc-prow-meta" }, meta.map((m) => h("div", null, h("dt", null, m[0]), h("dd", null, m[1])))))];
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
    return h("div", { class: "xc-prow" + (r.default ? " def" : ""), role: "listitem" }, kids);
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
    if (!routes.length && !more.length) {
      if (!modelsError) return null;
      return h("section", { class: "xc-prov-def", "aria-label": "Default model" },
        h("div", { class: "xc-prov-def-k" }, h("p", { class: "xc-k" }, "Default model"),
          h("p", { class: "xc-prov-def-note" }, "The route the main role calls first.")),
        h("div", { class: "xc-warn", role: "status" }, modelsError.text),
        modelsError.retryable ? h("div", { class: "xc-prov-def-act" },
          h("button", { class: "xc-btn", type: "button", onclick: retryModels }, "Try again")) : null);
    }
    if (!picked) picked = routes[0] || more[0];
    const opt = (v) => h("option", { value: v, selected: v === picked }, v);
    const sel = h("select", { class: "xc-field", "aria-label": "Default model", onchange: (e) => { picked = e.target.value; } },
      routes.length ? h("optgroup", { label: "configured" }, routes.map(opt)) : null,
      more.length ? h("optgroup", { label: "available" }, more.map(opt)) : null);
    const res = results._default;
    return h("section", { class: "xc-prov-def", "aria-label": "Default model" },
      h("div", { class: "xc-prov-def-k" }, h("p", { class: "xc-k" }, "Default model"), h("p", { class: "xc-prov-def-note" }, "The route the main role calls first.")),
      h("div", { class: "xc-prov-def-act" }, sel, h("button", { class: "xc-btn xc-primary", type: "button", onclick: saveDefault }, "Save default")),
      modelsError ? h("div", { class: "xc-warn", role: "status" }, modelsError.text + " This list is from the last good read.") : null,
      res ? h("div", { class: "xc-prov-res " + (res.cls || ""), role: "status" }, res.text) : null);
  }

  function draw() {
    if (err) { body.replaceChildren(errorBox(err)); return; }
    if (!data) { body.replaceChildren(h("div", { class: "xc-loading" }, "Loading providers...")); return; }
    const rows = data.providers || [];
    const active = document.activeElement;
    const keep = active && active.tagName === "INPUT" && active.type === "password" && active.value ? active : null;
    if (keep) return;   // never redraw while a key is being typed
    const ready = rows.filter((r) => r.verified || r.ready).length;
    const def = rows.find((r) => r.default);
    counts.replaceChildren(readout([["Ready", ready + " / " + rows.length, ready ? "ok" : "off"],
      ["Default", def ? def.provider : "not set", def ? "on" : "off"]]));
    body.replaceChildren(...[
      h("p", { class: "xc-prov-lead" }, h("span", { class: "xc-led", "data-s": "known", "aria-hidden": "true" }),
        "Keys are stored in " + (STORE_TEXT[data.store] || data.store || "the local store") +
        ". They are sent to this local server once, never shown again, and never kept in the browser."),
      data.ready || !rows.length ? null : h("div", { class: "xc-warn" }, "No model provider is ready yet. Add a key below."),
      rows.length ? h("div", { class: "xc-prov-list", role: "list" }, rows.map(row)) : emptyState({ k: "No providers",
        title: "This core knows no model provider yet.",
        body: "Providers come from the core's router config: hosted APIs that need a key, command-line tools, and local servers.",
        hint: "Add one to the config or start a local server such as Ollama, then open this tab again." }),
      picker()].filter(Boolean));
  }

  draw();
  load();
  return () => { alive = false; closeWorkers(); };
}
