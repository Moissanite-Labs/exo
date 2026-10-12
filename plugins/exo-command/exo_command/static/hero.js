// The site's hero, for an empty thread: a shell of linked nodes around an orange core, drawn on a canvas
// (ported from site/assets/main.js). Cheap by design: about 24 frames a second, device pixel ratio capped,
// stops when the tab is hidden or the canvas is off screen, and draws one still frame under reduced motion.
export function createHero(canvas) {
  const ctx = canvas.getContext && canvas.getContext("2d");
  if (!ctx) return { start() {}, stop() {}, still() {} };
  const mq = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };
  const N = 150, pts = [], edges = [], pulses = [], proj = new Array(N);
  const ga = Math.PI * (3 - Math.sqrt(5));
  for (let k = 0; k < N; k++) {
    const y = 1 - (k / (N - 1)) * 2, r = Math.sqrt(1 - y * y), th = ga * k;
    pts.push({ x: Math.cos(th) * r, y, z: Math.sin(th) * r, hot: Math.random() < 0.08 });
  }
  for (let a = 0; a < N; a++) for (let b = a + 1; b < N; b++) {
    const dx = pts[a].x - pts[b].x, dy = pts[a].y - pts[b].y, dz = pts[a].z - pts[b].z;
    if (dx * dx + dy * dy + dz * dz < 0.075) edges.push([a, b]);
  }
  let W = 0, H = 0, cx = 0, cy = 0, R = 0, raf = 0, running = false, last = 0, t0 = performance.now(), shown = true, lastT = 0;

  function size() {
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    W = rect.width; H = rect.height;
    canvas.width = Math.max(1, Math.round(W * dpr)); canvas.height = Math.max(1, Math.round(H * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    cx = W * 0.74; cy = H * 0.5; R = Math.min(W * 0.26, H * 0.4);
  }

  function draw(t, still) {
    const rotY = 0.6 + (still ? 0 : t * 0.08), rotX = -0.35;
    const cY = Math.cos(rotY), sY = Math.sin(rotY), cX = Math.cos(rotX), sX = Math.sin(rotX);
    ctx.clearRect(0, 0, W, H);
    const rr = R * (1 + (still ? 0 : Math.sin(t * 0.5) * 0.015));
    for (let i = 0; i < N; i++) {
      const p = pts[i];
      const x = p.x * cY + p.z * sY, z = -p.x * sY + p.z * cY;
      const y2 = p.y * cX - z * sX, z2 = p.y * sX + z * cX, s = 1.6 / (2.6 - z2);
      proj[i] = { x: cx + x * rr * s, y: cy + y2 * rr * s, z: z2 };
    }
    ctx.lineWidth = 1;
    for (const [a, b] of edges) {
      const A = proj[a], B = proj[b], al = 0.04 + ((A.z + B.z) / 2 + 1) * 0.09;
      ctx.strokeStyle = "rgba(236,234,228," + al.toFixed(3) + ")";
      ctx.beginPath(); ctx.moveTo(A.x, A.y); ctx.lineTo(B.x, B.y); ctx.stroke();
    }
    for (let j = 0; j < N; j++) {
      if (!pts[j].hot || proj[j].z < -0.1) continue;
      const P = proj[j], g = ctx.createLinearGradient(P.x, P.y, cx, cy);
      g.addColorStop(0, "rgba(255,91,20,0.35)"); g.addColorStop(1, "rgba(255,91,20,0)");
      ctx.strokeStyle = g; ctx.beginPath(); ctx.moveTo(P.x, P.y); ctx.lineTo(cx, cy); ctx.stroke();
    }
    for (let n = 0; n < N; n++) {
      const Q = proj[n], d = (Q.z + 1) / 2;
      if (pts[n].hot) { ctx.fillStyle = "rgba(255,91,20," + (0.5 + d * 0.5).toFixed(3) + ")"; ctx.fillRect(Q.x - 2.5, Q.y - 2.5, 5, 5); }
      else { ctx.fillStyle = "rgba(236,234,228," + (0.15 + d * 0.6).toFixed(3) + ")"; const sz = 1 + d * 1.6; ctx.fillRect(Q.x - sz / 2, Q.y - sz / 2, sz, sz); }
    }
    if (!still) {
      if (pulses.length < 5 && Math.random() < 0.06) {
        const hot = []; for (let q = 0; q < N; q++) if (pts[q].hot && proj[q].z > 0) hot.push(q);
        if (hot.length) pulses.push({ i: hot[(Math.random() * hot.length) | 0], s: 0 });
      }
      for (let u = pulses.length - 1; u >= 0; u--) {
        const pu = pulses[u]; pu.s += 0.018;
        if (pu.s >= 1) { pulses.splice(u, 1); continue; }
        const S = proj[pu.i];
        ctx.fillStyle = "rgba(255,138,76," + (1 - pu.s).toFixed(3) + ")";
        ctx.beginPath(); ctx.arc(S.x + (cx - S.x) * pu.s, S.y + (cy - S.y) * pu.s, 2.2, 0, Math.PI * 2); ctx.fill();
      }
    }
    const cr = R * 0.12, rot = still ? 0 : t * 0.25;
    ctx.save(); ctx.translate(cx, cy);
    for (let ring = 0; ring < 2; ring++) {
      const rad = cr * (ring ? 1.9 : 1), dir = ring ? -1 : 1;
      ctx.beginPath();
      for (let v = 0; v < 6; v++) {
        const ang = rot * dir + v * Math.PI / 3 + Math.PI / 6, vx = Math.cos(ang) * rad, vy = Math.sin(ang) * rad;
        v ? ctx.lineTo(vx, vy) : ctx.moveTo(vx, vy);
      }
      ctx.closePath();
      if (ring) { ctx.strokeStyle = "rgba(255,91,20,0.45)"; ctx.stroke(); }
      else { ctx.fillStyle = "rgba(255,91,20," + (0.75 + (still ? 0 : Math.sin(t * 1.4) * 0.2)).toFixed(3) + ")"; ctx.fill(); }
    }
    ctx.restore();
  }

  function frame(now) {
    if (!running) return;
    raf = requestAnimationFrame(frame);
    if (now - last < 40) return;   // about 24 fps
    last = now;
    draw((now - t0) / 1000, false);
  }
  function still() { size(); if (W > 0) draw(0, true); }
  function start() {
    if (mq.matches || document.hidden || !shown) { still(); return; }
    if (running) return;
    size(); running = true; t0 = performance.now() - lastT; raf = requestAnimationFrame(frame);
  }
  function stop() { if (running) { running = false; lastT = performance.now() - t0; cancelAnimationFrame(raf); } }
  document.addEventListener("visibilitychange", () => (document.hidden ? stop() : start()));
  if (mq.addEventListener) mq.addEventListener("change", () => { stop(); start(); });
  if ("ResizeObserver" in window) new ResizeObserver(() => { if (!running) still(); else size(); }).observe(canvas);
  if ("IntersectionObserver" in window) new IntersectionObserver((en) => { shown = en[0].isIntersecting; shown ? start() : stop(); }).observe(canvas);
  return { start, stop, still };
}
