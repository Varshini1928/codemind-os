import { api } from "../api.js";
import { confirmDialog, download, h, icon, mount, toast } from "../dom.js";
import { badge, card, emptyState, errorCallout, loading, pageHead, table } from "../components.js";
import { renderMarkdown } from "../md.js";
import { state } from "../state.js";

export default async function reports(root) {
  const list = h("div"), view = h("div");
  async function load() {
    mount(list, loading(3));
    try {
      const items = await api.reports(state.repoId || undefined);
      mount(list, items.length ? table(["Report", "Repository", "Created", "Size", ""], items.map((r) => [h("strong", { text: r.kind }), r.repository_id.slice(0, 22), r.created.replace("T", " ").slice(0, 15), `${(r.size_bytes / 1024).toFixed(1)} KB`,
        h("div", { class: "row-wrap" }, h("button", { class: "btn btn-sm", onclick: () => open(r) }, "Open"),
          h("button", { class: "btn btn-sm btn-ghost", title: "Delete report", onclick: async () => { if (await confirmDialog("Delete report", "This permanently removes the saved report.", "Delete", true)) { await api.deleteReport(r.repository_id, r.report_id); toast("Report deleted", "success"); load(); mount(view); } } }, icon("trash", 14)))]))
        : emptyState("file", "No saved reports", "Analyses are saved automatically and listed here."));
    } catch (e) { mount(list, errorCallout(e)); }
  }
  async function open(r) {
    mount(view, card(r.kind, loading(5)));
    try {
      const md = await api.report(r.repository_id, r.report_id);
      const dlmd = h("button", { class: "btn btn-sm" }, icon("download", 14), "Markdown");
      dlmd.onclick = () => download(`${r.report_id}.md`, md, "text/markdown");
      const dljs = h("button", { class: "btn btn-sm" }, icon("download", 14), "JSON");
      dljs.onclick = async () => download(`${r.report_id}.json`, await api.report(r.repository_id, r.report_id, "json"), "application/json");
      mount(view, card(`${r.kind} report`, h("div", { class: "stack" }, h("div", { class: "row-wrap" }, dlmd, dljs, badge(r.created)), h("article", {}, renderMarkdown(md, h)))));
      view.scrollIntoView({ behavior: "smooth" });
    } catch (e) { mount(view, errorCallout(e)); }
  }
  mount(root, pageHead("Reports", "Every analysis is saved as Markdown and JSON in the server's storage directory."), h("div", { class: "stack" }, card(null, list), view));
  load();
}
