// Exopet's local drawing. Geometry follows docs/BRAND.md and site/brand/egg.js.
// This module accepts counts and state only. It owns no data reads, timers or event listeners.
const NS = "http://www.w3.org/2000/svg";
const R = Math.SQRT2 - 1, K = 2 - Math.SQRT2;
export const PET_FORMS = Object.freeze(["egg", "cell", "tile", "cube", "frame", "d4", "d5", "d6", "d7", "d8"]);
const STATES = new Set(["idle", "thinking", "answering", "tool", "waiting", "asking", "error", "done", "learning", "folding", "peer", "job"]);
const ACTIVE = new Set(["thinking", "answering", "tool", "waiting", "asking", "job"]);
const cache = new Map();

function formName(value) { return PET_FORMS.includes(value) ? value : "egg"; }
function count(value, maximum = 512) { return typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.min(maximum, Math.floor(value))) : 0; }

function drawing() {
  const points = [], edges = [], keys = new Map(), links = new Set();
  function point(position, kind = "lattice") {
    const key = position.map(n => n.toFixed(8)).join(",");
    if (keys.has(key)) return keys.get(key);
    const id = points.length;
    points.push({ position, kind }); keys.set(key, id);
    return id;
  }
  function edge(a, b, kind = "lattice") {
    if (a === b) return;
    const key = [Math.min(a, b), Math.max(a, b)].join(":");
    if (!links.has(key)) { edges.push({ a, b, kind }); links.add(key); }
  }
  return { points, edges, point, edge };
}

function square(d, cx, cy) {
  const corners = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([x, y]) => d.point([cx + x, cy + y], "corner"));
  const middle = d.point([cx, cy], "corner");
  for (let i = 0; i < 4; i++) {
    d.edge(corners[i], corners[(i + 1) % 4]); d.edge(corners[i], middle);
    const [x, y] = [[0, -R], [R, 0], [0, R], [-R, 0]][i];
    const dot = d.point([cx + x, cy + y]);
    d.edge(dot, middle); d.edge(dot, corners[i]); d.edge(dot, corners[(i + 1) % 4]);
  }
}

function cube(d, dimension, center, frame = true) {
  const dots = [];
  for (let axis = 0; axis < dimension; axis++) for (const sign of [-1, 1]) {
    const position = center.slice(); position[axis] += K * sign;
    dots.push({ id: d.point(position), axis, sign });
  }
  // A cross-polytope: join every pair except opposite points on the same axis.
  for (let a = 0; a < dots.length; a++) for (let b = a + 1; b < dots.length; b++) {
    if (dots[a].axis !== dots[b].axis) d.edge(dots[a].id, dots[b].id);
  }
  if (!frame) return dots;
  const corners = Array.from({ length: 2 ** dimension }, (_, mask) => ({
    mask, id: d.point(center.map((n, axis) => n + (mask & (1 << axis) ? 1 : -1)), "corner")
  }));
  for (const corner of corners) for (let axis = 0; axis < dimension; axis++) {
    if (!(corner.mask & (1 << axis))) d.edge(corner.id, corners[corner.mask | (1 << axis)].id, "frame");
  }
  for (const dot of dots) for (const corner of corners) {
    if ((corner.mask & (1 << dot.axis) ? 1 : -1) === dot.sign) d.edge(dot.id, corner.id, "frame");
  }
  return dots;
}

function shell(d) {
  // The same outer honeycomb ring and egg projection as the existing brand renderer.
  const ring = [];
  for (let x = -3; x <= 3; x++) for (let y = -3; y <= 3; y++) for (let z = -3; z <= 3; z++) {
    for (let axis = 0; axis < 3; axis++) for (const sign of [-1, 1]) {
      const original = [2 * x, 2 * y, 2 * z]; original[axis] += K * sign;
      const length = Math.hypot(...original);
      if (length < 4.4 || length > 5.6) continue;
      const [a, b, c] = original.map(n => n / length), width = .94 + .1 * b;
      ring.push({ original, id: d.point([a * width, b * 1.2, c * width], "shell") });
    }
  }
  const distance = 2 * R + .001;
  for (let a = 0; a < ring.length; a++) for (let b = a + 1; b < ring.length; b++) {
    if (Math.hypot(...ring[a].original.map((n, i) => n - ring[b].original[i])) < distance) d.edge(ring[a].id, ring[b].id, "shell");
  }
}

export function geometryForForm(value = "egg") {
  const form = formName(value);
  if (cache.has(form)) return cache.get(form);
  const d = drawing();
  if (form === "cell") square(d, 0, 0);
  else if (form === "tile") {
    for (let x = -1; x <= 1; x++) for (let y = -1; y <= 1; y++) square(d, x * 2, y * 2);
  } else if (form === "egg") { cube(d, 3, [0, 0, 0], false); shell(d); }
  else if (form === "frame") {
    const cells = [];
    for (let mask = 0; mask < 8; mask++) {
      const center = [0, 1, 2].map(axis => mask & (1 << axis) ? 1 : -1);
      cells.push(cube(d, 3, center));
    }
    for (let mask = 0; mask < 8; mask++) for (let axis = 0; axis < 3; axis++) {
      if (mask & (1 << axis)) continue;
      const a = cells[mask].find(dot => dot.axis === axis && dot.sign === 1);
      const b = cells[mask | (1 << axis)].find(dot => dot.axis === axis && dot.sign === -1);
      d.edge(a.id, b.id, "bridge");
    }
  } else {
    const dimension = form === "cube" ? 3 : Number(form.slice(1));
    cube(d, dimension, Array(dimension).fill(0));
  }
  if (d.points.length > 512) throw Error("Exopet point budget exceeded");
  const result = Object.freeze({ form,
    points: Object.freeze(d.points.map(p => Object.freeze({ kind: p.kind, position: Object.freeze(p.position) }))),
    edges: Object.freeze(d.edges.map(e => Object.freeze(e))) });
  cache.set(form, result);
  return result;
}

function project(position) {
  if (position.length === 2) return position;
  let x = .84 * position[0] - .63 * position[2];
  let y = -.92 * position[1] + .36 * position[0] + .48 * position[2];
  for (let axis = 3; axis < position.length; axis++) {
    const angle = (axis - 2) * 2.399963229728653;
    x += .62 * Math.cos(angle) * position[axis]; y += .62 * Math.sin(angle) * position[axis];
  }
  return [x, y];
}

function svgNode(tag, attributes = {}) {
  const element = document.createElementNS(NS, tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, String(value));
  return element;
}

export function createPetRenderer(host, options = {}) {
  const svg = svgNode("svg", { viewBox: "0 0 100 100", class: "xc-pet-svg", "aria-hidden": "true", focusable: "false" });
  host.appendChild(svg);
  host.classList.add("xc-pet-glyph");
  if (options.mini) host.classList.add("xc-pet-mini");
  let destroyed = false, animation = null, previous = "", signature = "";
  const pause = () => { if (animation) animation.cancel(); animation = null; };
  function render(input = {}) {
    if (destroyed) return;
    const form = formName(input.form ?? PET_FORMS[count(input.stage, 9)]);
    const state = STATES.has(input.state) ? input.state : "idle";
    const mini = options.mini || host.classList.contains("xc-pet-mini");
    const learned = count(input.learned);
    const reduced = input.reducedMotion ?? options.reducedMotion;
    const systemReduce = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
    const nextSignature = [form, state, mini, learned].join(":");
    if (nextSignature === signature) {
      if (input.active === false || reduced || systemReduce || document.hidden || !host.getClientRects().length || host.closest("[hidden]")) pause();
      return;
    }
    pause();
    signature = nextSignature;
    const geometry = geometryForForm(form);
    // The tiny glyph keeps the central lattice, rather than shrinking hundreds of shell points.
    const all = geometry.points.map((p, id) => ({ ...p, id }));
    const selected = !mini || all.length <= 16 ? all : form === "tile"
      ? all.filter(p => p.position.every(n => Math.abs(n) <= 1))
      : all.filter(p => p.kind === "lattice").slice(0, 16);
    const positions = selected.map(p => project(p.position));
    const center = [0, 1].map(axis => (Math.min(...positions.map(p => p[axis])) + Math.max(...positions.map(p => p[axis]))) / 2);
    const extent = Math.max(1, ...positions.flatMap(p => p.map((n, axis) => Math.abs(n - center[axis]))));
    const scale = (mini ? 37 : 39) / extent;
    const points = positions.map(([x, y]) => [50 + (x - center[0]) * scale, 50 + (y - center[1]) * scale]);
    const indices = new Map(selected.map((p, index) => [p.id, index]));
    const layers = { frame: [], shell: [], lattice: [], bridge: [] };
    for (const edge of geometry.edges) {
      const a = indices.get(edge.a), b = indices.get(edge.b);
      if (a === undefined || b === undefined) continue;
      layers[edge.kind].push(`M${points[a][0].toFixed(3)},${points[a][1].toFixed(3)}L${points[b][0].toFixed(3)},${points[b][1].toFixed(3)}`);
    }
    const group = svgNode("g", { class: "xc-pet-body" });
    if (mini && form === "egg") group.appendChild(svgNode("ellipse", { cx: 50, cy: 49, rx: 41, ry: 46, class: "xc-pet-shell" }));
    for (const [kind, path] of Object.entries(layers)) {
      if (path.length) group.appendChild(svgNode("path", { d: path.join(""), class: "xc-pet-lines xc-pet-" + kind }));
    }
    for (let index = 0; index < points.length; index++) {
      const [x, y] = points[index], radius = mini ? 3 : selected[index].kind === "corner" ? (form.startsWith("d") ? .4 : .65) : selected[index].kind === "shell" ? .65 : 1.25;
      group.appendChild(svgNode("rect", { x: x - radius, y: y - radius, width: radius * 2, height: radius * 2,
        class: "xc-pet-node xc-pet-node-" + selected[index].kind + (index < learned ? " xc-pet-learned" : "") }));
    }
    if (points.length && state !== "idle") {
      const [x, y] = points[0];
      group.appendChild(svgNode("circle", { cx: x, cy: y, r: mini ? 4 : 2.5, class: "xc-pet-signal" }));
    }
    svg.setAttribute("data-form", form); svg.setAttribute("data-state", state);
    svg.setAttribute("data-active", String(ACTIVE.has(state)));
    svg.setAttribute("data-points", String(points.length));
    svg.replaceChildren(group);
    const key = form + ":" + state;
    const visible = !document.hidden && host.getClientRects().length > 0 && !host.closest("[hidden]");
    if (previous && key !== previous && state !== "idle" && input.active !== false && !reduced && !systemReduce && visible && group.animate) {
      // One finite state transition. No ambient frame loop, random traffic or delayed restart.
      animation = group.animate([{ opacity: .65, transform: "scale(.97)" }, { opacity: 1, transform: "scale(1)" }],
        { duration: state === "folding" ? 420 : 240, iterations: 1, easing: "ease-out" });
    }
    previous = key;
  }
  function destroy() { if (destroyed) return; destroyed = true; pause(); svg.remove(); }
  return { render, pause, destroy };
}
