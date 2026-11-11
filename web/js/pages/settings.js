import { api } from "../api.js";
import { h, icon, mount, toast } from "../dom.js";
import { badge, callout, card, errorCallout, field, loading, pageHead } from "../components.js";
import { refreshReady, state } from "../state.js";
import { withBusy } from "./shared.js";

export default async function settings(root) {
  mount(root, pageHead("Settings & privacy"), loading(5));
  let s, ready;
  try { [s, ready] = await Promise.all([api.settings(), refreshReady().then(() => state.ready)]); } catch (e) { mount(root, pageHead("Settings & privacy"), errorCallout(e)); return; }
  const llm = ready?.llm || {};
  const isAdmin = state.user?.role === "admin";
  const model = h("input", { class: "input", value: s.ollama_model });
  const saveModel = h("button", { class: "btn btn-primary btn-sm" }, "Save model");
  saveModel.onclick = () => withBusy(saveModel, async () => { await api.putSetting("ollama_model", model.value.trim()); await refreshReady(); toast("Model saved", "success"); settings(root); });
  const weights = ["alpha", "beta", "gamma", "delta"].map((k) => [k, h("input", { class: "input", type: "number", step: "0.05", min: "0", max: "1", value: s[k] })]);
  const saveW = h("button", { class: "btn btn-sm" }, "Save weights");
  saveW.onclick = () => withBusy(saveW, async () => { for (const [k, el] of weights) await api.putSetting(k, parseFloat(el.value)); toast("Ranking weights saved", "success"); });
  const flag = (label, on, good) => h("div", { class: "row" }, badge(on ? "enabled" : "disabled", on === good ? "success" : "warn"), h("span", { text: label }));
  mount(root, pageHead("Settings & privacy", "What runs where, what is stored, and what is allowed."),
    h("div", { class: "stack" },
      s.hosted ? callout("warn", h("strong", { text: "Hosted deployment. " }), "Repositories you add are cloned and indexed on this server, and results are stored in its storage directory. Use it only for code you are allowed to process on this host. Each user only sees their own repositories; administrators can see everything.") : callout("info", h("strong", { text: "Local mode. " }), "Source code, embeddings, graphs and reports stay on this machine. No telemetry is sent."),
      h("div", { class: "grid grid-2" },
        card("System status", h("div", { class: "stack" },
          h("div", { class: "row" }, badge(ready?.database_ok ? "ok" : "problem", ready?.database_ok ? "success" : "danger"), "Database"),
          h("div", { class: "row" }, badge(ready?.vector_store || "?"), "Vector store"),
          h("div", { class: "row" }, badge(ready?.git_available ? "available" : "missing", ready?.git_available ? "success" : "warn"), "Git"),
          h("div", { class: "row" }, badge(llm.available && llm.model_installed ? `online · ${llm.model}` : "offline", llm.available && llm.model_installed ? "success" : "warn"), "Local AI model"),
          !(llm.available && llm.model_installed) ? callout("info", llm.message || "No local model.", " Retrieval, graph analysis and rule-based review work without it.") : null)),
        card("Safety switches", h("div", { class: "stack" }, flag("Writing files into repositories", s.allow_repo_writes, false), flag("Executing repository tests", s.allow_test_execution, false),
          flag("Remote Ollama host", s.allow_remote_ollama, false),
          h("p", { class: "muted small", text: "These can only be changed through server environment variables. They are off by default, and always off in hosted mode." }))),
        card("Local AI model", h("div", { class: "stack" }, field("Ollama model (must already be installed)", isAdmin ? model : h("input", { class: "input", value: s.ollama_model, disabled: "" }), "Models are never downloaded automatically. Install with: ollama pull <model>"), isAdmin ? h("div", {}, saveModel) : h("p", { class: "muted small", text: "Only administrators can change server settings." }))),
        card("Retrieval ranking weights", h("div", { class: "stack" }, h("div", { class: "grid grid-4" }, weights.map(([k, el]) => (isAdmin || (el.disabled = true), field({ alpha: "Semantic", beta: "Graph", gamma: "Lexical", delta: "Recency" }[k], el)))),
          h("p", { class: "muted small", text: "Components are normalised per search, then combined as a weighted average." }), isAdmin ? h("div", {}, saveW) : null)),
        isAdmin ? card("Storage locations", h("dl", { class: "kv" }, Object.entries(s.storage_locations || {}).flatMap(([k, v]) => [h("dt", { text: k.replace("_", " ") }), h("dd", { class: "mono", text: v })]))) : null,
        card("Access", h("div", { class: "stack" }, h("div", { class: "row" }, badge(state.session.auth_mode === "accounts" ? `signed in as ${state.user?.email}` : "single local operator", "success")),
          h("p", { class: "muted small", text: "Secret scanning is best-effort and cannot guarantee that every credential is detected." })))),
      isAdmin ? h("details", {}, h("summary", { text: "All settings" }), h("pre", { class: "code-block", text: JSON.stringify(Object.fromEntries(Object.entries(s).filter(([k]) => k !== "storage_locations")), null, 2) })) : null));
}
