import { api } from "../api.js";
import { download, h, icon, mount, toast } from "../dom.js";
import { badge, callout, card, emptyState, errorCallout, field, locationLink, needRepo, pageHead, resolutionBadge, symbolInput } from "../components.js";
import { forceLayout } from "../graphlayout.js";
import { withBusy } from "./shared.js";

const COLORS = { Function: "#4f46e5", Method: "#0ea5a4", Class: "#d97706", Module: "#7c3aed", Test: "#16a34a", Endpoint: "#dc2626",
  File: "#64748b", ConfigKey: "#c026d3", DatabaseEntity: "#0284c7", DeploymentArtifact: "#475569", Directory: "#94a3b8", Repository: "#0f172a" };
const EDGE_TYPES = ["CALLS", "IMPORTS", "INHERITS", "IMPLEMENTS", "TESTS", "EXPOSES", "READS_CONFIG", "WRITES_ENTITY", "DEPLOYS", "CONTAINS"];

export default async function graph(root) {
  const repo = needRepo(root);
  if (!repo) return;
  const { input, el: inputEl } = symbolInput(repo.id, "Function, class, or file path (e.g. add_stock or inventory/service.py)", "graph-symbols");
  const depth = h("select", { class: "select" }, [1, 2, 3].map((d) => h("option", { value: d, text: `Depth ${d}` })));
  const dir = h("select", { class: "select" }, [["both", "Both directions"], ["in", "Dependents (who uses it)"], ["out", "Dependencies (what it uses)"]].map(([v, t]) => h("option", { value: v, text: t })));
  const active = new Set(["CALLS", "IMPORTS", "INHERITS", "TESTS", "EXPOSES", "READS_CONFIG"]);
  const chips = h("div", { class: "row-wrap" });
  const canvas = h("div"), side = h("div");
  let current = null;

  function drawChips() {
    mount(chips, EDGE_TYPES.map((t) => h("button", { class: `chip ${active.has(t) ? "" : "off"}`, onclick: () => { active.has(t) ? active.delete(t) : active.add(t); drawChips(); } }, t)));
  }

  async function run(nodeId) {
    mount(canvas, h("div", { class: "card" }, h("span", { class: "spinner" }), " Loading neighbourhood…"));
    try {
      const view = await api.neighborhood(repo.id, { node: nodeId, depth: depth.value, direction: dir.value });
      current = view; drawGraph(view);
    } catch (e) { mount(canvas, errorCallout(e)); }
  }

  function drawGraph(view) {
    const edges = view.edges.filter((e) => active.has(e.type));
    const W = 900, H = 620;
    const pos = forceLayout(view.nodes, edges, { width: W, height: H, pinned: view.center });
    const svg = h("svg", { class: "graph", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Dependency graph" },
      h("defs", {}, h("marker", { id: "arrow", viewBox: "0 0 10 10", refX: "18", refY: "5", markerWidth: "6", markerHeight: "6", orient: "auto-start-reverse" }, h("path", { d: "M0 0L10 5L0 10z", fill: "#94a3b8" }))));
    const g = h("g");
    for (const e of edges) {
      const a = pos.get(e.source), b = pos.get(e.target);
      if (!a || !b) continue;
      g.append(h("line", { class: `gedge ${e.resolution}`, x1: a.x, y1: a.y, x2: b.x, y2: b.y, "marker-end": "url(#arrow)" }, h("title", { text: `${e.type} (${e.resolution}, ${e.method}) ${e.path ? e.path + ":" + e.line : ""}` })));
    }
    for (const n of view.nodes) {
      const p = pos.get(n.id);
      if (!p) continue;
      const r = n.id === view.center ? 11 : 7;
      g.append(h("g", { class: "gnode", transform: `translate(${p.x},${p.y})`, tabindex: "0", role: "button", "aria-label": `${n.type} ${n.name}`,
        onclick: () => select(n), onkeydown: (ev) => { if (ev.key === "Enter") select(n); } },
        h("circle", { r, fill: COLORS[n.type] || "#94a3b8" }, h("title", { text: `${n.type}: ${n.qualified_name || n.name}` })),
        h("text", { y: r + 13, "text-anchor": "middle", text: (n.name || n.id).slice(0, 26) })));
    }
    svg.append(g);
    // pan & zoom
    let vb = [0, 0, W, H], drag = null;
    const apply = () => svg.setAttribute("viewBox", vb.join(" "));
    svg.addEventListener("wheel", (ev) => { ev.preventDefault(); const f = ev.deltaY > 0 ? 1.12 : 0.89; const r = svg.getBoundingClientRect();
      const mx = vb[0] + ((ev.clientX - r.left) / r.width) * vb[2], my = vb[1] + ((ev.clientY - r.top) / r.height) * vb[3];
      vb = [mx - (mx - vb[0]) * f, my - (my - vb[1]) * f, vb[2] * f, vb[3] * f]; apply(); }, { passive: false });
    svg.addEventListener("pointerdown", (ev) => { if (ev.target.closest(".gnode")) return; drag = { x: ev.clientX, y: ev.clientY, vb: [...vb] }; svg.setPointerCapture(ev.pointerId); });
    svg.addEventListener("pointermove", (ev) => { if (!drag) return; const r = svg.getBoundingClientRect();
      vb = [drag.vb[0] - ((ev.clientX - drag.x) / r.width) * vb[2], drag.vb[1] - ((ev.clientY - drag.y) / r.height) * vb[3], vb[2], vb[3]]; apply(); });
    svg.addEventListener("pointerup", () => { drag = null; });
    const types = [...new Set(view.nodes.map((n) => n.type))];
    mount(canvas, h("div", { class: "stack" },
      view.truncated ? callout("warn", "The neighbourhood was truncated to keep the view readable. Lower the depth or filter edge types.") : null,
      h("div", { class: "graph-wrap" }, svg),
      h("div", { class: "legend" }, types.map((t) => h("span", {}, h("i", { style: { background: COLORS[t] || "#94a3b8" } }), t)),
        h("span", {}, h("svg", { width: 28, height: 8 }, h("line", { x1: 0, y1: 4, x2: 28, y2: 4, class: "gedge resolved" })), " resolved"),
        h("span", {}, h("svg", { width: 28, height: 8 }, h("line", { x1: 0, y1: 4, x2: 28, y2: 4, class: "gedge heuristic" })), " heuristic"),
        h("span", { class: "muted", text: `${view.nodes.length} nodes · ${edges.length} edges · scroll to zoom, drag to pan` }))));
    const center = view.nodes.find((n) => n.id === view.center);
    if (center) select(center);
  }

  function select(n) {
    const rel = (current?.edges || []).filter((e) => e.source === n.id || e.target === n.id);
    const name = (id) => current.nodes.find((x) => x.id === id)?.name || id;
    mount(side, card(n.name || n.id, h("div", { class: "stack" },
      h("div", { class: "row-wrap" }, badge(n.type, "primary"), n.confidence ? badge(`confidence: ${n.confidence}`) : null),
      n.qualified_name ? h("div", { class: "mono", text: n.qualified_name }) : null,
      n.path ? locationLink(repo.id, n.path, n.start_line, n.end_line) : null,
      h("button", { class: "btn btn-sm", onclick: () => run(n.id) }, icon("graph", 14), "Centre on this node"),
      rel.length ? h("div", {}, h("h3", { text: "Relationships" }), rel.slice(0, 25).map((e) => h("div", { class: "row small", style: { margin: "4px 0" } }, badge(e.type), h("span", { text: e.source === n.id ? `→ ${name(e.target)}` : `← ${name(e.source)}` }), resolutionBadge(e.resolution)))) : null)));
  }

  const go = h("button", { class: "btn btn-primary" }, icon("search", 16), "Explore");
  go.onclick = () => withBusy(go, async () => { if (!input.value.trim()) { toast("Enter a symbol or file path.", "error"); return; } await run(input.value.trim()); });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") go.click(); });
  const exp = h("button", { class: "btn" }, icon("download", 16), "Export graph JSON");
  exp.onclick = () => withBusy(exp, async () => download(`${repo.name}-graph.json`, JSON.stringify(await api.graphExport(repo.id)), "application/json"));
  drawChips();
  mount(root, pageHead("Knowledge graph", "Statically extracted relationships. Solid green edges are resolved by static analysis; dashed amber edges are heuristic.", exp),
    h("div", { class: "stack" }, card(null, h("div", { class: "stack" }, h("div", { class: "grid grid-3" }, field("Start from", inputEl), field("Depth", depth), field("Direction", dir)), field("Edge types", chips), h("div", {}, go))),
      h("div", { class: "split", style: { "grid-template-columns": "minmax(0,1fr) 320px" } }, canvas, side)));
  mount(canvas, emptyState("graph", "Explore dependencies", "Enter a function, class or file to see what it uses and what uses it."));
}
