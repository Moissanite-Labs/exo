// Providers view: where model keys go, per-provider status, key save/test/remove, and the default model.
// Drawn as an instrument panel (D-061): one ruled row per provider, status in a pill whose led form carries it.
// A key value only ever lives in the password field. It is read once to build the request body, the field is
// cleared at once (also on error), and it is never logged, stored, put in a URL, or shown again.
import { h, api, errorBox, pageHead, readout, pill, emptyState } from "./dom.js";
import { createModelPicker } from "./model_picker.js";

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
  const counts = h("div", { class: "xc-phead-read" });
  root.replaceChildren(h("div", { class: "xc-pg" }, pageHead({ eyebrow: "05 / Providers", title: "Providers",
    lede: "Where model keys live, which providers answer, and the model the main role uses.", aside: counts }), body));
  let alive = true;

  if (!sessionStorage.getItem("xc-token")) {
    body.replaceChildren(emptyState({ k: "Page token missing", title: "Open the link exo web printed.",
      body: "It carries a page token that this page needs to set up providers. The bare address has none." }));
    return () => { alive = false; };
  }

  // One picker for the whole visit. A redraw must not throw away a search, an arrow-key position or the
  // focus, so the section holding it is built once and only its notes are rewritten in place.
  const defNote = h("div");
  const defRetry = h("div");
  const defRes = h("div");
  const chosen = createModelPicker({ label: "Default model", rows: null, selected: "", applyLabel: "Save default",
    note: "The route the main role calls first.", onApply: saveDefault });
  const defSection = h("section", { class: "xc-prov-def", "aria-label": "Default model" },
    h("div", { class: "xc-prov-def-k" }, h("p", { class: "xc-k" }, "Default model"), h("p", { class: "xc-prov-def-note" }, "The route the main role calls first.")),
    chosen.element, defNote, defRetry, defRes);
  const rowsRegion = h("div", { class: "xc-prov-rows" });
  body.replaceChildren(rowsRegion, defSection);

  const results = {};   // provider name -> {text, cls}; kept across redraws, never holds a key
  let data = null, models = null, modelsError = null, err = "", picked = "", savingDefault = false, loadGeneration = 0;

  // D-091: a failed model list says which failure it was. A transport failure can be retried; an authority
  // refusal cannot, so it gets no button. A failed retry keeps the last good list, so the picker stays usable.
  // A load that a newer one superseded is dropped, so a slow first answer cannot overwrite a newer list.
  async function loadModels(generation) {
    let nextModels = models, nextError = null;
    try {
      const d = await api("GET", "/api/providers/models");
      if (d && d.error) nextError = { text: d.error, cls: d.error_class || "other", retryable: d.retryable !== false };
      else { nextModels = d; nextError = null; }
    } catch (e) {
      nextError = { text: e.message || "The model list could not be read.", cls: "other", retryable: true };
    }
    if (!alive || generation !== loadGeneration) return;
    models = nextModels; modelsError = nextError;
  }

  async function load() {
    const generation = ++loadGeneration;
    try {
      const next = await api("GET", "/api/providers");
      if (!alive || generation !== loadGeneration) return;
      data = next;
      err = "";
      await loadModels(generation);
    } catch (e) { if (generation === loadGeneration) err = e.message; }
    if (alive && generation === loadGeneration) draw();
  }

  async function retryModels() {
    const generation = ++loadGeneration;
    modelsError = null;   // the list stays while the retry runs, so the picker does not empty out
    if (alive) draw();
    await loadModels(generation);
    if (alive && generation === loadGeneration) draw();
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

  // One default change at a time: the picker waits for the answer, so a second Enter is not a second POST.
  async function saveDefault(ref) {
    if (!alive || savingDefault) return "";
    savingDefault = true;
    try {
      const d = await api("POST", "/api/providers/default", { ref, role: "main" });
      if (!alive) return "";
      picked = ref;
      // "next start" is a real answer, not a falsy one: the core took the change but still runs the old route.
      results._default = { text: "Default set to " + ref + ((d.applied === true || d.applied === "now") ? "." : ". Saved; the core will use it on next start."), cls: "ok" };
    } catch (e) {
      if (!alive) return "";
      results._default = { text: e.message, cls: "bad" };
      throw e;   // the picker shows it beside the field it came from
    } finally { savingDefault = false; }
    // Let the picker finish and restore focus before a background refresh.
    setTimeout(() => { if (alive) load(); }, 0);
    return results._default.text;
  }

  // The route the "Current" mark names: the one this visit saved, else the route of the provider holding the
  // default. Without the second half the mark never appears here, because nothing on this page has picked yet.
  function currentRoute() {
    if (picked) return picked;
    const def = data && (data.providers || []).find((r) => r.default);
    if (!def) return "";
    // A row names the provider by id, an entry in the model list names it the way its config shows it:
    // "anthropic" against "Anthropic". Compare the words, not the spelling.
    const words = [def.provider, def.name, def.id].filter(Boolean).map((x) => String(x).toLowerCase());
    const hit = (models.models || []).find((m) => m.route && m.provider && words.includes(String(m.provider).toLowerCase()));
    return hit ? hit.route : "";
  }

  // The models object the picker was last given. Handing it the same list again would reset the row the
  // arrow keys are on, so a redraw that changed nothing leaves the picker alone. The selection is part of
  // "nothing changed": a save can move the mark while the list stays the same.
  let pickerSyncedToList, pickerSyncedToRoute;
  function syncPicker() {
    const cur = currentRoute();
    if (models === pickerSyncedToList && cur === pickerSyncedToRoute) return;
    if (!models && !modelsError) return;   // still loading: the picker already says so
    pickerSyncedToList = models; pickerSyncedToRoute = cur;
    if (!models) {
      // With nothing to pick from, the reason goes where the list would be.
      chosen.setError(modelsError.text + (modelsError.retryable ? "" : " Reopen this tab after the core restarts."));
      return;
    }
    // A searchable list, since routes that share a model name (several providers, one model) are the normal case.
    chosen.setSelected(cur);
    chosen.setRows([
      ...(models.models || []).map(m => ({ ...m, group: "configured" })),
      ...(models.available || []).map(m => ({ ...m, group: "available" })),
    ]);
  }

  // The default-model section is rewritten in place, never replaced: that is what keeps a typed search and
  // the focus inside it alive across the refresh that follows a save.
  function syncDefaultSection() {
    syncPicker();
    // The list stays usable, so the failure is a note above it. A refusal cannot be retried, so it says what can
    // be done instead: the core has to come back before this page can read a list again.
    defNote.replaceChildren(...(modelsError && models
      ? [h("div", { class: "xc-warn", role: "status" }, modelsError.text + " This list is from the last good read." +
          (modelsError.retryable ? "" : " Reopen this tab after the core restarts."))] : []));
    defRetry.replaceChildren(...(modelsError && modelsError.retryable
      ? [h("div", { class: "xc-prov-def-act" }, h("button", { class: "xc-btn", type: "button", onclick: retryModels }, "Try again"))] : []));
    const res = results._default;
    defRes.replaceChildren(...(res ? [h("div", { class: "xc-prov-res " + (res.cls || ""), role: "status" }, res.text)] : []));
  }

  function draw() {
    syncDefaultSection();
    const active = document.activeElement;
    if (active && active.tagName === "INPUT" && active.type === "password" && active.value) return;   // never redraw while a key is being typed
    if (err) { rowsRegion.replaceChildren(errorBox(err)); return; }
    if (!data) { rowsRegion.replaceChildren(h("div", { class: "xc-loading" }, "Loading providers...")); return; }
    const rows = data.providers || [];
    const ready = rows.filter((r) => r.verified || r.ready).length;
    const def = rows.find((r) => r.default);
    counts.replaceChildren(readout([["Ready", ready + " / " + rows.length, ready ? "ok" : "off"],
      ["Default", def ? def.provider : "not set", def ? "on" : "off"]]));
    rowsRegion.replaceChildren(...[
      h("p", { class: "xc-prov-lead" }, h("span", { class: "xc-led", "data-s": "known", "aria-hidden": "true" }),
        "Keys are stored in " + (STORE_TEXT[data.store] || data.store || "the local store") +
        ". They are sent to this local server once, never shown again, and never kept in the browser."),
      data.ready || !rows.length ? null : h("div", { class: "xc-warn" }, "No model provider is ready yet. Add a key below."),
      rows.length ? h("div", { class: "xc-prov-list", role: "list" }, rows.map(row)) : emptyState({ k: "No providers",
        title: "This core knows no model provider yet.",
        body: "Providers come from the core's router config: hosted APIs that need a key, command-line tools, and local servers.",
        hint: "Add one to the config or start a local server such as Ollama, then open this tab again." })].filter(Boolean));
  }

  draw();
  load();
  return () => { alive = false; };
}
