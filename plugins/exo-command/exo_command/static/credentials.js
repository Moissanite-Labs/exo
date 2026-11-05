// User-only, write-only credential entry. No credential value enters chat or browser storage.
import { h, api, pageHead } from "./dom.js";

export function mount(root) {
  let alive = true, busy = false, info = null;
  const status = h("p", { role: "status", "aria-live": "polite" });
  const body = h("div", { class: "xc-credentials" }, status);
  root.replaceChildren(h("div", { class: "xc-pg" }, pageHead({ eyebrow: "06 / Credentials", title: "Credentials",
    lede: "Write-only secrets for this core’s seat. Enter values here, never in chat." }), body));
  function show(text) { status.textContent = text; }
  async function load() {
    show("Checking the selected core’s credential capability…");
    try {
      const data = await api("GET", "/api/credentials");
      if (!alive) return;
      info = data; draw(); show("Credential settings loaded.");
    } catch (e) {
      if (alive) body.replaceChildren(status, h("p", null, "Credential setup is unavailable. The selected core may need an upgrade or principal access."),
        h("button", { class: "xc-btn", type: "button", onclick: load }, "Retry"));
      show("Nothing was saved.");
    }
  }
  const field = (label, attrs = {}) => {
    const input = h("input", { class: "xc-field", "aria-label": label, ...attrs });
    return { input, element: h("label", null, label, input) };
  };
  function secret(label) { return field(label, { type: "password", autocomplete: "off", spellcheck: "false", autocapitalize: "off" }); }
  async function run(action, data = {}, input) {
    if (busy || !info) return;
    const request = { action, ...data };
    if (input) { request.value = input.value; input.value = ""; }
    busy = true; show("Working…");
    for (const button of body.querySelectorAll("button")) button.disabled = true;
    try {
      const result = await api("POST", "/api/credentials", request);
      if (!alive) return;
      if (result.cleanup_pending) show("Binding disabled. Keychain deletion did not complete; cleanup is still needed.");
      else if (action.endsWith("remove")) show(result.deleted === true || result.removed === true ? "Binding disabled and stored credential removed." : "Binding disabled; stored credential deletion was not confirmed.");
      else if (action === "github_test") show(result.ok ? "GitHub authenticated as " + result.login + "." : "GitHub authentication failed. Check the account, token and permissions.");
      else if (action === "test") show(result.storage_available ? "The Keychain item is readable. Remote API authentication was not tested." : "The Keychain item is unavailable. Remote API authentication was not tested.");
      else show(result.ok === true ? "Saved in Keychain. New allowed subprocesses resolve it at start; existing processes are unchanged." : "The credential was not saved or confirmed.");
      const message = status.textContent;
      try {
        info = await api("GET", "/api/credentials");
        if (alive) { draw(); show(message); }
      } catch (e) { if (alive) show(message + " The status list could not be refreshed. Reopen Credentials to check it."); }
    } catch (e) { if (alive) show("The operation was not confirmed. Check settings, policy and Keychain availability."); }
    finally {
      delete request.value;
      busy = false;
      for (const button of body.querySelectorAll("button")) button.disabled = false;
      if (alive && info && !info.store_available) for (const button of body.querySelectorAll("[data-needs-keychain]")) button.disabled = true;
    }
  }
  function button(label, action, data, input, requiresStore = true) {
    return h("button", { class: "xc-btn", type: "button", "data-needs-keychain": requiresStore || null,
      disabled: busy || (requiresStore && !info.store_available), onclick: () => run(action, data(), input) }, label);
  }
  function draw() {
    const gh = info.github || {};
    const host = field("GitHub host", { value: gh.host || "github.com", autocomplete: "off" });
    const githubKey = secret("GitHub token (write-only)");
    const name = field("Service name", { placeholder: "weather", autocomplete: "off" });
    const env = field("Allowed variable name", { placeholder: "EXO_SECRET_WEATHER", autocomplete: "off" });
    const key = secret("API key (write-only)");
    const consumers = ["bash", "jobs"].map(id => ({ id, check: h("input", { type: "checkbox", "aria-label": "Allow " + id }) }));
    const rows = (info.credentials || []).map(row => h("section", { class: "xc-credential-row" },
      h("strong", null, row.name), h("p", null, row.env + " → " + (row.consumers || []).join(", ") + " · " + (row.enabled ? "configured, not checked" : "disabled")
        + (row.policy_blocked ? " · denied by seat policy" : "") + (row.cleanup_pending ? " · cleanup pending" : "")),
      button("Check storage", "test", () => ({ name: row.name })),
      button("Remove", "remove", () => ({ name: row.name }), null, false)));
    body.replaceChildren(status,
      h("p", null, "Selected core seat: ", h("strong", null, info.seat), " · macOS Keychain"),
      h("p", null, info.store_available ? "No plaintext fallback. Stored values are never returned to this page." : "Keychain is unavailable. Saving is disabled; there is no plaintext fallback."),
      h("section", { class: "xc-credential-section", "aria-label": "GitHub credentials" },
        h("h2", null, "Separate GitHub account"),
        h("p", null, "Mode: " + (gh.mode || "not configured") + (gh.secure ? " · Keychain token configured, not checked" : " · no secure token configured")),
        h("p", null, "Create the separate account and token yourself, then enter its token here. This does not create an account or change the machine’s login."),
        host.element, githubKey.element,
        button("Save or rotate GitHub token", "github_set", () => ({ host: host.input.value }), githubKey.input),
        button("Test GitHub account", "github_test", () => ({})),
        button("Disable GitHub credentials", "github_remove", () => ({}), null, false)),
      h("section", { class: "xc-credential-section", "aria-label": "API key bindings" },
        h("h2", null, "API key binding"),
        h("p", null, "Allow only the named environment variable in the consumers you select. No provider, connector or other process receives it automatically. Existing approval rules still apply."),
        name.element, env.element, key.element,
        h("div", null, ...consumers.map(({ id, check }) => h("label", { class: "xc-credential-consumer" }, check, "Allow " + id))),
        button("Save or rotate API key", "set", () => ({ name: name.input.value, env: env.input.value,
          consumers: consumers.filter(c => c.check.checked).map(c => c.id) }), key.input),
        h("p", null, "Use the same service name to rotate a key. Only new subprocesses see the replacement.")),
      h("section", { class: "xc-credential-section", "aria-label": "Allowed secret variable catalog" }, h("h2", null, "Allowed variable catalog"),
        rows.length ? rows : h("p", null, "No API key bindings configured.")));
  }
  load();
  return () => {
    alive = false;
    for (const input of body.querySelectorAll("input[type=password]")) input.value = "";
  };
}
