// A small, safe markdown renderer for panels: headings, lists, code fences, bold, italics, inline
// code, pipe tables and http(s) links. It builds DOM nodes, so nothing the model writes can become markup.
import { h } from "./dom.js";

function inline(text) {
  const out = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)|(\[[^\]]+\]\((https?:\/\/[^)\s]+)\))/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1]) out.push(h("code", null, m[1].slice(1, -1)));
    else if (m[2]) out.push(h("strong", null, m[2].slice(2, -2)));
    else if (m[3]) out.push(h("em", null, m[3].slice(1, -1)));
    else out.push(h("a", { href: m[5], target: "_blank", rel: "noopener noreferrer" }, m[4].slice(1, m[4].indexOf("]"))));
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// Split only unescaped pipes. Outer pipes are optional; escaped pipes stay cell text.
function tableRow(line) {
  const cells = [];
  let text = "", pipes = 0;
  const src = line.trim();
  for (let i = 0; i < src.length; i++) {
    if (src[i] === "\\" && (src[i + 1] === "|" || src[i + 1] === "\\")) { text += src[++i]; }
    else if (src[i] === "|") { cells.push(text.trim()); text = ""; pipes++; }
    else text += src[i];
  }
  cells.push(text.trim());
  if (!pipes) return null;
  if (src.startsWith("|")) cells.shift();
  if (src.endsWith("|") && cells[cells.length - 1] === "") cells.pop();
  return cells.length ? cells : null;
}

function tableStart(lines, i) {
  const head = tableRow(lines[i]), rule = i + 1 < lines.length ? tableRow(lines[i + 1]) : null;
  if (!head || !rule || head.length !== rule.length || !rule.every(x => /^:?-+:?$/.test(x))) return null;
  return { head, aligns: rule.map(x => x.startsWith(":") ? (x.endsWith(":") ? "center" : "left") : (x.endsWith(":") ? "right" : "left")) };
}

export function renderMarkdown(src) {
  const root = h("div", { class: "xc-md" });
  const lines = String(src || "").replace(/\r/g, "").split("\n");
  let para = [], list = null, i = 0;
  const flushPara = () => { if (para.length) { root.appendChild(h("p", null, inline(para.join(" ")))); para = []; } };
  const flushList = () => { if (list) { root.appendChild(list); list = null; } };
  while (i < lines.length) {
    const ln = lines[i];
    const fence = /^ {0,3}(`{3,}|~{3,})/.exec(ln);
    if (fence) {
      flushPara(); flushList();
      const buf = [];
      i++;
      const close = new RegExp("^ {0,3}" + fence[1][0] + "{" + fence[1].length + ",}\\s*$");
      while (i < lines.length && !close.test(lines[i])) buf.push(lines[i++]);
      root.appendChild(h("pre", { class: "xc-code" }, buf.join("\n")));
      i++;
      continue;
    }
    const start = tableStart(lines, i);
    if (start) {
      flushPara(); flushList();
      const cell = (tag, text, column) => h(tag, { "data-align": start.aligns[column], ...(tag === "th" ? { scope: "col" } : {}) }, h("div", { class: "xc-table-cell" }, inline(text)));
      const body = h("tbody");
      const table = h("table", null, h("thead", null, h("tr", null, start.head.map((text, column) => cell("th", text, column)))), body);
      i += 2;
      while (i < lines.length && lines[i].trim() && !/^ {0,3}(`{3,}|~{3,})/.test(lines[i])) {
        const row = tableRow(lines[i]);
        if (!row) break;
        body.appendChild(h("tr", null, start.head.map((_, column) => cell("td", row[column] || "", column))));
        i++;
      }
      root.appendChild(h("div", { class: "xc-table-scroll", role: "region", "aria-label": "Table", tabindex: "0" }, table));
      continue;
    }
    const hd = /^(#{1,4})\s+(.*)$/.exec(ln);
    const li = /^\s*([-*]|\d+\.)\s+(.*)$/.exec(ln);
    if (hd) { flushPara(); flushList(); root.appendChild(h("h" + (hd[1].length + 2), null, inline(hd[2]))); }
    else if (li) {
      flushPara();
      const ordered = /\d/.test(li[1]);
      if (!list || (list.tagName === "OL") !== ordered) { flushList(); list = h(ordered ? "ol" : "ul"); }
      list.appendChild(h("li", null, inline(li[2])));
    } else if (!ln.trim()) { flushPara(); flushList(); }
    else { flushList(); para.push(ln.trim()); }
    i++;
  }
  flushPara(); flushList();
  return root;
}
