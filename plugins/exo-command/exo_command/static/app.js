import { desktopPreference, saveDesktopPreferences } from "./desktop_prefs.js";
// exo-command web UI. Plain ES modules, no build step, no dependencies, no network beyond this server.
// Chat is the default page; Tasks, Schedule and Command (the read-mostly overview) are the other views.
import { h, token, redeem } from "./dom.js";
import { getAppearance, applyAppearance, setAppearance } from "./appearance.js";
import * as bus from "./bus.js";
import * as chat from "./chat.js";
import * as tasks from "./tasks.js";
import * as schedule from "./schedule.js";
import * as command from "./command.js";
import * as providers from "./providers.js";
import * as permissions from "./permissions.js";

const VIEWS = [["chat", "Chat", chat], ["tasks", "Tasks", tasks], ["schedule", "Schedule", schedule], ["command", "Command", command], ["providers", "Providers", providers]];

// The page token arrives in the URL fragment of the link `exo web` prints; every request needs it (D-039). Keep it in this tab's
// sessionStorage only, then take it out of the address bar and history.
let launchRefused = "";   // why a #code= link gave no token: shown instead of a view
function takeToken() {
  const parts = (location.hash || "").slice(1).split("&").filter(Boolean);
  const t = parts.find((x) => x.startsWith("token="));
  if (!t) return;
  let value;
  try { value = decodeURIComponent(t.slice(6)); } catch (e) { return false; }
  if (value) { sessionStorage.setItem("xc-token", value); launchRefused = ""; }
  const view = parts.find((x) => !x.startsWith("token=")) || desktopPreference("view") || "chat";
  history.replaceState(null, "", location.pathname + location.search + "#" + view);
  return !!value;
}

// The browser `exo web` opens gets a one-time #code= instead, since a launcher's argv is readable on the host (#166).
// Out of the address bar before the request, so a reload cannot send it twice; then traded for the token once.
async function takeCode() {
  const parts = (location.hash || "").slice(1).split("&").filter(Boolean);
  const c = parts.find((x) => x.startsWith("code="));
  if (!c) return false;
  const view = parts.find((x) => !x.startsWith("code=")) || desktopPreference("view") || "chat";
  history.replaceState(null, "", location.pathname + location.search + "#" + view);
  let got = {};
  try { got = await redeem(decodeURIComponent(c.slice(5))); } catch (e) { got = {}; }
  const value = got.token || "";
  const why = got.error || "";
  // paste, not click: a click in the terminal hands the #token= link to the URL opener's argv again (#166)
  launchRefused = value ? "" : (!why || why.includes("launch code")
    ? "This browser link was already used or has expired. Paste the link exo web printed in the terminal into this tab."
    : "This browser link was refused (" + why + "). Paste the link exo web printed in the terminal into this tab.");
  if (value) sessionStorage.setItem("xc-token", value);
  return !!value;
}
takeToken();
await takeCode();
const app = document.getElementById("app");
const main = h("main", { class: "xc-main", id: "xc-main", tabindex: "-1", "aria-label": "Workspace" });
const nav = h("nav", { class: "xc-nav", "aria-label": "Primary" });
const dot = h("span", { class: "xc-dot", id: "xc-live", title: "a turn is running" });
const where = h("span", { class: "xc-where", id: "xc-where" });
const led = h("span", { class: "xc-dot" });
const connText = h("span", null, "");
const conn = h("span", { class: "xc-conn", role: "status", "aria-live": "polite", "aria-atomic": "true" }, led, connText);
let unmount = null, current = null, workspaceReady = false, authGeneration = 0;
let connecting = Promise.resolve();
const appearance = h("select", { class: "xc-appearance", "aria-label": "Appearance",
  onchange: () => { appearance.value = setAppearance(appearance.value); saveDesktopPreferences({ appearance: appearance.value }); } },
  ...[["dark", "Dark"], ["light", "Light"], ["system", "System"]].map(([value, label]) => h("option", { value }, label)));
appearance.value = applyAppearance(desktopPreference("appearance") || getAppearance());
const appearanceControl = h("label", { class: "xc-appearance-wrap" }, h("span", null, "Appearance"), appearance);
const links = VIEWS.map(([id, label]) => h("a", { class: "xc-nav-link", href: "#" + id }, label));
nav.replaceChildren(h("strong", { class: "xc-brand" },
  h("img", { class: "xc-brand-mark", src: "/static/brand.svg", alt: "", width: "28", height: "28", "aria-hidden": "true" }), dot, "Exo"),
  where, ...links, appearanceControl, conn);
const skip = document.getElementById("xc-skip");
if (skip && skip.addEventListener) skip.addEventListener("click", (event) => { event.preventDefault(); main.focus(); });

function show(id) {
  if (!token()) {
    main.replaceChildren(h("p", { role: "status" }, launchRefused
      || "Open the complete link printed by exo web, including #token=… . The bare address has no page token."));
    return;
  }
  const v = VIEWS.find((x) => x[0] === id) || VIEWS[0];
  if (unmount) { try { unmount(); } catch (e) { console.error(e); } }
  current = v[0];
  saveDesktopPreferences({ view: current });
  if (location.hash !== "#" + current) history.replaceState(null, "", "#" + current);
  where.textContent = "";
  dot.classList.remove("on");
  links.forEach((link, index) => {
    const active = VIEWS[index][0] === current;
    link.className = "xc-nav-link" + (active ? " on" : "");
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  document.title = v[1] + " | Exo";
  main.replaceChildren();
  unmount = null;
  if (!workspaceReady) {
    main.replaceChildren(h("p", { class: "xc-loading", role: "status" }, "Connecting to the workspace…"));
    return;
  }
  unmount = v[2].mount(main) || null;
}

bus.onState((s) => {
  connText.textContent = !token() ? "Page token missing" : s.denied
    ? "Page link expired: open the latest complete link printed by exo web"
    : s.connected ? "Connected" : "Reconnecting";
  led.className = "xc-dot " + (s.connected ? "ok" : "");
});

const permBanner = permissions.banner();   // D-065: on every page while the core's gate is in bypass
app.replaceChildren(nav, permBanner, main);
// Keep real bootstrap progress inline. Navigation is usable while the initial panel snapshot is read.
show((location.hash || "").slice(1) || desktopPreference("view"));
window.addEventListener("hashchange", async () => {
  const fresh = takeToken() || await takeCode();
  if (fresh) {
    const generation = ++authGeneration; workspaceReady = false;
    await connecting;  // a previous bootstrap cannot overwrite the fresh connection's panel snapshot
    if (generation !== authGeneration) return;
    connecting = bus.restart();
    await connecting;
    if (generation !== authGeneration) return;
    workspaceReady = true;
    permissions.start();
  }
  const id = (location.hash || "").slice(1);
  if (fresh || launchRefused || id !== current) show(id);
});

if (token()) {
  const initialAuth = authGeneration;
  connecting = bus.start();   // loads open panels before Chat reads them, then follows the event stream
  await connecting;
  if (initialAuth === authGeneration) {
    workspaceReady = true;
    permissions.start();
    show((location.hash || "").slice(1));
  }
}

// The pet control is supplied by the optional companion module; retain its display choice when present.
window.addEventListener("change", (event) => { if (event.target?.matches?.(".xc-pet-mode")) saveDesktopPreferences({ petMode: event.target.value }); });
