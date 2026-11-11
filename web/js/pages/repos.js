import { api } from "../api.js";
import { clear, confirmDialog, fmtDate, h, icon, mount, toast } from "../dom.js";
import { badge, callout, card, emptyState, field, pageHead, statusPill, table } from "../components.js";
import { addCleanup, refreshRepos, setRepo, state } from "../state.js";
import { withBusy } from "./shared.js";

const jobKey = (id) => `codemind.job.${id}`;

export default async function repos(root) {
  const s = state.session;
  const jobPanel = h("div");
  const listBox = h("div");
  let mode = s.local_paths_enabled ? "path" : "git";

  const urlInput = h("input", { class: "input", placeholder: "https://github.com/owner/repository", autocomplete: "off", spellcheck: "false" });
  const pathInput = h("input", { class: "input", placeholder: "C:\\projects\\my-repo   or   /home/me/my-repo", autocomplete: "off", spellcheck: "false" });
  const addBtn = h("button", { class: "btn btn-primary" }, icon("plus", 16), "Add repository");
  const formBody = h("div", { class: "stack" });

  function drawForm() {
    mount(formBody,
      s.local_paths_enabled ? h("div", { class: "segmented" },
        h("button", { class: mode === "path" ? "active" : "", onclick: () => { mode = "path"; drawForm(); } }, "Local folder"),
        h("button", { class: mode === "git" ? "active" : "", onclick: () => { mode = "git"; drawForm(); } }, "Git URL")) : null,
      mode === "path" ? field("Repository folder", pathInput, "Absolute path on the machine running CodeMind. Indexing is read-only; nothing in the repository is executed or modified.")
        : field("Public Git URL", urlInput, s.allowed_git_hosts?.length ? `Allowed hosts: ${s.allowed_git_hosts.join(", ")}. https only, public repositories.` : "https or ssh URL without embedded credentials."),
      h("div", {}, addBtn));
  }
  addBtn.onclick = () => withBusy(addBtn, async () => {
    const body = mode === "path" ? { path: pathInput.value.trim() } : { git_url: urlInput.value.trim() };
    if (!Object.values(body)[0]) { toast("Enter a repository location first.", "error"); return; }
    const r = await api.register(body);
    toast(`Added ${r.name}`, "success");
    setRepo(r.id); await refreshRepos(); drawList();
  });

  function watch(jobId, repoId) {
    sessionStorage.setItem(jobKey(repoId), jobId);
    const box = h("div", { class: "stack" });
    mount(jobPanel, card("Indexing", box));
    let timer;
    const tick = async () => {
      let j;
      try { j = await api.job(jobId); } catch { clearInterval(timer); return; }
      mount(box, h("div", { class: "row" }, statusPill(j.status), h("span", { class: "muted", text: j.message || "" }), h("div", { class: "spacer" }),
        ["queued", "running"].includes(j.status) ? h("button", { class: "btn btn-sm", onclick: () => api.cancelJob(jobId).then(() => toast("Cancel requested")) }, "Cancel") : null),
        h("div", { class: "progress" }, h("span", { style: { "--w": `${Math.round((j.progress || 0) * 100)}%` } })),
        j.error ? callout("danger", j.error) : null,
        j.status === "succeeded" && j.result ? h("p", { class: "muted small", text: `Added ${j.result.added}, modified ${j.result.modified}, deleted ${j.result.deleted}, unchanged ${j.result.unchanged}; ${j.result.new_chunks} new chunks; embeddings: ${j.result.embedder}.` }) : null,
        ...(j.result?.warnings || []).slice(0, 4).map((w) => callout("warn", w)),
        h("details", {}, h("summary", { text: "Log" }), h("pre", { class: "code-block", text: (j.logs || []).slice(-40).join("\n") || "(empty)" })));
      if (!["queued", "running"].includes(j.status)) {
        clearInterval(timer); sessionStorage.removeItem(jobKey(repoId));
        toast(j.status === "succeeded" ? "Indexing finished" : `Indexing ${j.status}`, j.status === "succeeded" ? "success" : "error");
        await refreshRepos(); drawList();
      }
    };
    timer = setInterval(tick, 1200); tick();
    addCleanup(() => clearInterval(timer));
  }

  async function start(repo, full) {
    const r = await api.index(repo.id, full);
    watch(r.job_id, repo.id);
  }

  function drawList() {
    const rs = state.repos;
    mount(listBox, rs.length ? table(["Repository", "Status", "Files", "Chunks", "Indexed", "Actions"], rs.map((r) => [
      h("div", {}, h("strong", { text: r.name }), h("div", { class: "muted small mono", text: r.root })),
      h("div", {}, statusPill(r.status), r.git_url ? h("div", { class: "muted small", text: "cloned" }) : null),
      r.file_count ?? 0, r.chunk_count ?? 0, fmtDate(r.indexed_at),
      h("div", { class: "row-wrap" },
        r.id === state.repoId ? badge("selected", "primary") : h("button", { class: "btn btn-sm", onclick: () => { setRepo(r.id); drawList(); } }, "Select"),
        h("button", { class: "btn btn-sm btn-primary", onclick: (e) => withBusy(e.currentTarget, () => start(r, false)) }, icon("refresh", 14), r.status === "ready" ? "Update index" : "Index"),
        h("button", { class: "btn btn-sm", onclick: async (e) => { if (await confirmDialog("Full re-index", "Rebuild every chunk, embedding and graph edge from scratch?", "Re-index")) withBusy(e.currentTarget, () => start(r, true)); } }, "Full re-index"),
        h("button", { class: "btn btn-sm btn-ghost", title: "Delete index", onclick: async () => { if (await confirmDialog("Delete index", `Remove the index of ${r.name}? Source files are not touched.`, "Delete index", true)) { await api.deleteIndex(r.id); await refreshRepos(); drawList(); toast("Index deleted", "success"); } } }, icon("trash", 14)),
        h("button", { class: "btn btn-sm btn-ghost", title: "Remove repository", onclick: async () => { if (await confirmDialog("Remove repository", s.hosted ? "Removes the repository, its index and its cloned copy from this server." : "Removes the repository registration and its index. Your files stay where they are.", "Remove", true)) { await api.unregister(r.id); await refreshRepos(); drawList(); } } }, "Remove")),
    ])) : emptyState("folder", "No repositories yet", "Add a folder or Git URL above to start."));
  }

  mount(root, pageHead("Repositories", "Add a repository and build its evidence index."),
    s.hosted ? callout("info", h("strong", { text: "Hosted deployment. " }), "Repositories are cloned onto this server and analysed there. Only add code you are comfortable processing on this host. Repository code is never executed.") : null,
    h("div", { class: "stack" }, card("Add a repository", formBody), jobPanel, card("Your repositories", listBox)));
  drawForm(); drawList();
  const cur = state.repos.find((r) => r.status === "indexing");
  const saved = cur && sessionStorage.getItem(jobKey(cur.id));
  if (saved) watch(saved, cur.id);
}
