import { api } from "../api.js";
import { download, h, icon, mount, toast } from "../dom.js";
import { badge, callout, card, claimList, emptyState, evidenceChips, field, needRepo, pageHead, resultHeader, symbolInput, verificationBar, inlineMd } from "../components.js";
import { state } from "../state.js";
import { llmToggle, withBusy } from "./shared.js";

const CAT = { behavior: "primary", exception: "danger", branch: "info" };

export function planMarkdown(tests) {
  const L = ["# Test plan (proposal — nothing was executed)", ""];
  for (const t of tests) {
    L.push(`## ${t.id} [${t.category}] ${t.target}`, `- **Behavior:** ${t.behavior}`, `- **Evidence:** ${t.evidence_ids.join(", ") || "none"}`,
      `- **Inputs/fixtures:** ${t.inputs_fixtures.join("; ")}`, `- **Expected result:** ${t.expected_result} _(${t.expected_basis})_`);
    if (t.boundary_conditions.length) L.push(`- **Boundaries:** ${t.boundary_conditions.join("; ")}`);
    if (t.exception_scenarios.length) L.push(`- **Exceptions:** ${t.exception_scenarios.join(", ")}`);
    L.push(`- **Existing tests:** ${t.existing_tests.join(", ") || "none found"}`, `- **Rationale:** ${t.rationale}`, "");
  }
  return L.join("\n");
}

export default async function tests(root) {
  const repo = needRepo(root);
  if (!repo) return;
  const { input, el: inputEl } = symbolInput(repo.id, "Function, class or file (leave empty for the whole repository)", "test-symbols");
  const ai = llmToggle();
  const run = h("button", { class: "btn btn-primary" }, icon("tests", 16), "Create test plan");
  const out = h("div");

  function draw(res) {
    const sec = res.sections[0], list = sec.payload.tests || [];
    if (!list.length) { mount(out, h("div", { class: "stack" }, callout("warn", sec.summary || "No testable functions were found for this target."))); return; }
    const cards = list.map((t) => h("div", { class: "test-card" },
      h("div", { class: "row-wrap" }, badge(t.id, "primary"), badge(t.category, CAT[t.category]), h("strong", { text: t.target }), h("span", { class: "spacer" }),
        t.untested ? badge("no linked existing test", "warn") : badge(`${t.existing_tests.length} linked test(s)`, "success"), evidenceChips(t.evidence_ids, res.evidence, repo.id)),
      h("dl", { class: "kv" },
        h("dt", { text: "Behavior under test" }), h("dd", {}, inlineMd(t.behavior)),
        h("dt", { text: "Inputs / fixtures" }), h("dd", {}, t.inputs_fixtures.map((x) => h("div", {}, inlineMd(x)))),
        h("dt", { text: "Expected result" }), h("dd", {}, inlineMd(t.expected_result), " ", h("span", { class: "muted small", text: `(${t.expected_basis})` })),
        t.boundary_conditions.length ? [h("dt", { text: "Boundary conditions" }), h("dd", {}, t.boundary_conditions.map((x) => h("div", {}, inlineMd(x))))] : null,
        t.exception_scenarios.length ? [h("dt", { text: "Exception scenarios" }), h("dd", { text: t.exception_scenarios.join(", ") })] : null,
        h("dt", { text: "Rationale" }), h("dd", { text: t.rationale }))));
    const md = h("button", { class: "btn btn-sm" }, icon("download", 14), "Download plan (.md)");
    md.onclick = () => download(`test-plan-${repo.name}.md`, planMarkdown(list), "text/markdown");
    const exp = state.session.hosted ? null : h("button", { class: "btn btn-sm" }, "Export to review folder");
    if (exp) exp.onclick = () => withBusy(exp, async () => { const r = await api.exportTests(repo.id, input.value.trim()); toast(`Saved to ${r.markdown}`, "success", 8000); });
    mount(out, h("div", { class: "stack" }, resultHeader(res), sec.summary ? card("Summary", h("p", { text: sec.summary })) : null,
      callout("info", sec.payload.coverage_note || "Test code is not generated and nothing is executed."),
      h("div", { class: "row-wrap" }, md, exp), cards,
      sec.claims.some((c) => c.kind === "interpretation") ? card("AI proposals (unverified)", claimList(sec, res.evidence, repo.id, ["interpretation", "recommendation"])) : null, verificationBar(res.verification)));
  }
  run.onclick = () => withBusy(run, async () => draw(await api.tests(repo.id, input.value.trim(), ai.value())));
  mount(root, pageHead("Test planner", "Evidence-linked test specifications — behaviour, inputs, expected result, boundaries and exceptions — before any test code exists."),
    h("div", { class: "stack" }, card(null, h("div", { class: "stack" }, field("Target", inputEl), h("div", { class: "row" }, run, ai.el))), out));
  mount(out, emptyState("tests", "No plan yet", "Select a target to propose test cases derived from the code's real branches, raises and parameters."));
}
