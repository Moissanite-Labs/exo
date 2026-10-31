/* The Exo egg: the living mark (D-049, docs/BRAND.md).
 *
 * The orb is built from one drawing (credit: aska-digital/incentre-lattice): a square cut by its X, and in each
 * triangle the incentre, the point equally far from all three sides. Lifted to a cube of side 2, the six
 * incentres sit on the axes at K = 2 - sqrt(2) from the centre, and stacked cubes give the truncated cubic
 * honeycomb, where every edge has the same length 2(sqrt(2) - 1). The same rule works in any dimension n:
 * 2n dots per cell, each touching 2n - 1 others. The egg draws that lattice as cells of information.
 *
 *   stage 0  egg        one cell (the yolk: six dots, an octahedron) inside a closed shell
 *   stage 1  hatchling  the six face cells join; the shell cracks and falls away
 *   stage 2  lattice    nineteen cells, the honeycomb as an orb; the first cube is drawn
 *   stage 3  hyper      the fourth dimension unfolds out of the third: 33 cells, 8 dots each
 *   stage 4  (spare)    a fifth dimension, for evolution lines that need one more step
 *
 * Fractional stages blend. No dependencies; Canvas 2D; honours prefers-reduced-motion (one still frame).
 *
 *   var egg = ExoEgg.mount(canvas, { stage: 0, activity: 0.5 });
 *   egg.setStage(2);  egg.setActivity(0.8);  egg.pulse(6);  egg.destroy();
 *
 * The activity layer (repository events drive the egg; interface in docs/LIVE-EGG.md section 1.2):
 *
 *   egg.event("issue" | "pr" | "merge" | "release", { replay: false, weight });   // unknown types play as "issue"
 *   egg.signal(level);   // the same, by level 1..4
 *   egg.queued();        // events waiting to play
 *   ExoEgg.EVENTS        // { issue: {level: 1}, pr: {level: 2}, merge: {level: 3}, release: {level: 4} }
 *
 *   issue    level 1  the core beats once, brighter, and a small quick pulse lights one lattice node
 *   pr       level 2  a stronger pulse, and a bright packet that travels several edges
 *   merge    level 3  a growth flash through the whole lattice, a brief rise in brightness and activity,
 *                     and the core heartbeat quickening for a few seconds
 *   release  level 4  a merge, a lift of the stage, and a second, fainter flash after it
 *
 * Events play from a queue (gaps 0.4 s after a level 1, 1.2 s after a 2, 2.5 s after a 3 or 4; at most 20 wait,
 * then the rest fold into one at the highest level). A bloom (3 or 4) happens at most once every 3 seconds; extra
 * merges play as level 2. Replayed events play at 0.6 strength and never bloom twice. weight is only for a HUD.
 * With prefers-reduced-motion there is no motion: one node is lit briefly, then it fades back.
 */
(function (root) {
  "use strict";

  var K = 2 - Math.SQRT2;                 // incentre distance from the cell centre (cube side 2)
  var MAX_STAGE = 4;
  var ACC = "255,91,20", ACC2 = "255,138,76", FG = "236,234,228", COLD = "159,180,196";

  var STAGES = [
    { stage: 0, name: "egg", dimension: 3, cells: 1, dots: 6, shell: 1, brightness: 0.62 },
    { stage: 1, name: "hatchling", dimension: 3, cells: 7, dots: 42, shell: 0.15, brightness: 0.74 },
    { stage: 2, name: "lattice", dimension: 3, cells: 19, dots: 114, shell: 0, brightness: 0.86 },
    { stage: 3, name: "hyper", dimension: 4, cells: 33, dots: 264, shell: 0, brightness: 1 },
    { stage: 4, name: "spare", dimension: 5, cells: 51, dots: 510, shell: 0, brightness: 1 }
  ];

  function clamp(x, a, b) { return x < a ? a : x > b ? b : x; }
  function smooth(x) { x = clamp(x, 0, 1); return x * x * (3 - 2 * x); }
  function vis(s, t) { return t < 0 ? 1 : smooth(s - t); }       // an element that appears over [t, t + 1]
  function rng(seed) {                                               // mulberry32
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6d2b79f5) >>> 0; var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  /* ---------- the lattice in D dimensions ---------- */
  function build(D) {
    var cells = [], dots = [], edges = [], idx = {};
    // cells: integer vectors c with |c|^2 <= 2 (the centre, its face neighbours, its edge neighbours)
    var c = [];
    (function rec(a) {
      if (a === D) {
        var q = 0, hi = -1;
        for (var i = 0; i < D; i++) { q += c[i] * c[i]; if (c[i] && i >= 3) hi = i; }
        if (q > 2) return;
        var t = hi >= 0 ? hi - 1 : (q === 0 ? -1 : q - 1);           // when the cell appears
        cells.push({ c: c.slice(), t: t, q: q, heat: 0, dots: [] });
        return;
      }
      for (var v = -1; v <= 1; v++) { c[a] = v; rec(a + 1); }
    })(0);
    cells.sort(function (x, y) { return x.t - y.t || x.q - y.q; });   // the centre cell (the yolk) is cells[0]
    cells.forEach(function (cell, ci) { idx[cell.c.join(",")] = ci; });
    cells.forEach(function (cell, ci) {
      for (var a = 0; a < D; a++) for (var sg = -1; sg <= 1; sg += 2) {
        var p = new Float32Array(D);
        for (var i = 0; i < D; i++) p[i] = 2 * cell.c[i];
        p[a] += K * sg;
        var t = Math.max(cell.t, a >= 3 ? a - 1 : -1);
        var hi = 0; for (var j = 3; j < D; j++) if (p[j]) hi = 1;
        cell.dots.push(dots.length);
        dots.push({ p: p, cell: ci, axis: a, sg: sg, t: t, hi: hi, heat: 0, nb: [] });
      }
    });
    function link(i, j, kind) {
      var e = { a: i, b: j, t: Math.max(dots[i].t, dots[j].t), kind: kind, hi: dots[i].hi || dots[j].hi };
      dots[i].nb.push(edges.length); dots[j].nb.push(edges.length); edges.push(e);
    }
    cells.forEach(function (cell) {                                    // inside a cell: every pair not on one axis
      var ds = cell.dots;
      for (var i = 0; i < ds.length; i++) for (var j = i + 1; j < ds.length; j++)
        if (dots[ds[i]].axis !== dots[ds[j]].axis) link(ds[i], ds[j], 0);
    });
    cells.forEach(function (cell) {                                    // through a face: the dot meets its mirror image
      cell.dots.forEach(function (di) {
        var d = dots[di]; if (d.sg < 0) return;
        var n = cell.c.slice(); n[d.axis] += 1;
        var cj = idx[n.join(",")]; if (cj === undefined) return;
        cells[cj].dots.forEach(function (dj) { if (dots[dj].axis === d.axis && dots[dj].sg < 0) link(di, dj, 1); });
      });
    });
    return { D: D, cells: cells, dots: dots, edges: edges };
  }

  /* The first cube, drawn from stage 1 to 2: its 12 edges and the lines from each dot to its face's corners
     (the drawing's lines, lifted). Stays in 3D. */
  function buildFrame() {
    var corners = [], lines = [];
    for (var i = 0; i < 8; i++) corners.push([(i & 1) ? 1 : -1, (i & 2) ? 1 : -1, (i & 4) ? 1 : -1]);
    for (var a = 0; a < 8; a++) for (var b = a + 1; b < 8; b++) {
      var d = 0; for (var k = 0; k < 3; k++) if (corners[a][k] !== corners[b][k]) d++;
      if (d === 1) lines.push([corners[a], corners[b], 0]);
    }
    for (var ax = 0; ax < 3; ax++) for (var sg = -1; sg <= 1; sg += 2) {
      var dot = [0, 0, 0]; dot[ax] = K * sg;
      corners.forEach(function (cn) { if (cn[ax] === sg) lines.push([dot, cn, 1]); });
    }
    return lines;
  }

  /* The shell: the same honeycomb, one ring further out, pushed onto an egg-shaped surface. */
  function buildShell() {
    var pts = [], edges = [], key = {};
    function egg(p) {
      var r = Math.hypot(p[0], p[1], p[2]) || 1, x = p[0] / r, y = p[1] / r, z = p[2] / r;
      var w = 0.94 + 0.1 * y;                                          // wider at the bottom (y down on screen)
      return [x * w, y * 1.2, z * w];
    }
    for (var i = -3; i <= 3; i++) for (var j = -3; j <= 3; j++) for (var k = -3; k <= 3; k++) {
      for (var a = 0; a < 3; a++) for (var sg = -1; sg <= 1; sg += 2) {
        var p = [2 * i, 2 * j, 2 * k]; p[a] += K * sg;
        var r = Math.hypot(p[0], p[1], p[2]);
        if (r < 4.4 || r > 5.6) continue;
        key[p.map(function (v) { return v.toFixed(3); }).join(",")] = pts.length;
        pts.push({ p: p, q: egg(p) });
      }
    }
    var L = 2 * (Math.SQRT2 - 1) + 1e-3;
    for (var m = 0; m < pts.length; m++) for (var n = m + 1; n < pts.length; n++) {
      var A = pts[m].p, B = pts[n].p;
      if (Math.hypot(A[0] - B[0], A[1] - B[1], A[2] - B[2]) < L) edges.push([m, n]);
    }
    return { pts: pts, edges: edges };
  }

  var EVENTS = { issue: { level: 1 }, pr: { level: 2 }, merge: { level: 3 }, release: { level: 4 } };
  var GAP = [0, 0.4, 1.2, 2.5, 2.5];
  var cache = {};
  function lattice(D) { return cache[D] || (cache[D] = build(D)); }
  var FRAME = null, SHELL = null;

  /* ---------- one egg on one canvas ---------- */
  function Egg(canvas, opt) {
    opt = opt || {};
    this.cv = canvas; this.ctx = canvas.getContext("2d");
    this.stage = this.target = clamp(+(opt.stage || 0), 0, MAX_STAGE);
    this.activity = clamp(opt.activity == null ? 0.5 : +opt.activity, 0, 1);
    this.layout = opt.layout || null;
    this.shellOn = opt.shell !== false;
    this.reduce = opt.reducedMotion != null ? !!opt.reducedMotion
      : !!(root.matchMedia && root.matchMedia("(prefers-reduced-motion: reduce)").matches);
    this.rand = rng(opt.seed == null ? 7 : opt.seed);
    this.spin = opt.spin == null ? 1 : +opt.spin;
    this.packets = []; this.t = opt.time || 0; this.last = 0; this.beatPrev = 0; this.raf = 0; this.running = false;
    this.phase = this.t; this.boost = 0; this.bloom = 0; this.quick = 0; this.kick = 0; this.flares = []; this.wave = null;
    this.q = []; this.gap = 0; this.lastBloom = -99; this.replayBloom = false; this.later = []; this.lift = null;
    FRAME = FRAME || buildFrame(); SHELL = SHELL || buildShell();
    this.shellSeed = SHELL.edges.map(this.rand);
    this.useDim(this.stage);
    this.resize();
    var self = this;
    this._resize = function () { self.resize(); if (!self.running) self.draw(0); };
    root.addEventListener("resize", this._resize, { passive: true });
    if (!this.reduce) {
      if ("IntersectionObserver" in root) {
        this.io = new IntersectionObserver(function (en) { en[0].isIntersecting ? self.resume() : self.pause(); });
        this.io.observe(canvas);
      }
      this._vis = function () { document.hidden ? self.pause() : self.resume(); };
      document.addEventListener("visibilitychange", this._vis);
      this.resume();
    } else {
      this.still();
    }
  }

  Egg.prototype.useDim = function (s) {
    var D = s > 3 ? 5 : s > 2 ? 4 : 3;
    if (this.L && this.L.D >= D) return;
    this.L = lattice(D);
    this.L.dots.forEach(function (d) { d.heat = 0; }); this.L.cells.forEach(function (c) { c.heat = 0; });
    this.packets = [];
    this.proj = new Float32Array(this.L.dots.length * 4);              // x, y, depth, higher-dimension weight
  };

  Egg.prototype.resize = function () {
    var r = this.cv.getBoundingClientRect(), dpr = Math.min(root.devicePixelRatio || 1, 2);
    this.W = r.width || this.cv.width; this.H = r.height || this.cv.height;
    this.cv.width = Math.round(this.W * dpr); this.cv.height = Math.round(this.H * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    var lay = this.layout ? this.layout(this.W, this.H) : null;
    this.cx = lay ? lay.cx : this.W / 2; this.cy = lay ? lay.cy : this.H / 2;
    this.R = lay ? lay.r : Math.min(this.W, this.H) * 0.4;
  };

  /* ---------- public API ---------- */
  Egg.prototype.setStage = function (s, o) {
    this.lift = null; this.target = clamp(+s || 0, 0, MAX_STAGE);
    this.useDim(Math.max(this.target, this.stage));
    if ((o && o.immediate) || this.reduce) { this.stage = this.target; if (!this.running) this.reduce ? this.still() : this.draw(0); }
    return this;
  };
  Egg.prototype.getStage = function () { return this.stage; };
  Egg.prototype.setActivity = function (a) { this.activity = clamp(+a || 0, 0, 1); if (this.reduce) this.still(); return this; };
  Egg.prototype.pulse = function (n) {                                 // n packets leave the core along live edges
    if (this.reduce) return this;
    var core = this.L.cells[0].dots;
    for (var i = 0; i < (n || 4); i++) this.spawn(core[(this.rand() * core.length) | 0], 10 + ((this.rand() * 8) | 0));
    return this;
  };
  /* Repository activity (docs/LIVE-EGG.md 1.2). Events wait in a queue and play in order. */
  Egg.prototype.event = function (type, o) {
    var ev = Object.prototype.hasOwnProperty.call(EVENTS, type) ? EVENTS[type] : EVENTS.issue;
    return this.enqueue(ev.level, !!(o && o.replay));
  };
  Egg.prototype.signal = function (level) { return this.enqueue(clamp(Math.round(+level || 1), 1, 4), false); };
  Egg.prototype.queued = function () { return this.q.length; };
  Egg.prototype.enqueue = function (lv, replay) {
    if (this.reduce) { this.highlight(); return this; }
    this.q.push({ lv: lv, replay: replay });
    if (this.q.length > 20) {                                          // a full queue folds into one event at the highest level
      var m = 1, rp = true; this.q.forEach(function (e) { m = Math.max(m, e.lv); rp = rp && e.replay; });
      this.q = [{ lv: m, replay: rp }];
    }
    return this;
  };
  Egg.prototype.play = function (item) {
    var lv = item.lv, k = item.replay ? 0.6 : 1, d = this.randDot();
    if (!item.replay) this.replayBloom = false;
    if (lv >= 3) {
      if (this.t - this.lastBloom < 3 || (item.replay && this.replayBloom)) lv = 2;
      else { this.lastBloom = this.t; if (item.replay) this.replayBloom = true; }
    }
    this.gap = GAP[lv] * (item.replay ? 0.6 : 1);
    this.kick = Math.max(this.kick, k);                                  // the core beats once, brighter
    this.lightDot(d, k, lv === 1 ? 0.35 : 0.55);
    this.pulse(lv === 1 ? 2 : lv === 2 ? 4 : lv === 3 ? 12 : 24);
    if (lv === 1) this.flares.push({ d: d, age: 0, dur: 0.7, size: 0.55 * k });
    if (lv === 2) {
      this.flares.push({ d: d, age: 0, dur: 1.1, size: k });
      var e = this.pick(d, -1);
      if (e >= 0) { var E = this.L.edges[e]; this.packets.push({ e: e, from: d, to: E.a === d ? E.b : E.a, p: 0, v: 3.2, life: 6 + ((this.rand() * 3) | 0), big: true }); }
    }
    if (lv >= 3) this.bloomFrom(d, k, lv >= 4 ? 1.25 : 1);
    if (lv >= 4) {
      var self = this;
      if (!this.lift) this.lift = { base: this.target };
      this.target = Math.min(MAX_STAGE, this.lift.base + 0.15);
      this.later.push({ at: 1.0, fn: function () { if (self.lift) { self.target = self.lift.base; self.lift = null; } } });
      this.later.push({ at: 0.9, fn: function () { self.bloomFrom(self.randDot(), 0.7, 1); } });
    }
  };
  Egg.prototype.randDot = function () {
    var L = this.L;
    for (var i = 0; i < 24; i++) { var d = (this.rand() * L.dots.length) | 0; if (vis(this.stage, L.dots[d].t) > 0.6) return d; }
    return L.cells[0].dots[0];
  };
  Egg.prototype.lightDot = function (d, h, ch) {
    var L = this.L; L.dots[d].heat = Math.max(L.dots[d].heat, h);
    var c = L.cells[L.dots[d].cell]; c.heat = Math.min(1, c.heat + ch);
  };
  Egg.prototype.bloomFrom = function (d, wf, size) {
    var L = this.L, o = L.dots[d].p, dist = new Float32Array(L.dots.length), max = 0;
    for (var i = 0; i < dist.length; i++) {
      var p = L.dots[i].p, s = 0; for (var k = 0; k < p.length; k++) s += (p[k] - o[k]) * (p[k] - o[k]);
      dist[i] = Math.sqrt(s); if (dist[i] > max) max = dist[i];
    }
    this.wave = { dist: dist, done: new Uint8Array(dist.length), r: 0, speed: max / 1.5, max: max, h: 0.7 * wf };
    this.flares.push({ d: d, age: 0, dur: 1.6, size: 1.7 * size * wf });
    this.bloom = Math.min(1.1, this.bloom + 0.8 * wf * size);
    this.boost = Math.min(1, this.boost + 0.8 * wf);
    this.quick = 4.5;
  };
  /* Reduced motion: light one node for a moment, then put the still frame back. No movement. */
  Egg.prototype.highlight = function () {
    var self = this, L = this.L, d = this.randDot();
    clearTimeout(this._hl);
    L.dots[d].heat = 1; L.cells[L.dots[d].cell].heat = 0.6; this.draw(0, true);
    this._hl = setTimeout(function () { self.still(); }, 900);
  };
  Egg.prototype.pause = function () { if (this.running) { this.running = false; cancelAnimationFrame(this.raf); } return this; };
  Egg.prototype.resume = function () {
    if (this.running || this.reduce) return this;
    this.running = true; this.last = performance.now();
    var self = this;
    this.raf = requestAnimationFrame(function loop(now) {
      if (!self.running) return;
      var dt = Math.min(0.05, (now - self.last) / 1000); self.last = now;
      self.draw(dt);
      self.raf = requestAnimationFrame(loop);
    });
    return this;
  };
  Egg.prototype.destroy = function () {
    this.pause(); root.removeEventListener("resize", this._resize);
    if (this.io) this.io.disconnect();
    if (this._vis) document.removeEventListener("visibilitychange", this._vis);
  };
  /* Reduced motion: one still frame, with a few cells lit along a path so the picture still says "information". */
  Egg.prototype.still = function () {
    this.t = 9.5; this.phase = 9.5; this.packets = [];
    var L = this.L, s = this.stage, r = rng(3);
    L.dots.forEach(function (d) { d.heat = 0; }); L.cells.forEach(function (c) { c.heat = 0; });
    var d = L.cells[0].dots[0];
    for (var hop = 0; hop < 7; hop++) {
      L.dots[d].heat = 1 - hop * 0.1; L.cells[L.dots[d].cell].heat = 0.8 - hop * 0.08;
      var nb = L.dots[d].nb.filter(function (e) { return vis(s, L.edges[e].t) > 0.5; });
      if (!nb.length) break;
      var e = L.edges[nb[(r() * nb.length) | 0]]; d = e.a === d ? e.b : e.a;
    }
    this.draw(0, true);
  };

  /* ---------- simulation ---------- */
  Egg.prototype.spawn = function (from, life) {
    var e = this.pick(from, -1); if (e < 0) return;
    var E = this.L.edges[e];
    this.packets.push({ e: e, from: from, to: E.a === from ? E.b : E.a, p: 0, v: 1.6 + this.rand() * 1.4, life: life || 8 });
  };
  Egg.prototype.pick = function (d, notEdge) {
    var nb = this.L.dots[d].nb, s = this.stage, ok = [];
    for (var i = 0; i < nb.length; i++) if (nb[i] !== notEdge && vis(s, this.L.edges[nb[i]].t) > 0.35) ok.push(nb[i]);
    return ok.length ? ok[(this.rand() * ok.length) | 0] : -1;
  };

  Egg.prototype.step = function (dt) {
    var L = this.L, a = clamp(this.activity + 0.35 * this.boost, 0, 1);
    this.boost *= Math.exp(-dt * 0.45); this.bloom *= Math.exp(-dt * 1.1);
    if (this.quick > 0) this.quick = Math.max(0, this.quick - dt);
    this.kick *= Math.exp(-dt * 4);
    if ((this.gap -= dt) <= 0 && this.q.length) this.play(this.q.shift());
    if (this.later.length) for (var li = this.later.length - 1; li >= 0; li--) { if ((this.later[li].at -= dt) <= 0) this.later.splice(li, 1)[0].fn(); }
    var W = this.wave;
    if (W) {
      W.r += dt * W.speed;
      var band = 0.5 * W.max;                                        // the flash has a body, not just a front
      for (var wi = 0; wi < W.dist.length; wi++) if (W.dist[wi] <= W.r && vis(this.stage, L.dots[wi].t) > 0.3) {
        var behind = W.r - W.dist[wi];
        if (!W.done[wi]) { W.done[wi] = 1; this.lightDot(wi, W.h, 0.1); }
        else if (behind < band) this.lightDot(wi, W.h * (1 - behind / band), 0.01);
      }
      if (W.r > W.max) this.wave = null;
    }
    for (var fi = this.flares.length - 1; fi >= 0; fi--) { this.flares[fi].age += dt; if (this.flares[fi].age >= this.flares[fi].dur) this.flares.splice(fi, 1); }
    // ease the stage toward its target
    var ds = this.target - this.stage;
    if (Math.abs(ds) > 1e-4) this.stage += clamp(ds, -dt * 0.7, dt * 0.7); else this.stage = this.target;
    // heartbeat: a beat sends packets out of the core; the rate rises with activity
    var f = (0.5 + a * 0.9) * (1 + 0.9 * smooth(this.quick)), ph;
    this.phase += dt * f; ph = this.phase % 1;
    var beat = ph < this.beatPrev;
    this.beatPrev = ph;
    if (beat) this.pulse(1 + Math.round(a * 3 + this.stage));
    // background traffic anywhere in the lattice
    var cap = 3 + a * 26 * (0.25 + 0.75 * smooth(this.stage / 2)) + this.stage * 10;
    if (this.packets.length < cap && this.rand() < dt * (2 + a * 18 + this.stage * 4)) {
      var d = (this.rand() * L.dots.length) | 0;
      if (vis(this.stage, L.dots[d].t) > 0.6) this.spawn(d, 4 + ((this.rand() * 6) | 0));
    }
    for (var i = this.packets.length - 1; i >= 0; i--) {
      var P = this.packets[i]; P.p += dt * P.v;
      if (P.p < 1) continue;
      var D = L.dots[P.to]; D.heat = 1; L.cells[D.cell].heat = Math.min(1, L.cells[D.cell].heat + 0.55);
      if (--P.life <= 0) { this.packets.splice(i, 1); continue; }
      var n = this.pick(P.to, P.e);
      if (n < 0) { this.packets.splice(i, 1); continue; }
      var E = L.edges[n]; P.e = n; P.from = P.to; P.to = E.a === P.to ? E.b : E.a; P.p -= 1;
    }
    var k = Math.exp(-dt * 2.2), kc = Math.exp(-dt * 1.4);
    for (var j = 0; j < L.dots.length; j++) L.dots[j].heat *= k;
    for (var c = 0; c < L.cells.length; c++) L.cells[c].heat *= kc;
  };

  /* ---------- drawing ---------- */
  Egg.prototype.draw = function (dt, frozen) {
    if (dt && !frozen) { this.t += dt; this.step(dt); }
    var ctx = this.ctx, L = this.L, s = this.stage, t = this.t, D = L.D;
    var W = this.W, H = this.H, cx = this.cx, cy = this.cy, R = this.R;
    ctx.clearRect(0, 0, W, H);
    var bright = s >= 3 ? 1 : STAGES[Math.floor(s)].brightness + (STAGES[Math.min(3, Math.floor(s) + 1)].brightness - STAGES[Math.floor(s)].brightness) * (s - Math.floor(s));
    var beat = Math.pow(Math.max(0, Math.cos(Math.PI * 2 * (this.phase % 1))), 12) * (1 + 0.45 * smooth(this.quick));
    beat = Math.max(beat, this.kick);
    bright *= 1 + 0.22 * Math.min(1, this.bloom);
    var breathe = 1 + 0.012 * Math.sin(t * 0.9) + 0.01 * beat;

    // rotation: a slow turn in 3D, and turns through each higher axis
    var ry = 0.7 + t * 0.11 * this.spin, rx = -0.42, cyR = Math.cos(ry), syR = Math.sin(ry), cxR = Math.cos(rx), sxR = Math.sin(rx);
    var a4 = t * 0.23 * this.spin, a5 = t * 0.17 * this.spin;
    var u4 = vis(s, 2), u5 = vis(s, 3);                                // how far each extra axis has unfolded
    var extent = 1.25 + 2.35 * smooth(s / 2);                          // the lattice grows to fill the orb
    var scale = R * 0.92 / extent * breathe, F = 3.2;
    var pr = this.proj, n = L.dots.length;
    for (var i = 0; i < n; i++) {
      var p = L.dots[i].p, x = p[0], y = p[1], z = p[2], w4 = 0, w5 = 0;
      if (D > 3) { w4 = p[3] * u4; var c4 = Math.cos(a4), s4 = Math.sin(a4); var nx = x * c4 - w4 * s4; w4 = x * s4 + w4 * c4; x = nx;
        var nz = z * Math.cos(a4 * 0.7) - w4 * Math.sin(a4 * 0.7); w4 = z * Math.sin(a4 * 0.7) + w4 * Math.cos(a4 * 0.7); z = nz; }
      if (D > 4) { w5 = p[4] * u5; var c5 = Math.cos(a5), s5 = Math.sin(a5); var ny = y * c5 - w5 * s5; w5 = y * s5 + w5 * c5; y = ny; }
      var X = x * cyR + z * syR, Z = -x * syR + z * cyR;
      var Y = y * cxR - Z * sxR, Z2 = y * sxR + Z * cxR;
      var zz = Z2 / extent, ps = F / (F - zz);
      pr[i * 4] = cx + X * scale * ps; pr[i * 4 + 1] = cy + Y * scale * ps; pr[i * 4 + 2] = clamp(zz, -1, 1);
      pr[i * 4 + 3] = L.dots[i].hi;
    }
    var proj3 = function (q) {                                         // for 3D-only extras (frame, shell)
      var X = q[0] * cyR + q[2] * syR, Z = -q[0] * syR + q[2] * cyR, Y = q[1] * cxR - Z * sxR, Z2 = q[1] * sxR + Z * cxR;
      return [X, Y, Z2];
    };

    ctx.lineCap = "round";
    // 1. the shell
    var sh = this.shellOn ? 1 - smooth((s - 0.25) / 1.15) : 0;
    if (sh > 0.01) {
      var crack = 1 - sh, rs = R * (0.8 + crack * 0.35) * breathe, sp = SHELL.pts, sq = [];
      for (var m = 0; m < sp.length; m++) { var v = proj3(sp[m].q), qs = F / (F - v[2] * 0.6); sq.push([cx + v[0] * rs * qs, cy + v[1] * rs * qs, v[2]]); }
      var buckets = [[], [], []];
      for (var e = 0; e < SHELL.edges.length; e++) {
        if (this.shellSeed[e] < crack * 1.1) continue;                 // cracks: edges drop out as the egg hatches
        var A = sq[SHELL.edges[e][0]], B = sq[SHELL.edges[e][1]], dz = (A[2] + B[2]) / 2;
        buckets[dz < -0.33 ? 0 : dz < 0.33 ? 1 : 2].push(A, B);
      }
      for (var b = 0; b < 3; b++) {
        ctx.strokeStyle = "rgba(" + FG + "," + ((0.05 + b * 0.07) * sh * bright).toFixed(3) + ")"; ctx.lineWidth = 1;
        ctx.beginPath(); var bl = buckets[b];
        for (var q = 0; q < bl.length; q += 2) { ctx.moveTo(bl[q][0], bl[q][1]); ctx.lineTo(bl[q + 1][0], bl[q + 1][1]); }
        ctx.stroke();
      }
      for (var m2 = 0; m2 < sq.length; m2++) {
        var dd = (sq[m2][2] + 1) / 2; ctx.fillStyle = "rgba(" + FG + "," + ((0.1 + dd * 0.4) * sh * bright).toFixed(3) + ")";
        var z2 = 1 + dd * 1.2; ctx.fillRect(sq[m2][0] - z2 / 2, sq[m2][1] - z2 / 2, z2, z2);
      }
    }

    // 2. the first cube and the drawing's lines inside it (stage 1 to 2, fading under higher dimensions)
    var fr = vis(s, 0.6) * (1 - 0.6 * u4);
    if (fr > 0.01) {
      ctx.lineWidth = 1;
      for (var kind = 0; kind < 2; kind++) {
        ctx.strokeStyle = kind ? "rgba(" + ACC2 + "," + (0.16 * fr * bright).toFixed(3) + ")" : "rgba(" + FG + "," + (0.2 * fr * bright).toFixed(3) + ")";
        ctx.beginPath();
        for (var l = 0; l < FRAME.length; l++) {
          if (FRAME[l][2] !== kind) continue;
          var P1 = proj3(FRAME[l][0]), P2 = proj3(FRAME[l][1]);
          var s1 = F / (F - P1[2] / extent), s2 = F / (F - P2[2] / extent);
          ctx.moveTo(cx + P1[0] * scale * s1, cy + P1[1] * scale * s1); ctx.lineTo(cx + P2[0] * scale * s2, cy + P2[1] * scale * s2);
        }
        ctx.stroke();
      }
    }

    // 3. lattice edges, batched by alpha level and colour (structure white, higher dimensions steel blue)
    var LV = 10, paths = [];
    for (var z3 = 0; z3 < LV * 2; z3++) paths.push([]);
    for (var e2 = 0; e2 < L.edges.length; e2++) {
      var E = L.edges[e2], ve = vis(s, E.t); if (ve < 0.02) continue;
      var ia = E.a * 4, ib = E.b * 4, dz2 = (pr[ia + 2] + pr[ib + 2]) / 2;
      var heat = Math.max(L.dots[E.a].heat, L.dots[E.b].heat) * 0.5;
      var al = ve * (0.1 + (dz2 + 1) * 0.16 + heat * 0.5) * (E.kind ? 0.8 : 1);
      var lvl = clamp(Math.round(al * LV * 1.6), 0, LV - 1) + (E.hi ? LV : 0);
      paths[lvl].push(ia, ib);
    }
    ctx.lineWidth = 1;
    for (var pth = 0; pth < paths.length; pth++) {
      var arr = paths[pth]; if (!arr.length) continue;
      var lv = (pth % LV + 0.5) / (LV * 1.6);
      ctx.strokeStyle = "rgba(" + (pth >= LV ? COLD : FG) + "," + (lv * bright).toFixed(3) + ")";
      ctx.beginPath();
      for (var g = 0; g < arr.length; g += 2) { ctx.moveTo(pr[arr[g]], pr[arr[g] + 1]); ctx.lineTo(pr[arr[g + 1]], pr[arr[g + 1] + 1]); }
      ctx.stroke();
    }

    // 4. spokes: from each dot to its cell centre (the drawing's line to the centre). They light when a cell is busy.
    ctx.globalCompositeOperation = "lighter";
    var cc = this._cc || (this._cc = []);
    for (var ci = 0; ci < L.cells.length; ci++) {
      var cell = L.cells[ci], vc = vis(s, cell.t), hv = ci === 0 ? Math.max(cell.heat, 0.35 + 0.4 * beat) : cell.heat;
      if (vc < 0.05 || hv < 0.04) continue;
      var sx = 0, sy = 0; for (var di = 0; di < cell.dots.length; di++) { sx += pr[cell.dots[di] * 4]; sy += pr[cell.dots[di] * 4 + 1]; }
      sx /= cell.dots.length; sy /= cell.dots.length;
      ctx.strokeStyle = "rgba(" + ACC + "," + (0.5 * hv * vc * bright).toFixed(3) + ")"; ctx.lineWidth = 1;
      ctx.beginPath();
      for (var dj = 0; dj < cell.dots.length; dj++) { var o = cell.dots[dj] * 4; ctx.moveTo(sx, sy); ctx.lineTo(pr[o], pr[o + 1]); }
      ctx.stroke();
      cc.push(sx, sy, hv * vc);
    }
    // the core's glow, and a soft glow on every busy cell
    var glow = this.glow || (this.glow = sprite());
    for (var gi = 0; gi < cc.length; gi += 3) {
      var gr = (gi === 0 ? 0.42 : 0.2) * R * (0.6 + 0.4 * cc[gi + 2]) * (gi === 0 ? (1 + 0.25 * beat) : 1);
      ctx.globalAlpha = clamp(cc[gi + 2] * (gi === 0 ? 0.55 + 0.35 * beat : 0.45) * bright, 0, 1);
      ctx.drawImage(glow, cc[gi] - gr, cc[gi + 1] - gr, gr * 2, gr * 2);
    }
    cc.length = 0; ctx.globalAlpha = 1;

    // 5. packets of information moving along the edges
    for (var pk = 0; pk < this.packets.length; pk++) {
      var Pk = this.packets[pk], fa = Pk.from * 4, ta = Pk.to * 4, tt = Pk.p;
      var px = pr[fa] + (pr[ta] - pr[fa]) * tt, py = pr[fa + 1] + (pr[ta + 1] - pr[fa + 1]) * tt;
      var tb = Math.max(0, tt - 0.45), qx = pr[fa] + (pr[ta] - pr[fa]) * tb, qy = pr[fa + 1] + (pr[ta + 1] - pr[fa + 1]) * tb;
      var bg = Pk.big;
      ctx.strokeStyle = bg ? "rgba(255,200,160,0.9)" : "rgba(" + ACC2 + ",0.7)"; ctx.lineWidth = bg ? 2.4 : 1.6;
      ctx.beginPath(); ctx.moveTo(bg ? pr[fa] + (pr[ta] - pr[fa]) * Math.max(0, tt - 0.8) : qx, bg ? pr[fa + 1] + (pr[ta + 1] - pr[fa + 1]) * Math.max(0, tt - 0.8) : qy); ctx.lineTo(px, py); ctx.stroke();
      var gs = bg ? 24 : 14;
      ctx.globalAlpha = bg ? 1 : 0.8; ctx.drawImage(glow, px - gs / 2, py - gs / 2, gs, gs); ctx.globalAlpha = 1;
    }
    // 5b. activity: flares at the nodes that were hit, and a soft bloom over the whole lattice
    if (this.bloom > 0.02) {
      var br = R * (0.95 + 0.1 * (1 - Math.min(1, this.bloom)));
      ctx.globalAlpha = clamp(0.13 * this.bloom, 0, 0.16); ctx.drawImage(glow, cx - br, cy - br, br * 2, br * 2); ctx.globalAlpha = 1;
    }
    for (var fl = 0; fl < this.flares.length; fl++) {
      var Fl = this.flares[fl], u = Fl.age / Fl.dur, eo = 1 - Math.pow(1 - u, 3), fo = Fl.d * 4;
      var fr2 = (8 + eo * 34 * Fl.size) * (R / 200 > 1 ? Math.min(1.6, R / 200) : 1), fa = (1 - u) * (1 - u);
      ctx.globalAlpha = clamp(0.8 * fa, 0, 1); ctx.drawImage(glow, pr[fo] - fr2, pr[fo + 1] - fr2, fr2 * 2, fr2 * 2);
      ctx.globalAlpha = 1; ctx.strokeStyle = "rgba(" + ACC2 + "," + (0.55 * fa).toFixed(3) + ")"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(pr[fo], pr[fo + 1], fr2 * 0.7, 0, 6.2832); ctx.stroke();
    }
    ctx.globalCompositeOperation = "source-over";

    // 6. the dots: squares, like the site; hot ones orange; the seed (top dot of the core cell) always orange
    for (var dn = 0; dn < n; dn++) {
      var Dd = L.dots[dn], vd = vis(s, Dd.t); if (vd < 0.03) continue;
      var o2 = dn * 4, dep = (pr[o2 + 2] + 1) / 2, h = Dd.heat;
      var seed = Dd.cell === 0 && Dd.axis === 1 && Dd.sg < 0;
      var sz = (1.3 + dep * 1.9 + h * 1.6) * (s < 1 ? 1.5 - 0.5 * s : 1) * (seed ? 1.5 : 1);
      if (seed || h > 0.25) ctx.fillStyle = "rgba(" + ACC + "," + (vd * (0.6 + 0.4 * Math.max(h, seed ? 1 : 0))).toFixed(3) + ")";
      else ctx.fillStyle = "rgba(" + (Dd.hi ? COLD : FG) + "," + (vd * (0.25 + dep * 0.65) * bright).toFixed(3) + ")";
      ctx.fillRect(pr[o2] - sz / 2, pr[o2 + 1] - sz / 2, sz, sz);
    }
  };

  function sprite() {
    var c = document.createElement("canvas"); c.width = c.height = 64;
    var g = c.getContext("2d"), gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    gr.addColorStop(0, "rgba(255,170,120,1)"); gr.addColorStop(0.25, "rgba(255,91,20,0.55)"); gr.addColorStop(1, "rgba(255,91,20,0)");
    g.fillStyle = gr; g.fillRect(0, 0, 64, 64); return c;
  }

  /* ---------- module ---------- */
  var ExoEgg = {
    version: "1",
    MAX_STAGE: MAX_STAGE,
    STAGES: STAGES,
    K: K,
    mount: function (canvas, opt) { return new Egg(canvas, opt); },
    EVENTS: EVENTS,
    autoMount: function (scope) {
      var out = [];
      (scope || document).querySelectorAll("canvas[data-exo-egg]").forEach(function (cv) {
        if (cv._exoEgg) return;
        cv._exoEgg = new Egg(cv, { stage: cv.dataset.stage, activity: cv.dataset.activity,
          seed: cv.dataset.seed == null ? undefined : +cv.dataset.seed, shell: cv.dataset.shell !== "off"});
        out.push(cv._exoEgg);
      });
      return out;
    }
  };
  root.ExoEgg = ExoEgg;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { ExoEgg.autoMount(); });
  else ExoEgg.autoMount();
})(window);
