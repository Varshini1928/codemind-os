import { api } from "../api.js";
import { debounce, h, icon, mount } from "../dom.js";
import { badge, callout, card, codeView, emptyState, errorCallout, loading, needRepo, pageHead, statusPill } from "../components.js";

const PARSE_KIND = { parsed: "success", syntax_error: "danger", partial: "warn", text_only: "" };

export default async function explorer(root) {
  const repo = needRepo(root);
  if (!repo) return;
  const list = h("div", { class: "file-list" }), viewer = h("div"), symBox = h("div");
  const q = h("input", { class: "input", placeholder: "Filter files…", "aria-label": "Filter files" });
  const lang = h("select", { class: "select", "aria-label": "Language" }, h("option", { value: "", text: "All languages" }));
  const sym = h("input", { class: "input", placeholder: "Find a function, class or endpoint…", "aria-label": "Symbol search" });
  let files = [], active = "";

  async function open(path, hl = null) {
    active = path; drawList();
    mount(viewer, card(path, loading(8)));
    try {
      const f = files.find((x) => x.path === path);
      const r = await api.source(repo.id, path, 1, 2000);
      mount(viewer, card(path, h("div", { class: "stack" },
        h("div", { class: "row-wrap" }, badge(f?.language || ""), f ? badge(`parse: ${f.parse_status.replace("_", " ")}`, PARSE_KIND[f.parse_status]) : null, badge(`${r.total_lines} lines`)),
        f && f.parse_status !== "parsed" ? callout("warn", "Structure of this file was extracted heuristically or not at all, so graph data for it is partial.") : null,
        r.total_lines > r.end_line ? callout("info", `Showing the first ${r.end_line} lines.`) : null,
        codeView(r.lines, r.start_line, hl))));
      const first = viewer.querySelector("#cv-first"); if (first) first.scrollIntoView({ block: "center" });
    } catch (e) { mount(viewer, errorCallout(e)); }
  }

  function drawList() {
    mount(list, files.length ? files.slice(0, 600).map((f) => h("div", { class: `file-item ${f.path === active ? "active" : ""}`, onclick: () => open(f.path), role: "button", tabindex: "0",
      onkeydown: (e) => { if (e.key === "Enter") open(f.path); } }, h("span", { text: f.path }), h("span", { class: "muted", text: f.lines }))) : h("p", { class: "muted", text: "No files match." }));
  }
  async function load() {
    mount(list, loading(6));
    try { files = await api.files(repo.id, { language: lang.value, q: q.value }); drawList(); } catch (e) { mount(list, errorCallout(e)); }
  }
  const searchSym = debounce(async () => {
    if (sym.value.trim().length < 2) { mount(symBox); return; }
    try {
      const r = await api.symbols(repo.id, sym.value.trim(), 30);
      mount(symBox, r.length ? h("div", { class: "stack" }, r.map((s) => h("div", { class: "file-item", role: "button", tabindex: "0", onclick: () => open(s.path, [s.start_line, s.end_line]) },
        h("span", {}, h("strong", { text: s.type + " " }), s.qualified_name), h("span", { class: "muted", text: `${s.path}:${s.start_line}` })))) : h("p", { class: "muted", text: "No symbols found." }));
    } catch (e) { mount(symBox, errorCallout(e)); }
  }, 250);
  q.addEventListener("input", debounce(load, 250)); lang.addEventListener("change", load); sym.addEventListener("input", searchSym);

  mount(root, pageHead("Explorer", "Browse indexed files and symbols. Excluded files (secrets, binaries, dependencies) never appear here."),
    h("div", { class: "split" }, h("div", { class: "stack" }, card("Files", h("div", { class: "stack" }, q, lang, list)), card("Symbols", h("div", { class: "stack" }, sym, symBox))),
      h("div", {}, viewer)));
  mount(viewer, emptyState("file", "Select a file", "Pick a file or symbol to view the indexed source."));
  try {
    const all = await api.files(repo.id, {});
    [...new Set(all.map((f) => f.language))].sort().forEach((l) => lang.append(h("option", { value: l, text: l })));
  } catch { /* handled in load */ }
  load();
}
