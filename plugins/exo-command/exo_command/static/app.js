// exo-command web UI. Plain ES modules, no build step, no dependencies, no network beyond this server.
// Chat is the default page; Tasks, Schedule and Command (the read-mostly overview) are the other views.
import { h } from "./dom.js";
import * as bus from "./bus.js";
import * as chat from "./chat.js";
import * as tasks from "./tasks.js";
import * as schedule from "./schedule.js";
import * as command from "./command.js";

const VIEWS = [["chat", "Chat", chat], ["tasks", "Tasks", tasks], ["schedule", "Schedule", schedule], ["command", "Command", command]];
const app = document.getElementById("app");
const main = h("div", { class: "xc-main" });
const nav = h("nav", { class: "xc-nav", role: "tablist" });
const conn = h("span", { class: "xc-dim xc-conn" });
let unmount = null, current = null;

function show(id) {
  const v = VIEWS.find((x) => x[0] === id) || VIEWS[0];
  if (unmount) { try { unmount(); } catch (e) { console.error(e); } }
  current = v[0];
  if (location.hash !== "#" + current) history.replaceState(null, "", "#" + current);
  nav.replaceChildren(h("strong", { class: "xc-brand" }, "exo"),
    ...VIEWS.map((x) => h("button", { class: x[0] === current ? "on" : "", role: "tab", onclick: () => show(x[0]) }, x[1])), conn);
  main.replaceChildren();
  unmount = v[2].mount(main) || null;
}

bus.onState((s) => { conn.textContent = s.connected ? "live" : "reconnecting"; });
app.replaceChildren(nav, main);
await bus.start();   // loads open panels, then follows the event stream
show((location.hash || "").slice(1));
