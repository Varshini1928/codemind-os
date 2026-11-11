import { api } from "../api.js";
import { h, icon, mount } from "../dom.js";
import { badge, callout, card, claimList, emptyState, evidenceChips, field, locationLink, needRepo, pageHead, resolutionBadge, resultHeader, statTile, table, tabs, symbolInput, verificationBar, downloadButtons } from "../components.js";
import { llmToggle, withBusy } from "./shared.js";

export default async function impact(root) {
  const repo = needRepo(root);
  if (!repo) return;
  const { input, el: inputEl } = symbolInput(repo.id, "Symbol or file path, e.g. add_stock or inventory/service.py", "impact-symbols");
  const change = h("textarea", { class: "textarea", rows: 2, placeholder: "Describe the proposed change (optional) — e.g. “rename the amount parameter and allow zero”" });
  const depth = h("select", { class: "select" }, [2, 3, 4, 5].map((d) => h("option", { value: d, selected: d === 3 ? "" : null, text: `Depth ${d}` })));
  const ai = llmToggle();
  const run = h("button", { class: "btn btn-primary" }, icon("zap", 16), "Analyze impact");
  const out = h("div");

  const itemRows = (items, res) => items.map((i) => [h("div", {}, h("strong", { text: i.qualified_name || i.name }), h("div", { class: "muted small", text: i.type })),
    h("span", { class: "badge", text: i.via || "—" }), resolutionBadge(i.resolution), String(i.depth ?? ""), locationLink(repo.id, i.edge_path || i.path, i.edge_line || i.start_line), evidenceChips(i.evidence_ids, res.evidence, repo.id)]);
  const H5 = ["Symbol", "Via", "Confidence", "Hops", "Location", "Evidence"];

  function draw(res) {
    const sec = res.sections[0], p = sec.payload;
    if (res.status === "insufficient_evidence" && !p.direct_dependents) {
      mount(out, h("div", { class: "stack" }, callout("warn", h("strong", { text: "Cannot analyze. " }), sec.summary),
        (p.candidates || []).length ? card("Did you mean…", h("div", { class: "row-wrap" }, p.candidates.map((c) => h("button", { class: "btn btn-sm", onclick: () => { input.value = c.qualified_name; run.click(); } }, c.qualified_name)))) : null));
      return;
    }
    const tabDefs = [
      { label: "Dependents", count: p.direct_dependents.length + p.transitive_dependents.length, render: () => h("div", {},
        h("h3", { text: "Direct" }), table(H5, itemRows(p.direct_dependents, res), { empty: "No direct dependents found." }),
        h("h3", { text: "Transitive (possible)" }), table(H5, itemRows(p.transitive_dependents, res), { empty: "No transitive dependents found." }),
        p.implementations.length ? h("div", {}, h("h3", { text: "Implementations / subclasses" }), table(H5, itemRows(p.implementations, res))) : null) },
      { label: "Tests", count: p.tests.length, render: () => h("div", {}, table(["Test", "Link", "Confidence", "Hops", "Location", "Evidence"], itemRows(p.tests, res), { empty: "No related tests were found — consider adding some." }),
        h("h3", { text: "Suggested tests to run" }), (p.recommended_tests?.to_run || []).length ? h("ul", { class: "md-list" }, p.recommended_tests.to_run.map((t) => h("li", {}, h("code", { class: "inline-code", text: t })))) : h("p", { class: "muted", text: "None identified." }),
        h("p", { class: "muted small", text: p.recommended_tests?.note || "" })) },
      { label: "Config & endpoints", count: p.configuration.length + p.exposed_endpoints.length, render: () => h("div", {},
        h("h3", { text: "Configuration read by affected code" }), table(["Key", "Confidence", "Evidence"], p.configuration.map((c) => [h("code", { class: "inline-code", text: c.name }), resolutionBadge(c.resolution), evidenceChips(c.evidence_ids, res.evidence, repo.id)]), { empty: "No configuration dependencies." }),
        h("h3", { text: "Externally visible endpoints" }), table(["Endpoint", "Confidence", "Evidence"], p.exposed_endpoints.map((c) => [c.name, resolutionBadge(c.resolution), evidenceChips(c.evidence_ids, res.evidence, repo.id)]), { empty: "No endpoints affected." })) },
      { label: "Docs", count: (p.documentation_to_review || []).length, render: () => table(["Document", "Lines", "Evidence"], (p.documentation_to_review || []).map((d) => [locationLink(repo.id, d.path, +d.lines.split("-")[0], +d.lines.split("-")[1]), d.lines, evidenceChips(d.evidence_ids, res.evidence, repo.id)]), { empty: "No documentation mentions this target." }) },
      { label: "Unresolved", count: p.unresolved_possible_callers.length, render: () => h("div", {}, callout("warn", "These call sites use the same name but could not be linked by static analysis (dynamic dispatch, untyped receivers). They are NOT confirmed callers."),
        table(["Caller", "Call", "Location"], p.unresolved_possible_callers.map((u) => [u.caller, h("code", { class: "inline-code", text: u.call }), locationLink(repo.id, u.path, u.line)]), { empty: "No unresolved possible callers." })) },
      { label: "Claims & evidence", render: () => claimList(sec, res.evidence, repo.id) },
    ];
    mount(out, h("div", { class: "stack" }, resultHeader(res),
      h("div", { class: "grid grid-6" }, statTile(p.direct_dependents.length, "direct dependents"), statTile(p.transitive_dependents.length, "transitive"), statTile(p.tests.length, "related tests"),
        statTile(p.configuration.length, "config keys"), statTile(p.exposed_endpoints.length, "endpoints"), statTile(p.unresolved_possible_callers.length, "unresolved")),
      sec.summary ? card("Summary", h("p", { text: sec.summary })) : null,
      callout("info", h("strong", { text: "Coverage limit. " }), (p.limits || []).join(" ")),
      card(null, tabs(tabDefs)), verificationBar(res.verification), downloadButtons(res, repo.id)));
  }
  run.onclick = () => withBusy(run, async () => {
    if (!input.value.trim()) { throw new Error("Choose a target symbol or file first."); }
    draw(await api.impact(repo.id, input.value.trim(), change.value.trim(), +depth.value, ai.value()));
  });
  mount(root, pageHead("Change impact", "Trace what a change can affect using extracted call, import and inheritance edges — with every link labelled by how certain it is."),
    h("div", { class: "stack" }, card(null, h("div", { class: "stack" }, h("div", { class: "grid grid-2" }, field("Target", inputEl), field("Traversal depth", depth)), field("Proposed change", change), h("div", { class: "row" }, run, ai.el))), out));
  mount(out, emptyState("zap", "Pick a target", "Choose a function, class or file to see dependents, related tests, configuration and docs."));
}
