// exo-command web UI. Plain ES modules, no build step, no dependencies, no network beyond this server.
// Chat is the default page; Tasks, Schedule and Command (the read-mostly overview) are the other views.
import { h, api, token, redeem } from "./dom.js";
import * as bus from "./bus.js";
import * as chat from "./chat.js";
import * as tasks from "./tasks.js";
import * as schedule from "./schedule.js";
import * as command from "./command.js";
import * as providers from "./providers.js";
import * as credentials from "./credentials.js";
import * as permissions from "./permissions.js";

const VIEWS = [["chat", "Chat", chat], ["tasks", "Tasks", tasks], ["schedule", "Schedule", schedule], ["command", "Command", command], ["providers", "Providers", providers], ["credentials", "Credentials", credentials]];

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
  const view = parts.find((x) => !x.startsWith("token=")) || "chat";
  history.replaceState(null, "", location.pathname + location.search + "#" + view);
  return !!value;
}

// The browser `exo web` opens gets a one-time #code= instead, since a launcher's argv is readable on the host (#166).
// Out of the address bar before the request, so a reload cannot send it twice; then traded for the token once.
async function takeCode() {
  const parts = (location.hash || "").slice(1).split("&").filter(Boolean);
  const c = parts.find((x) => x.startsWith("code="));
  if (!c) return false;
  const view = parts.find((x) => !x.startsWith("code=")) || "chat";
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
const main = h("div", { class: "xc-main" });
const nav = h("nav", { class: "xc-nav", role: "tablist" });
const dot = h("span", { class: "xc-dot", id: "xc-live", title: "a turn is running" });
const where = h("span", { class: "xc-where", id: "xc-where" });
const led = h("span", { class: "xc-dot" });
const connText = h("span", null, "");
const conn = h("span", { class: "xc-conn" }, led, connText);
let unmount = null, current = null;
const reduced = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function show(id) {
  if (!token()) {
    main.replaceChildren(h("p", { role: "status" }, launchRefused
      || "Open the complete link printed by exo web, including #token=… . The bare address has no page token."));
    return;
  }
  const v = VIEWS.find((x) => x[0] === id) || VIEWS[0];
  if (unmount) { try { unmount(); } catch (e) { console.error(e); } }
  current = v[0];
  if (location.hash !== "#" + current) history.replaceState(null, "", "#" + current);
  where.textContent = "";
  dot.classList.remove("on");
  nav.replaceChildren(h("strong", { class: "xc-brand" }, dot, "E X O"), where,
    ...VIEWS.map((x) => h("button", { class: x[0] === current ? "on" : "", role: "tab", "aria-selected": x[0] === current ? "true" : "false",
      onclick: () => show(x[0]) }, x[1])), conn);
  main.replaceChildren();
  unmount = v[2].mount(main) || null;
}

bus.onState((s) => {
  connText.textContent = s.connected ? "live" : s.denied ? "page link expired: open the latest complete link printed by exo web" : !token() ? "page token missing" : !s.transport_connected ? "web reconnecting"
    : s.core_state === "unavailable" ? "core unavailable" : "connecting to core";
  led.className = "xc-dot " + (s.connected ? "ok" : "");
});

// Boot: draws the brand and runs four honest checks as `·` lines that go green. Under a second, a click or
// key skips it, and it never runs under reduced motion or twice in one tab. The page loads behind it.
function boot(checks) {
  if (reduced() || sessionStorage.getItem("xc-booted") || /[?&]boot=0/.test(location.search)) return null;
  sessionStorage.setItem("xc-booted", "1");
  const el = h("div", { class: "xc-boot", role: "status" }, h("div", { class: "xc-bt" }, "E X O"));
  const lines = checks.map(([label]) => h("div", { class: "xc-bl" }, h("b", null, label), h("i", null, "...")));
  lines.forEach((l, i) => { l.style.animationDelay = (120 + i * 130) + "ms"; el.appendChild(l); });
  el.appendChild(h("div", { class: "xc-bh" }, "any key to skip"));
  app.appendChild(el);
  let gone = false;
  const done = () => {
    if (gone) return; gone = true;
    el.classList.add("out"); setTimeout(() => el.remove(), 300);
    window.removeEventListener("keydown", done);
  };
  el.addEventListener("click", done); window.addEventListener("keydown", done);
  const results = checks.map(([, fn], i) => Promise.resolve().then(fn).catch(() => null).then((t) => {
    const line = lines[i];
    return new Promise((r) => setTimeout(() => { line.lastChild.textContent = t || "unavailable"; if (t) line.classList.add("ok"); r(); }, 160 + i * 130));
  }));
  Promise.all(results).then(() => setTimeout(done, 280));
  return done;
}

const permBanner = permissions.banner();   // D-065: on every page while the core's gate is in bypass
app.replaceChildren(nav, permBanner, main);
if (token()) boot([
  ["core", async () => ((await api("GET", "/api/health")).connected ? "connected" : null)],
  ["agent", async () => { const s = (await api("GET", "/api/chat/sessions")).sessions || []; return s.length ? (s[0].agent || s[0].seat || "exo") + " · " + s.length + " thread" + (s.length > 1 ? "s" : "") : null; }],
  ["memory", async () => { const s = (await api("GET", "/api/chat/sessions")).sessions || []; return s.length ? s.reduce((n, x) => n + (x.message_count || 0), 0) + " messages on record" : null; }],
  ["model route", async () => { const s = (await api("GET", "/api/chat/sessions")).sessions || []; return (s[0] && s[0].model) || null; }],
]);
if (token()) await bus.start();   // loads open panels, then follows the event stream
if (token()) permissions.start();
show((location.hash || "").slice(1));
window.addEventListener("hashchange", async () => {
  const fresh = takeToken() || await takeCode();
  if (fresh) await bus.restart();
  if (fresh) permissions.start();
  const id = (location.hash || "").slice(1);
  if (fresh || launchRefused || id !== current) show(id);
});
