// A small, safe markdown renderer for panels: headings, lists, code fences, bold, italics, inline
// code and http(s) links. It builds DOM nodes, so nothing the model writes can become markup.
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

export function renderMarkdown(src) {
  const root = h("div", { class: "xc-md" });
  const lines = String(src || "").replace(/\r/g, "").split("\n");
  let para = [], list = null, i = 0;
  const flushPara = () => { if (para.length) { root.appendChild(h("p", null, inline(para.join(" ")))); para = []; } };
  const flushList = () => { if (list) { root.appendChild(list); list = null; } };
  while (i < lines.length) {
    const ln = lines[i];
    if (/^```/.test(ln)) {
      flushPara(); flushList();
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      root.appendChild(h("pre", { class: "xc-code" }, buf.join("\n")));
      i++;
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
