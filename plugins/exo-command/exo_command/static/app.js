// exo-command web UI. Plain ES modules, no build step, no dependencies, no network beyond this server.
// Chat is the default page; Tasks, Schedule and Command (the read-mostly overview) are the other views.
import { h, api } from "./dom.js";
import * as bus from "./bus.js";
import * as chat from "./chat.js";
import * as tasks from "./tasks.js";
import * as schedule from "./schedule.js";
import * as command from "./command.js";
import * as providers from "./providers.js";

const VIEWS = [["chat", "Chat", chat], ["tasks", "Tasks", tasks], ["schedule", "Schedule", schedule], ["command", "Command", command], ["providers", "Providers", providers]];

// The page token for provider routes arrives in the URL fragment (`exo web` prints it). Keep it in this tab's
// sessionStorage only, then take it out of the address bar and history.
function takeToken() {
  const parts = (location.hash || "").slice(1).split("&").filter(Boolean);
  const t = parts.find((x) => x.startsWith("token="));
  if (!t) return;
  const value = decodeURIComponent(t.slice(6));
  if (value) sessionStorage.setItem("xc-token", value);
  const view = parts.find((x) => !x.startsWith("token=")) || "chat";
  history.replaceState(null, "", location.pathname + location.search + "#" + view);
}
takeToken();
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
  const v = VIEWS.find((x) => x[0] === id) || VIEWS[0];
  if (unmount) { try { unmount(); } catch (e) { console.error(e); } }
  current = v[0];
  if (location.hash !== "#" + current) history.replaceState(null, "", "#" + current);
  where.textContent = "";
  dot.classList.remove("on");
  nav.replaceChildren(h("strong", { class: "xc-brand" }, dot, "E X O"), where,
    ...VIEWS.map((x) => h("button", { class: x[0] === current ? "on" : "", role: "tab", onclick: () => show(x[0]) }, x[1])), conn);
  main.replaceChildren();
  unmount = v[2].mount(main) || null;
}

bus.onState((s) => {
  connText.textContent = s.connected ? "live" : "reconnecting";
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

app.replaceChildren(nav, main);
boot([
  ["core", async () => ((await api("GET", "/api/health")).connected ? "connected" : null)],
  ["agent", async () => { const s = (await api("GET", "/api/chat/sessions")).sessions || []; return s.length ? (s[0].agent || s[0].seat || "exo") + " · " + s.length + " thread" + (s.length > 1 ? "s" : "") : null; }],
  ["memory", async () => { const s = (await api("GET", "/api/chat/sessions")).sessions || []; return s.length ? s.reduce((n, x) => n + (x.message_count || 0), 0) + " messages on record" : null; }],
  ["model route", async () => { const s = (await api("GET", "/api/chat/sessions")).sessions || []; return (s[0] && s[0].model) || null; }],
]);
await bus.start();   // loads open panels, then follows the event stream
show((location.hash || "").slice(1));
window.addEventListener("hashchange", () => { const id = (location.hash || "").slice(1); if (id !== current) show(id); });
