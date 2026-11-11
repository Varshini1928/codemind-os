import { api } from "./api.js";
import { append, clear, download, fmtDate, h, icon, mount, shortRev } from "./dom.js";
import { parseInline } from "./md.js";
import { currentRepo } from "./state.js";

export const inlineMd = (text) => parseInline(String(text ?? "")).map((t) =>
  t.t === "code" ? h("code", { class: "inline-code", text: t.v }) : t.t === "b" ? h("strong", { text: t.v }) : t.t === "i" ? h("em", { text: t.v }) : t.v);

export function avatar(user, size = 36) {
  const base = (user.display_name || user.email || "?").trim();
  const initials = base.split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map((x) => x[0].toUpperCase()).join("") || "?";
  let hash = 0; for (const c of user.email || base) hash = (hash * 31 + c.charCodeAt(0)) % 360;
  return h("span", { class: "avatar", "aria-hidden": "true", style: { width: `${size}px`, height: `${size}px`, "font-size": `${Math.round(size * 0.4)}px`, background: `hsl(${hash} 55% 45%)` } }, initials);
}

export const badge = (text, kind = "") => h("span", { class: `badge ${kind ? "b-" + kind : ""}` }, text);
export const pageHead = (title, subtitle, ...actions) => h("div", { class: "page-head" },
  h("div", {}, h("h1", { text: title }), subtitle ? h("p", { text: subtitle }) : null), h("div", { class: "right row-wrap" }, ...actions));
export const card = (title, body, actions) => h("section", { class: "card" },
  title || actions ? h("div", { class: "card-head" }, title ? h("h2", { text: title }) : null, h("div", { class: "spacer" }), actions || null) : null, body);
export const callout = (kind, ...children) => h("div", { class: `callout callout-${kind}` }, icon(kind === "info" ? "info" : "alert", 18), h("div", {}, ...children));
export const field = (label, control, hint) => h("div", { class: "field" }, h("label", { text: label }), control, hint ? h("span", { class: "hint", text: hint }) : null);
export const loading = (n = 4) => h("div", {}, Array.from({ length: n }, (_, i) => h("div", { class: "skeleton", style: { width: `${95 - i * 12}%` } })));
export const statTile = (value, label) => h("div", { class: "card stat" }, h("div", { class: "v", text: value ?? "—" }), h("div", { class: "l", text: label }));
export const errorCallout = (e) => callout("danger", h("strong", { text: "Something went wrong. " }), e?.detail || e?.message || String(e));

export function emptyState(iconName, title, text, action) {
  return h("div", { class: "empty card" }, icon(iconName, 36), h("h3", { text: title }), h("p", { text }), action || null);
}

export const STATUS_KIND = { ready: "success", indexing: "info", registered: "", failed: "danger", cancelled: "warn",
  complete: "success", partial: "warn", insufficient_evidence: "warn", succeeded: "success", running: "info", queued: "" };
export const statusPill = (s) => h("span", { class: `badge ${STATUS_KIND[s] ? "b-" + STATUS_KIND[s] : ""}` }, h("i", { class: "dot" }), String(s).replace("_", " "));

export const RES_KIND = { resolved: ["success", "statically resolved"], "statically resolved": ["success", "statically resolved"],
  heuristic: ["warn", "heuristic"], external: ["", "external"], unresolved: ["", "unresolved"] };
export const resolutionBadge = (r) => { const [k, t] = RES_KIND[r] || ["", r]; return badge(t, k); };

export function barChart(obj) {
  const entries = Object.entries(obj || {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map((e) => e[1]));
  if (!entries.length) return h("p", { class: "muted", text: "No data." });
  return h("div", { class: "bars" }, entries.map(([k, v]) => h("div", { class: "bar-row" }, h("span", { text: k }),
    h("div", { class: "bar-track" }, h("div", { class: "bar-fill", style: { "--w": `${(v / max) * 100}%` } })), h("span", { class: "muted", text: v }))));
}

export function table(headers, rows, { onRow, empty = "Nothing to show." } = {}) {
  if (!rows.length) return h("p", { class: "muted", text: empty });
  return h("div", { class: "table-wrap" }, h("table", { class: "table" },
    h("thead", {}, h("tr", {}, headers.map((x) => h("th", { text: x })))),
    h("tbody", {}, rows.map((r, i) => h("tr", { class: onRow ? "clickable" : "", onclick: onRow ? () => onRow(i) : null },
      r.map((c) => h("td", { class: typeof c === "string" && c.length > 60 ? "wrap" : "" }, c)))))));
}

export function tabs(defs, initial = 0) {
  const body = h("div"), bar = h("div", { class: "tabs", role: "tablist" });
  const show = (i) => { [...bar.children].forEach((b, j) => b.classList.toggle("active", i === j)); mount(body, defs[i].render()); };
  defs.forEach((d, i) => bar.append(h("button", { class: "tab", role: "tab", onclick: () => show(i) }, d.label, d.count !== undefined ? ` (${d.count})` : "")));
  show(initial);
  return h("div", {}, bar, body);
}

export function codeView(lines, start = 1, hl = null) {
  return h("div", { class: "cv", role: "region", "aria-label": "Source code" }, lines.map((ln, i) => {
    const no = start + i;
    const on = hl && no >= hl[0] && no <= hl[1];
    return h("div", { class: `cv-line ${on ? "cv-hl" : ""}`, id: on && no === hl[0] ? "cv-first" : null }, h("span", { class: "cv-no", text: no }), h("span", { class: "cv-text", text: ln || " " }));
  }));
}

/* ---------------------------------------------------------------- drawers */
function openDrawer(title, bodyBuilder) {
  const close = () => { wrap.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const body = h("div", { class: "drawer-body" });
  const wrap = h("div", { class: "drawer-wrap", onclick: (e) => { if (e.target === wrap) close(); } },
    h("aside", { class: "drawer", role: "dialog", "aria-modal": "true", "aria-label": title },
      h("div", { class: "drawer-head" }, h("h3", { text: title, class: "spacer" }), h("button", { class: "icon-btn", onclick: close, "aria-label": "Close" }, icon("x"))), body));
  document.addEventListener("keydown", onKey);
  document.body.append(wrap);
  bodyBuilder(body);
  return body;
}

export async function openSource(repoId, path, start = 1, end = start, context = 12) {
  openDrawer(path, async (body) => {
    mount(body, loading(6));
    try {
      const s = Math.max(1, start - context), e = end + context;
      const r = await api.source(repoId, path, s, e);
      mount(body, h("p", { class: "muted small" }, `Revision ${shortRev(r.revision)} · lines ${r.start_line}–${r.end_line} of ${r.total_lines}`),
        codeView(r.lines, r.start_line, [start, end]));
      const first = body.querySelector("#cv-first");
      if (first) first.scrollIntoView({ block: "center" });
    } catch (err) { mount(body, errorCallout(err)); }
  });
}

export function openEvidence(evidence, selected, repoId) {
  openDrawer("Evidence", (body) => {
    const items = (evidence || []).slice();
    if (!items.length) { mount(body, h("p", { class: "muted", text: "No evidence attached." })); return; }
    const nodes = items.map((e) => {
      const codeBox = h("div", {});
      const el = h("div", { class: `ev-item ${e.id === selected ? "sel" : ""}`, id: `ev-${e.id}` },
        h("div", { class: "row-wrap" }, badge(e.id, "primary"), h("span", { class: "mono", text: `${e.path}:${e.start_line}-${e.end_line}` }),
          e.symbol ? h("span", { class: "muted small", text: e.symbol }) : null),
        h("div", { class: "row-wrap", style: { margin: "8px 0" } }, (e.methods || []).map((m) => badge(m)), badge(`revision ${shortRev(e.revision)}`)),
        (e.uncertainty || []).map((u) => callout("warn", u)),
        codeBox,
        h("div", { class: "row", style: { "margin-top": "8px" } },
          h("button", { class: "btn btn-sm", onclick: () => openSource(repoId, e.path, e.start_line, e.end_line) }, icon("file", 14), "Open in file context")));
      mount(codeBox, codeView((e.text || "").split("\n"), e.start_line));
      return el;
    });
    mount(body, h("p", { class: "muted small", text: "Citation validity means these lines exist in the indexed revision. It does not prove that a statement about them is correct." }), nodes);
    const sel = body.querySelector(`#ev-${CSS.escape(selected || "")}`);
    if (sel) sel.scrollIntoView({ block: "start" });
  });
}

export function evidenceChips(ids, evidence, repoId) {
  return (ids || []).map((id) => h("button", { class: "chip", onclick: () => openEvidence(evidence, id, repoId), title: "Show cited source" }, id));
}

export function locationLink(repoId, path, line, endLine) {
  if (!path) return h("span", { class: "muted", text: "—" });
  return h("button", { class: "chip", onclick: (e) => { e.stopPropagation(); openSource(repoId, path, line || 1, endLine || line || 1); } }, `${path}${line ? ":" + line : ""}`);
}

/* ---------------------------------------------------------------- analysis result */
const CLAIM_STATUS = { "citation-valid": ["success", "citation verified"], "partially-supported": ["warn", "partly supported"],
  "unverified-hypothesis": ["warn", "unverified hypothesis"], "uncited-recommendation": ["", "no citation"], "uncited-interpretation": ["", "no citation"] };
const KIND_TITLE = { fact: "Verified repository facts", interpretation: "Interpretations and unverified hypotheses", recommendation: "Recommendations" };

export function claimList(section, evidence, repoId, kinds = ["fact", "interpretation", "recommendation"]) {
  const out = [];
  for (const kind of kinds) {
    const cs = (section.claims || []).filter((c) => c.kind === kind);
    if (!cs.length) continue;
    out.push(h("h3", { text: KIND_TITLE[kind] }), h("div", {}, cs.map((c) => {
      const [k, t] = CLAIM_STATUS[c.status] || ["", c.status];
      return h("div", { class: "claim" }, h("div", {}, h("div", {}, inlineMd(c.text)),
        h("div", { class: "meta" }, badge(t, k), evidenceChips(c.evidence_ids, evidence, repoId), (c.notes || []).map((n) => h("span", { class: "muted small", text: n })))));
    })));
  }
  return out.length ? h("div", {}, out) : h("p", { class: "muted", text: "No claims were produced." });
}

export function verificationBar(v) {
  if (!v || !v.total) return null;
  const pct = Math.round((v.citation_valid / v.total) * 100);
  return h("div", { class: "card" }, h("div", { class: "row" }, h("strong", { text: "Citation check" }), badge(`${v.citation_valid}/${v.total} claims verified`, pct === 100 ? "success" : "warn"),
    v.unverified ? badge(`${v.unverified} unverified`, "warn") : null, v.removed ? badge(`${v.removed} removed`, "danger") : null,
    h("span", { class: "muted small", text: `coverage ${v.citation_coverage ?? "n/a"}` })),
  h("div", { class: "progress", style: { margin: "10px 0" } }, h("span", { style: { "--w": `${pct}%` } })), h("p", { class: "muted small", text: v.note }));
}

export function resultHeader(res) {
  const rows = [h("div", { class: "row-wrap" }, statusPill(res.status),
    res.ai.used ? badge(`AI interpretation · ${res.ai.model}`, "primary") : badge("Deterministic analysis (no AI)", ""),
    badge(`revision ${shortRev(res.revision)}`), badge(`${res.elapsed_s}s`), badge(res.engine.split(" ")[0]), h("span", { class: "muted small", text: fmtDate(res.generated_at) }))];
  if (!res.ai.used) rows.push(callout("info", h("strong", { text: "AI interpretation was not used. " }),
    res.ai.error ? `${res.ai.error} ` : "", "Results below come from static analysis and retrieval, each linked to source evidence."));
  if (res.stale_index) rows.push(callout("warn", "The repository changed after it was indexed. Results describe the indexed revision; update the index to refresh."));
  if (res.errors?.length) rows.push(callout("danger", h("strong", { text: "Partial result. " }), res.errors.join(" · ")));
  return h("div", { class: "stack" }, rows);
}

export function downloadButtons(res, repoId) {
  const base = `codemind-${res.tasks[0] || "report"}`;
  return h("div", { class: "row-wrap" },
    h("button", { class: "btn btn-sm", onclick: () => download(`${base}.json`, JSON.stringify(res, null, 2), "application/json") }, icon("download", 14), "JSON"),
    res.report ? h("button", { class: "btn btn-sm", onclick: async () => { try { download(`${base}.md`, await api.report(repoId, res.report.report_id), "text/markdown"); } catch (e) { /* toast handled by caller */ } } }, icon("download", 14), "Markdown report") : null);
}

/* generic renderer used by chat; specialised pages compose the same pieces */
export function resultView(res, { repoId = currentRepo()?.id, kinds, extra } = {}) {
  const sections = res.sections.map((s) => card(`${s.task[0].toUpperCase() + s.task.slice(1)} — ${s.question.length > 90 ? s.question.slice(0, 90) + "…" : s.question}`,
    h("div", { class: "stack" },
      s.summary ? h("p", { text: s.summary }) : null,
      s.insufficient ? callout("warn", h("strong", { text: "Insufficient evidence. " }), "The index does not contain enough matching code or documentation to answer without guessing.") : null,
      extra ? extra(s) : null,
      claimList(s, res.evidence, repoId, kinds),
      (s.notes || []).length ? h("details", {}, h("summary", { text: "Notes and limitations" }), h("ul", { class: "md-list" }, [...new Set(s.notes)].map((n) => h("li", { text: n })))) : null)));
  return h("div", { class: "stack" }, resultHeader(res), sections, verificationBar(res.verification), downloadButtons(res, repoId));
}

export function needRepo(root, { ready = true } = {}) {
  const repo = currentRepo();
  if (!repo) { mount(root, emptyState("folder", "No repository yet", "Add a repository to start analysing it.",
    h("a", { class: "btn btn-primary", href: "#/repos" }, icon("plus", 16), "Add repository"))); return null; }
  if (ready && repo.status !== "ready") {
    mount(root, emptyState("refresh", `Repository is ${repo.status}`, repo.last_error || "Index it first so there is evidence to analyse.",
      h("a", { class: "btn btn-primary", href: "#/repos" }, "Go to Repositories"))); return null; }
  return repo;
}

export function symbolInput(repoId, placeholder, listId = "symbols") {
  const dl = h("datalist", { id: listId });
  const input = h("input", { class: "input", list: listId, placeholder, autocomplete: "off", spellcheck: "false" });
  let t;
  input.addEventListener("input", () => {
    clearTimeout(t);
    t = setTimeout(async () => {
      if (input.value.length < 2) return;
      try { const r = await api.symbols(repoId, input.value, 15); mount(dl, r.map((s) => h("option", { value: s.qualified_name }, `${s.type} · ${s.path || ""}`))); } catch { /* ignore */ }
    }, 220);
  });
  return { input, el: h("div", {}, input, dl) };
}
