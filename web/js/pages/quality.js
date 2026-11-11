import { api } from "../api.js";
import { h, icon, mount } from "../dom.js";
import { badge, callout, card, claimList, emptyState, evidenceChips, field, locationLink, needRepo, pageHead, resultHeader, statTile, verificationBar, downloadButtons } from "../components.js";
import { llmToggle, withBusy } from "./shared.js";

const SEV = { high: "danger", medium: "warn", low: "info", info: "" };

export default async function quality(root) {
  const repo = needRepo(root);
  if (!repo) return;
  const focus = h("input", { class: "input", placeholder: "Optional focus, e.g. “input validation and error handling”" });
  const ai = llmToggle();
  const run = h("button", { class: "btn btn-primary" }, icon("shield", 16), "Run review");
  const out = h("div");
  let res, sev = new Set(["high", "medium", "low", "info"]), cat = "", text = "";

  function draw() {
    if (!res) return;
    const sec = res.sections[0], f = sec.payload.findings || [], counts = sec.payload.severity_counts || {};
    const cats = [...new Set(f.map((x) => x.category))];
    const shown = f.filter((x) => sev.has(x.severity) && (!cat || x.category === cat) && (!text || (x.title + x.path + x.explanation).toLowerCase().includes(text.toLowerCase())));
    const rows = shown.map((x) => h("div", { class: "card" },
      h("div", { class: "row-wrap" }, badge(x.severity.toUpperCase(), SEV[x.severity]), badge(x.category), h("strong", { text: x.title }), h("span", { class: "spacer" }), locationLink(repo.id, x.path, x.line, x.end), evidenceChips(x.evidence_ids, res.evidence, repo.id)),
      h("p", { text: x.explanation, style: { margin: "8px 0 4px" } }),
      h("dl", { class: "kv" }, h("dt", { text: "Uncertainty" }), h("dd", { text: x.uncertainty }), h("dt", { text: "Suggested next step" }), h("dd", { text: x.next_step }),
        h("dt", { text: "How it was found" }), h("dd", {}, h("code", { class: "inline-code", text: x.method }), " ", badge("candidate, not confirmed"))),
    ));
    mount(out, h("div", { class: "stack" }, resultHeader(res),
      h("div", { class: "grid grid-4" }, ["high", "medium", "low", "info"].map((s) => statTile(counts[s] ?? 0, `${s} severity`))),
      callout("info", "Findings are candidates produced by rules (and optionally an AI model). They are not confirmed defects; review each with its evidence. ", sec.payload.scope?.not_scanned || ""),
      card("Filters", h("div", { class: "row-wrap" },
        ...["high", "medium", "low", "info"].map((s) => h("button", { class: `chip ${sev.has(s) ? "" : "off"}`, onclick: () => { sev.has(s) ? sev.delete(s) : sev.add(s); draw(); } }, s)),
        h("select", { class: "select", style: { width: "180px" }, onchange: (e) => { cat = e.target.value; draw(); } }, h("option", { value: "", text: "All categories" }), cats.map((c) => h("option", { value: c, selected: c === cat ? "" : null, text: c }))),
        h("input", { class: "input", style: { width: "220px" }, placeholder: "Search findings…", value: text, oninput: (e) => { text = e.target.value; clearTimeout(draw.t); draw.t = setTimeout(draw, 250); } }))),
      rows.length ? rows : (f.length ? h("p", { class: "muted", text: "No findings match the filters." }) : emptyState("check", "No rule-based findings", "This does not mean the code is free of defects.")),
      sec.claims.some((c) => c.kind !== "fact") ? card("AI suggestions and recommendations", claimList(sec, res.evidence, repo.id, ["interpretation", "recommendation"])) : null,
      verificationBar(res.verification), downloadButtons(res, repo.id)));
  }
  run.onclick = () => withBusy(run, async () => { res = await api.quality(repo.id, focus.value.trim(), ai.value()); draw(); });
  mount(root, pageHead("Code quality", "Rule-based review with severity, uncertainty and a concrete next step for every candidate finding."),
    h("div", { class: "stack" }, card(null, h("div", { class: "stack" }, field("Focus (optional)", focus), h("div", { class: "row" }, run, ai.el))), out));
  mount(out, emptyState("shield", "No review yet", "Run a review to list candidate issues with citations."));
}
