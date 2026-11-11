import { api } from "../api.js";
import { fmtDate, fmtNum, h, icon, mount, shortRev } from "../dom.js";
import { badge, barChart, callout, card, emptyState, errorCallout, loading, locationLink, needRepo, pageHead, statTile, statusPill, table } from "../components.js";
import { state } from "../state.js";

export default async function overview(root) {
  const repo = needRepo(root, { ready: false });
  if (!repo) return;
  mount(root, pageHead(repo.name, repo.root), loading(6));
  if (repo.status !== "ready") {
    mount(root, pageHead(repo.name, repo.root), emptyState("refresh", `Index status: ${repo.status}`, repo.last_error || "Index the repository to see its structure.",
      h("a", { class: "btn btn-primary", href: "#/repos" }, "Open Repositories")));
    return;
  }
  let ov;
  try { ov = await api.overview(repo.id); } catch (e) { mount(root, pageHead(repo.name, repo.root), errorCallout(e)); return; }
  const r = ov.repository, nFiles = Object.values(ov.languages).reduce((a, b) => a + b, 0);
  const ents = ov.entity_types;
  const llm = state.ready?.llm;
  mount(root,
    pageHead(repo.name, `${repo.root}`, statusPill(r.status), badge(`revision ${shortRev(r.revision)}`), badge(`indexed ${fmtDate(r.indexed_at)}`),
      h("a", { class: "btn btn-primary", href: "#/ask" }, icon("ask", 16), "Ask a question")),
    r.stale ? callout("warn", h("strong", { text: "Files changed since indexing. " }), "Update the index on the Repositories page to refresh results.") : null,
    ...(r.warnings || []).slice(0, 3).map((w) => callout("warn", w)),
    h("div", { class: "grid grid-6" }, statTile(fmtNum(nFiles), "files indexed"), statTile(fmtNum(r.chunk_count), "code chunks"),
      statTile(fmtNum((ents.Function || 0) + (ents.Method || 0)), "functions & methods"), statTile(fmtNum(ents.Class || 0), "classes"),
      statTile(fmtNum(ov.endpoints.length), "endpoints"), statTile(fmtNum(ov.config_keys.length), "config keys")),
    h("div", { class: "grid grid-2" },
      card("Languages", barChart(ov.languages)), card("Code entities", barChart(ents)),
      card("Entry points & endpoints", h("div", { class: "stack" },
        ov.entry_points.length ? table(["Entry point", "Line"], ov.entry_points.map((e) => [locationLink(repo.id, e.path, e.line), e.line])) : h("p", { class: "muted", text: "No __main__ entry points detected." }),
        ov.endpoints.length ? table(["Route", "Handler", "Location"], ov.endpoints.map((e) => [h("code", { class: "inline-code", text: e.route }), e.handler || "—", locationLink(repo.id, e.path, e.line)])) : null)),
      card("Configuration keys", ov.config_keys.length ? table(["Key", "Declared in", "Read by"], ov.config_keys.map((k) => [h("code", { class: "inline-code", text: k.key }),
        k.declared_in ? locationLink(repo.id, k.declared_in, k.line) : h("span", { class: "muted", text: "not declared" }), (k.read_by || []).slice(0, 2).join(", ") || "—"])) : h("p", { class: "muted", text: "No environment variables detected." })),
      card("External dependencies", Object.keys(ov.external_dependencies).length ? h("div", { class: "row-wrap" }, Object.entries(ov.external_dependencies).map(([k, v]) => badge(`${k} · ${v}`))) : h("p", { class: "muted", text: "None detected." })),
      card("Index health", h("div", { class: "stack" },
        h("div", { class: "row-wrap" }, Object.entries(ov.parse_status).map(([k, v]) => badge(`${k.replace("_", " ")}: ${v}`, k === "parsed" ? "success" : k === "syntax_error" ? "danger" : "warn"))),
        h("p", { class: "muted small", text: "Parse status shows how reliably each file's structure was extracted. 'text only' and 'partial' files use heuristics." }),
        Object.keys(ov.excluded).length ? h("details", {}, h("summary", { text: "Excluded files (privacy and safety filters)" }), barChart(ov.excluded)) : null,
        h("div", { class: "row-wrap" }, badge(`embeddings: ${r.embedder || "—"}`), badge(`AI model: ${llm?.available && llm.model_installed ? llm.model : "not available"}`, llm?.available && llm.model_installed ? "success" : "warn"))))));
}
