import { api } from "../api.js";
import { h, icon, mount } from "../dom.js";
import { callout, emptyState, errorCallout, needRepo, pageHead, resultView } from "../components.js";
import { state } from "../state.js";
import { llmToggle, withBusy } from "./shared.js";

const SUGGESTIONS = ["Give me an architecture overview", "What are the security risks in this codebase?", "Which tests cover the core logic?",
  "What is the impact of changing the main service module?", "Generate documentation for the public API"];

export default async function ask(root) {
  const repo = needRepo(root);
  if (!repo) return;
  const chat = (state.chats[repo.id] ||= []);
  const thread = h("div", { class: "chat" });
  const box = h("textarea", { class: "textarea", rows: 2, placeholder: "Ask about this repository — e.g. “Where is the discount calculated and what depends on it?”", "aria-label": "Question" });
  const ai = llmToggle();
  const send = h("button", { class: "btn btn-primary" }, icon("ask", 16), "Ask");

  function draw() {
    mount(thread, chat.length ? chat.map((m) => h("div", { class: "stack" }, h("div", { class: "msg-q", text: m.q }),
      m.pending ? h("div", { class: "card" }, h("span", { class: "spinner" }), " Retrieving evidence and verifying citations…") : m.error ? errorCallout(m.error) : resultView(m.res, { repoId: repo.id })))
      : [emptyState("ask", "Ask anything about your code", "Answers cite the exact file and lines they rely on, and say so when the evidence is insufficient.",
        h("div", { class: "row-wrap", style: { "justify-content": "center", "margin-top": "14px" } }, SUGGESTIONS.map((s) => h("button", { class: "btn btn-sm", onclick: () => { box.value = s; box.focus(); } }, s))))]);
  }

  async function submit() {
    const q = box.value.trim();
    if (q.length < 3) return;
    const entry = { q, pending: true }; chat.push(entry); box.value = ""; draw();
    entry.scroll = true;
    try { entry.res = await api.ask(repo.id, q, ai.value()); } catch (e) { entry.error = e; }
    entry.pending = false; draw();
    thread.lastElementChild?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  send.onclick = () => withBusy(send, submit);
  box.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send.click(); });
  mount(root, pageHead("Ask", "Evidence-grounded answers. Multi-part questions are split and routed to the right specialist."),
    thread, h("div", { class: "composer" }, h("div", { class: "box" }, box, h("div", { class: "stack", style: { "align-items": "flex-end" } }, ai.el, send)),
      h("p", { class: "muted small", text: "Ctrl+Enter to send. Interpretations without a valid citation are labelled as unverified hypotheses." })));
  draw();
}
