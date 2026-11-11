import { append, clear, h, toast } from "../dom.js";
import { state } from "../state.js";

export function aiAvailable() { const l = state.ready?.llm; return !!(l && l.available && l.model_installed); }

export function llmToggle() {
  const on = aiAvailable();
  const input = h("input", { type: "checkbox", checked: on ? "" : null, disabled: on ? null : "" });
  if (on) input.checked = true;
  const el = h("label", { class: "switch", title: on ? `Local model: ${state.ready.llm.model}` : (state.ready?.llm?.message || "No local model available") },
    input, h("span", { text: on ? "AI interpretation" : "AI interpretation unavailable" }));
  return { el, value: () => input.checked && on };
}

export async function withBusy(btn, fn) {
  const old = [...btn.childNodes];
  btn.disabled = true;
  clear(btn).append(h("span", { class: "spinner" }), " Working…");
  try { return await fn(); }
  catch (e) { toast(e?.detail || e?.message || "Request failed", "error", 8000); return null; }
  finally { clear(btn); append(btn, old); btn.disabled = false; }
}

export const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
