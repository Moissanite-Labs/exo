// Providers view: where model keys go, per-provider status, key save/test/remove, and the default model.
// Drawn as an instrument panel (D-061): one ruled row per provider, status in a pill whose led form carries it.
// A key value only ever lives in the password field. It is read once to build the request body, the field is
// cleared at once (also on error), and it is never logged, stored, put in a URL, or shown again.
import { h, api, errorBox, pageHead, readout, pill, emptyState } from "./dom.js";

import { createModelPicker } from "./model_picker.js";
import { mount as mountReasoning } from "./reasoning.js";

const STORE_TEXT = {
  keychain: "the macOS Keychain",
  "secret-service": "the system keyring (Secret Service)",
  file: "a private keys file in your Exo home (mode 600)",
};

function chip(r) {
  const st = r.status || "";
  const lt = r.last_test || {};
  if (r.needs === "chatgpt") return pill(r.ready ? "ChatGPT connected" : "not connected", r.ready ? "known" : "off");
  if (r.verified) return pill("verified" + (lt.latency_ms != null ? " · " + Math.round(lt.latency_ms) + " ms" : ""), "ok");
  if (st.startsWith("failed")) return pill(st, "fail");
  if (st === "no base_url") return pill(st, "off");  // nothing to call: the router skips it even with a key
  if (r.needs === "key") return pill(r.key === "found" ? "key found" : "no key", r.key === "found" ? "known" : "off");
  if (r.needs === "cli") return pill(r.ready ? "cli installed" : "cli not installed", r.ready ? "known" : "off");
  return pill(r.local ? (r.reachable ? "local · running" : "local · not running") : st, r.reachable ? "ok" : "off");
}

export function mount(root) {
  const body = h("div", { class: "xc-prov" });
  const connection = h("section", { class: "xc-prov-def", "aria-label": "ChatGPT subscription connection" });
  const counts = h("div", { class: "xc-phead-read" });
  const reasoningRoot = h("div");
  root.replaceChildren(h("div", { class: "xc-pg" }, pageHead({ eyebrow: "05 / Providers", title: "Providers",
    lede: "Where model keys live, which providers answer, and the model the main role uses.", aside: counts }), connection, reasoningRoot, body));
  let alive = true, authTimer = null, authBusy = false, authState = null;

  function drawChatGPT(message = "") {
    if (!alive) return;
    const d = authState;
    const note = message || (d && d.dependency_available === false ? "Install Exo’s chatgpt extra in the core runtime before signing in." : "") || (d && d.message) || (d && d.available ? "Using ChatGPT plan when a chatgpt model is selected." : "Connect your ChatGPT subscription separately from API keys and CLI logins.");
    const button = (label, action, profile) => h("button", { class: "xc-btn", type: "button", disabled: authBusy,
      onclick: () => chatGPT(action, profile) }, label);
    connection.replaceChildren(h("div", null, h("h2", null, "ChatGPT subscription"),
      h("p", { role: "status" }, note),
      h("p", null, "Connecting does not change your default model or enable paid API fallback."),
      button("Continue with ChatGPT", "start"),
      d && d.pending ? button("Cancel sign-in", "cancel") : null,
      h("a", { class: "xc-btn", href: "https://chatgpt.com/settings/usage", target: "_blank", rel: "noopener noreferrer" }, "Manage ChatGPT usage"),
      ...((d && d.profiles) || []).map((p) => h("div", { class: "xc-prow" },
        h("span", null, (p.email || "ChatGPT account") + " · registration " + p.client_id.slice(-8) +
          (d.active === p.profile ? " · active" : "") + (p.plan_enabled ? "" : " · plan usage not enabled")),
        button("Sign in to this account", "start", p.profile), button("Disconnect", "disconnect", p.profile)))));
  }

  async function refreshChatGPT() {
    try {
      const previous = authState;
      authState = await api("GET", "/api/providers/chatgpt"); drawChatGPT();
      if (alive && previous && ((previous.pending && !authState.pending) || previous.active !== authState.active)) load();
    }
    catch (_) { drawChatGPT("ChatGPT connection status is unavailable. This core may need an upgrade."); }
    if (authTimer) clearTimeout(authTimer);
    if (alive && authState && authState.pending) authTimer = setTimeout(refreshChatGPT, 1500);
  }

  async function chatGPT(action, profile) {
    if (authBusy) return;
    authBusy = true; drawChatGPT("Updating ChatGPT connection...");
    try {
      authState = await api("POST", "/api/providers/chatgpt", { action, ...(profile ? { profile } : {}) });
      authBusy = false; drawChatGPT();
      if (alive) { refreshChatGPT(); load(); }
    } catch (_) { authBusy = false; drawChatGPT("ChatGPT connection action failed. The previous active connection is unchanged unless you chose Disconnect."); }
  }


  if (!sessionStorage.getItem("xc-token")) {
    body.replaceChildren(emptyState({ k: "Page token missing", title: "Open the link exo web printed.",
      body: "It carries a page token that this page needs to set up providers. The bare address has none." }));
    return () => { alive = false; };
  }

  const stopReasoning = mountReasoning(reasoningRoot);
  const results = {};   // provider name -> {text, cls}; kept across redraws, never holds a key
  let data = null, models = null, err = "", picked = "", modelsError = "", savingDefault = false, loadGeneration = 0;
  let defaultPicker = null, pickerSelection = null, pickerModels = null, pickerError = null;

  async function load() {
    const generation = ++loadGeneration;
    try {
      const next = await api("GET", "/api/providers");
      let nextModels = null, nextError = "";
      try { nextModels = await api("GET", "/api/providers/models"); } catch (e) { nextError = e.message || "Models could not be loaded."; }
      if (!alive || generation !== loadGeneration) return;
      data = next; models = nextModels; modelsError = nextError; err = "";
    } catch (e) { if (generation === loadGeneration) err = e.message; }
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
    } else if (r.needs === "chatgpt") {
      kids.push(h("p", { class: "xc-prow-act" }, "Manage this connection in ChatGPT subscription above."));
    } else {
      kids.push(h("div", { class: "xc-prow-act" }, h("button", { class: "xc-btn", type: "button", onclick: () => test(r) }, "Test")));
    }
    const res = results[r.provider];
    if (res) kids.push(h("div", { class: "xc-prov-res " + (res.cls || ""), role: "status" }, res.text));
    return h("div", { class: "xc-prow" + (r.default ? " def" : ""), role: "listitem" }, kids);
  }

  async function saveDefault(ref) {
    if (!alive || savingDefault) return;
    savingDefault = true;
    try {
      const d = await api("POST", "/api/providers/default", { ref, role: "main" });
      if (!alive) return;
      picked = ref;
      results._default = { text: "Default set to " + ref + ((d.applied === true || d.applied === "now") ? "." : ". Saved; the core will use it on next start."), cls: "ok" };
    } catch (e) { if (alive) throw e; }
    finally { savingDefault = false; }
    // Let the picker finish and restore focus before a background refresh.
    setTimeout(() => { if (alive) load(); }, 0);
    return results._default && results._default.text;
  }

  function picker() {
    const rows = [...((models && models.models) || []), ...((models && models.available) || [])];
    if (!defaultPicker || pickerSelection !== picked) {
      const query = defaultPicker?.element.querySelector(".xc-model-search")?.value || "";
      defaultPicker = createModelPicker({ label: "Default model", rows: modelsError ? [] : rows, selected: picked,
        applyLabel: "Save default", note: "The route the main role calls first.", onApply: saveDefault });
      const field = defaultPicker.element.querySelector(".xc-model-search");
      field.value = query; field.dispatchEvent(new Event("input"));
      pickerSelection = picked;
    } else if (pickerModels !== models || pickerError !== modelsError) defaultPicker.setRows(modelsError ? [] : rows);
    pickerModels = models; pickerError = modelsError;
    if (modelsError) defaultPicker.setError(modelsError + " Reopen Providers to retry.");
    const chosen = defaultPicker;
    const res = results._default;
    if (res && !modelsError) chosen.element.querySelector(".xc-model-status").replaceChildren(res.text);
    return h("section", { class: "xc-prov-def", "aria-label": "Default model" }, chosen.element,
      res ? h("div", { class: "xc-prov-res " + (res.cls || ""), role: "status" }, res.text) : null);
  }

  function draw() {
    if (err) { body.replaceChildren(errorBox(err)); return; }
    if (!data) { body.replaceChildren(h("div", { class: "xc-loading" }, "Loading providers...")); return; }
    const rows = data.providers || [];
    const active = document.activeElement;
    const keep = active && active.tagName === "INPUT" && active.type === "password" && active.value;
    const searchFocused = active?.classList.contains("xc-model-search");
    const selection = searchFocused ? [active.selectionStart, active.selectionEnd] : null;
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
    if (searchFocused) {
      const field = defaultPicker.element.querySelector(".xc-model-search");
      field.focus(); field.setSelectionRange(...selection);
    }
  }

  draw();
  load();
  refreshChatGPT();
  return () => { alive = false; stopReasoning(); if (authTimer) clearTimeout(authTimer); };
}
