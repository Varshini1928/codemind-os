import { api } from "../api.js";
import { download, h, icon, mount } from "../dom.js";
import { badge, callout, card, emptyState, needRepo, pageHead, resultHeader, verificationBar } from "../components.js";
import { renderMarkdown } from "../md.js";
import { llmToggle, withBusy } from "./shared.js";

const KINDS = [["full", "Full documentation"], ["architecture", "Architecture"], ["modules", "Modules"], ["api", "API summary"], ["setup", "Setup"],
  ["dependencies", "Dependencies"], ["configuration", "Configuration"], ["developer_guide", "Developer guide"]];

export default async function docs(root) {
  const repo = needRepo(root);
  if (!repo) return;
  let kind = "full";
  const ai = llmToggle();
  const run = h("button", { class: "btn btn-primary" }, icon("book", 16), "Generate");
  const seg = h("div", { class: "segmented", style: { "flex-wrap": "wrap" } });
  const out = h("div");
  const drawSeg = () => mount(seg, KINDS.map(([k, t]) => h("button", { class: k === kind ? "active" : "", onclick: () => { kind = k; drawSeg(); } }, t)));

  run.onclick = () => withBusy(run, async () => {
    const res = await api.docs(repo.id, kind, ai.value());
    const sec = res.sections[0], md = sec.payload.markdown || "";
    const dl = h("button", { class: "btn btn-sm" }, icon("download", 14), "Download Markdown");
    dl.onclick = () => download(`${repo.name}-${kind}.md`, md, "text/markdown");
    const facts = sec.claims.filter((c) => c.kind === "fact"), ok = facts.filter((c) => c.status === "citation-valid").length;
    mount(out, h("div", { class: "stack" }, resultHeader(res),
      h("div", { class: "row-wrap" }, dl, badge(`${ok}/${facts.length} cited facts verified`, ok === facts.length ? "success" : "warn")),
      callout("info", "Statements under “Verified repository facts” carry file:line references checked against the indexed revision. “Suggested improvements” are recommendations, not facts."),
      card(null, h("article", {}, renderMarkdown(md, h))), verificationBar(res.verification)));
  });
  drawSeg();
  mount(root, pageHead("Documentation", "Generate Markdown documentation from extracted facts, with file and line references."),
    h("div", { class: "stack" }, card(null, h("div", { class: "stack" }, seg, h("div", { class: "row" }, run, ai.el))), out));
  mount(out, emptyState("book", "Nothing generated yet", "Choose a document type and generate it. Output is separated into verified facts and suggestions."));
}
