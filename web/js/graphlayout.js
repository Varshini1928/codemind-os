// Deterministic force-directed layout (Fruchterman-Reingold style) for small neighbourhood graphs.
function rng(seed) { let a = seed >>> 0; return () => { a |= 0; a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }

export function forceLayout(nodes, edges, { width = 900, height = 600, iterations = 260, seed = 7, pinned = null } = {}) {
  const n = nodes.length;
  const pos = new Map();
  if (!n) return pos;
  const rand = rng(seed);
  const cx = width / 2, cy = height / 2;
  nodes.forEach((nd, i) => {
    const ang = (2 * Math.PI * i) / n, r = Math.min(width, height) * 0.35;
    pos.set(nd.id, { x: cx + r * Math.cos(ang) + (rand() - 0.5) * 20, y: cy + r * Math.sin(ang) + (rand() - 0.5) * 20 });
  });
  if (pinned && pos.has(pinned)) pos.set(pinned, { x: cx, y: cy });
  const k = Math.sqrt((width * height) / n) * 0.75;
  const ids = nodes.map((x) => x.id);
  const links = edges.filter((e) => pos.has(e.source) && pos.has(e.target) && e.source !== e.target);
  let temp = width / 8;
  for (let it = 0; it < iterations; it++) {
    const disp = new Map(ids.map((id) => [id, { x: 0, y: 0 }]));
    for (let a = 0; a < n; a++) for (let b = a + 1; b < n; b++) {
      const pa = pos.get(ids[a]), pb = pos.get(ids[b]);
      let dx = pa.x - pb.x, dy = pa.y - pb.y, d = Math.hypot(dx, dy) || 0.01;
      const f = (k * k) / d;
      dx /= d; dy /= d;
      disp.get(ids[a]).x += dx * f; disp.get(ids[a]).y += dy * f;
      disp.get(ids[b]).x -= dx * f; disp.get(ids[b]).y -= dy * f;
    }
    for (const e of links) {
      const ps = pos.get(e.source), pt = pos.get(e.target);
      let dx = ps.x - pt.x, dy = ps.y - pt.y, d = Math.hypot(dx, dy) || 0.01;
      const f = (d * d) / k;
      dx /= d; dy /= d;
      disp.get(e.source).x -= dx * f; disp.get(e.source).y -= dy * f;
      disp.get(e.target).x += dx * f; disp.get(e.target).y += dy * f;
    }
    for (const id of ids) {
      if (id === pinned) continue;
      const p = pos.get(id), d = disp.get(id);
      const len = Math.hypot(d.x, d.y) || 0.01;
      p.x += (d.x / len) * Math.min(len, temp) + (cx - p.x) * 0.01;
      p.y += (d.y / len) * Math.min(len, temp) + (cy - p.y) * 0.01;
      p.x = Math.max(40, Math.min(width - 40, p.x));
      p.y = Math.max(30, Math.min(height - 30, p.y));
    }
    temp *= 0.985;
  }
  return pos;
}
