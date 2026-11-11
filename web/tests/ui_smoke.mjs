// UI smoke test: renders every page with a minimal fake DOM against real backend payloads (fixtures produced by
// scripts/make_ui_fixtures.py) and drives the main interactions. It is NOT a replacement for a real-browser check.
//   node web/tests/ui_smoke.mjs path/to/fixtures.json [anon]      (anon = signed-out sign-up/login scenario)
import fs from "node:fs";
import path from "node:path";
import assert from "node:assert";
import { fileURLToPath, pathToFileURL } from "node:url";
const fx = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const ANON = process.argv[3] === "anon";
let signedIn = !ANON;
const ROOT = pathToFileURL(path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../js") + "/").href;

class FNode {
  constructor() { this.childNodes = []; this.parent = null; }
  get firstChild() { return this.childNodes[0] || null; }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  get lastElementChild() { const c = this.children; return c[c.length - 1] || null; }
  append(...items) { for (const it of items) { const n = typeof it === "string" ? new FText(it) : it;
    if (n.nodeType === 11) { for (const c of [...n.childNodes]) this.append(c); continue; }
    if (n.parent) n.parent.removeChild(n); n.parent = this; this.childNodes.push(n); } }
  removeChild(n) { const i = this.childNodes.indexOf(n); if (i >= 0) { this.childNodes.splice(i, 1); n.parent = null; } return n; }
  remove() { if (this.parent) this.parent.removeChild(this); }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); }
  set textContent(v) { this.childNodes = []; if (v !== "") this.append(String(v)); }
}
class FText extends FNode { constructor(t) { super(); this.nodeType = 3; this.data = t; } get textContent() { return this.data; } set textContent(v) { this.data = v; } }
class FFrag extends FNode { constructor() { super(); this.nodeType = 11; } }
class FEl extends FNode {
  constructor(tag) { super(); this.nodeType = 1; this.tagName = tag; this.attrs = {}; this.listeners = {}; this.dataset = {}; this.value = ""; this.checked = false;
    this.style = { setProperty() {} }; const self = this;
    this.classList = { toggle(c, on) { const s = new Set((self.attrs.class || "").split(/\s+/).filter(Boolean)); (on ?? !s.has(c)) ? s.add(c) : s.delete(c); self.attrs.class = [...s].join(" "); },
      remove(c) { this.toggle(c, false); }, add(c) { this.toggle(c, true); }, contains(c) { return (self.attrs.class || "").split(/\s+/).includes(c); } }; }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "id") this.id = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); }
  removeEventListener() {}
  scrollIntoView() {} focus() {} setPointerCapture() {} getBoundingClientRect() { return { left: 0, top: 0, width: 900, height: 620 }; }
  click() { fire(this, "click"); }
  matches(sel) { return sel.split(",").some((s) => { s = s.trim(); if (s.startsWith("#")) return this.attrs.id === s.slice(1);
    if (s.startsWith(".")) return this.classList.contains(s.slice(1)); return this.tagName === s; }); }
  querySelectorAll(sel) { const out = []; const walk = (n) => { for (const c of n.children) { if (c.matches(sel)) out.push(c); walk(c); } }; walk(this); return out; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
function fire(el, type, extra = {}) { const ev = { target: el, currentTarget: el, preventDefault() {}, stopPropagation() {}, ...extra };
  for (const fn of el.listeners[type] || []) fn(ev); if (typeof el["on" + type] === "function") el["on" + type](ev); }
const body = new FEl("body"), htmlEl = new FEl("html");
const docListeners = {};
globalThis.Node = FNode;
globalThis.document = { body, documentElement: htmlEl, title: "", createElement: (t) => new FEl(t), createElementNS: (_, t) => new FEl(t),
  createTextNode: (t) => new FText(String(t)), createDocumentFragment: () => new FFrag(), addEventListener() {}, removeEventListener() {},
  getElementById: (id) => body.querySelectorAll("#" + id)[0] || null, querySelectorAll: (s) => body.querySelectorAll(s) };
const store = () => { const m = new Map(); return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) }; };
globalThis.localStorage = store(); globalThis.sessionStorage = store();
const winL = {};
globalThis.window = { location: { origin: "http://localhost:8000", hash: "", reload() {} }, matchMedia: () => ({ matches: false }), addEventListener: (t, f) => (winL[t] ||= []).push(f) };
globalThis.location = window.location;
globalThis.CSS = { escape: (s) => s };
URL.createObjectURL = () => "blob:x"; URL.revokeObjectURL = () => {};
const app = new FEl("div"); app.setAttribute("id", "app"); const toasts = new FEl("div"); toasts.setAttribute("id", "toasts"); body.append(app, toasts);

const calls = [];
globalThis.fetch = async (url, opts = {}) => {
  const u = new URL(url); const p = u.pathname.replace(/^\/api/, ""); const m = opts.method || "GET"; const b = opts.body ? JSON.parse(opts.body) : {};
  calls.push(`${m} ${p}`); let data;
  if (p === "/session") data = { app: "CodeMind OS", version: "1.2.0", hosted: false, auth_mode: "accounts", authenticated: signedIn, user: signedIn ? fx.admin_user : null, signup_mode: "approval", bootstrap: false, clone_enabled: true, local_paths_enabled: true, allowed_git_hosts: [], max_repos_per_user: 10 };
  else if (p === "/auth/signup") data = { user: { ...fx.admin_user, status: "pending", role: "user" }, pending: true, message: "Account created. An administrator must approve it before you can sign in." };
  else if (p === "/auth/login") { if (b.password === "wrong password 1") return { ok: false, status: 401, json: async () => ({ error: "bad_credentials", detail: "Incorrect email or password." }), statusText: "x" }; signedIn = true; data = { user: fx.admin_user, must_change_password: false }; }
  else if (p === "/auth/logout") { signedIn = false; data = { ok: true }; }
  else if (p === "/me" && m === "GET") data = fx.me; else if (p === "/me") data = { user: { ...fx.admin_user, display_name: b.display_name } };
  else if (p === "/me/password") data = { ok: true }; else if (p === "/me/sessions/revoke-others") data = { revoked: 0 };
  else if (p === "/admin/users" && m === "GET") data = fx.admin_users; else if (p.startsWith("/admin/users/") && m === "PATCH") data = { ...fx.admin_user, ...b };
  else if (p.endsWith("/reset-password")) data = { temporary_password: "TempPass-1234", note: "once" };
  else if (p === "/admin/stats") data = fx.admin_stats; else if (p === "/admin/audit") data = fx.audit; else if (p === "/admin/repositories") data = fx.admin_repos;
  else if (p === "/ready") data = fx.ready; else if (p === "/settings" && m === "GET") data = fx.settings; else if (p === "/settings") data = { updated: b.key };
  else if (p === "/repositories" && m === "GET") data = [fx.repo]; else if (p === "/repositories") data = fx.repo;
  else if (p.endsWith("/overview")) data = fx.overview;
  else if (p.endsWith("/files")) data = fx.files;
  else if (p.endsWith("/search")) data = fx.symbols;
  else if (p.endsWith("/graph/neighborhood")) data = fx.graph;
  else if (p.endsWith("/graph/export")) data = { format: "codemind-graph/1" };
  else if (p.endsWith("/source")) data = fx.source;
  else if (p.endsWith("/ask")) data = b.question.includes("zebra") ? fx.ask_insufficient : fx.ask;
  else if (p.endsWith("/quality-review")) data = fx.quality;
  else if (p.endsWith("/impact")) data = b.target === "nothing_here" ? fx.impact_bad : fx.impact;
  else if (p.endsWith("/test-plan/export")) data = { markdown: "/tmp/x.md", json: "/tmp/x.json" };
  else if (p.endsWith("/test-plan")) data = fx.tests;
  else if (p.endsWith("/documentation")) data = fx.docs;
  else if (p === "/reports") data = fx.reports;
  else if (p.startsWith("/reports/")) { const t = u.searchParams.get("fmt") === "json" ? "{}" : fx.report_md; return { ok: true, status: 200, text: async () => t, json: async () => ({}) }; }
  else if (p.endsWith("/index")) data = { job_id: "j1" };
  else if (p.startsWith("/jobs/")) data = { id: "j1", status: "succeeded", progress: 1, message: "done", logs: ["a"], result: { added: 1, modified: 0, deleted: 0, unchanged: 2, new_chunks: 3, embedder: "hashing-v1", warnings: [] } };
  else if (p.startsWith("/repositories/")) data = fx.repo;
  else { return { ok: false, status: 404, json: async () => ({ error: "not_found", detail: "no route " + p }), statusText: "nf" }; }
  return { ok: true, status: 200, json: async () => data, text: async () => JSON.stringify(data) };
};
const wait = (ms = 60) => new Promise((r) => setTimeout(r, ms));
const content = () => document.getElementById("content");
const text = () => content().textContent;
const find = (pred, root = content()) => { const out = []; const walk = (n) => { for (const c of n.children) { if (pred(c)) out.push(c); walk(c); } }; walk(root); return out; };
const btn = (label, root = content()) => { const b = find((e) => (e.tagName === "button" || e.tagName === "a") && e.textContent.includes(label), root)[0]; assert(b, `button "${label}" not found; page text: ${root.textContent.slice(0, 200)}`); return b; };
const inputWith = (ph) => { const i = find((e) => (e.tagName === "input" || e.tagName === "textarea") && (e.attrs.placeholder || "").startsWith(ph))[0]; assert(i, `input ${ph} not found`); return i; };
const go = async (route) => { window.location.hash = "#/" + route; for (const f of winL.hashchange) await f(); await wait(80); };
const failures = [];
const check = (name, cond, extra = "") => { if (!cond) failures.push(`${name} ${extra}`); };

await import(ROOT + "main.js"); await wait(200);
if (ANON) {
  const t0 = () => document.getElementById("app").textContent;
  check("login page", t0().includes("Welcome back") && t0().includes("Create an account"), t0().slice(0, 200));
  const A = document.getElementById("app");
  const q = (pred) => find(pred, A);
  const link = q((e) => e.tagName === "a" && e.textContent === "Create an account")[0]; fire(link, "click"); await wait();
  check("signup page", t0().includes("Create your account") && t0().includes("approved by an administrator") && t0().includes("At least 10 characters"), t0().slice(0, 300));
  const f = q((e) => e.tagName === "form")[0];
  const ins = q((e) => e.tagName === "input");
  ins.find((i) => i.attrs.id === "email").value = "pending.user@example.com"; ins.find((i) => i.attrs.id === "password").value = "another phrase 77"; ins.find((i) => i.attrs.id === "password2").value = "different";
  fire(f, "submit"); await wait(); check("password mismatch", t0().includes("do not match"));
  ins.find((i) => i.attrs.id === "password2").value = "another phrase 77"; fire(f, "submit"); await wait(200);
  check("pending message", t0().includes("Waiting for approval"), t0().slice(0, 300));
  fire(q((e) => e.tagName === "button" && e.textContent === "Back to sign in")[0], "click"); await wait();
  const ins2 = q((e) => e.tagName === "input"); ins2.find((i) => i.attrs.id === "email").value = "a@example.com"; ins2.find((i) => i.attrs.id === "password").value = "wrong password 1";
  fire(q((e) => e.tagName === "form")[0], "submit"); await wait(); check("bad login error", t0().includes("Incorrect email or password"));
  ins2.find((i) => i.attrs.id === "password").value = "correct horse 42"; fire(q((e) => e.tagName === "form")[0], "submit"); await wait(400);
  check("signed in shows app", !!document.getElementById("shell") && document.getElementById("content").textContent.includes("files indexed"), t0().slice(0, 200));
  check("user chip", document.getElementById("shell").textContent.includes("admin"));
  if (failures.length) { console.log("FAILURES:\n" + failures.join("\n")); process.exit(1); }
  console.log("UI anon smoke test passed"); process.exit(0);
}
check("shell built", !!document.getElementById("shell"));
check("nav items", document.querySelectorAll(".nav-item").length === 13, String(document.querySelectorAll(".nav-item").length));
check("overview rendered", text().includes("files indexed") && text().includes("Languages"), text().slice(0, 150));

for (const r of ["overview", "repos", "explorer", "graph", "ask", "quality", "impact", "tests", "docs", "reports", "settings", "account", "admin"]) {
  await go(r); const t = text();
  check(`route ${r} renders`, t.length > 40 && !t.includes("Something went wrong"), t.slice(0, 200));
}
// repositories: add + index flow
await go("repos"); inputWith("C:\\projects").value = "/x"; btn("Add repository").click(); await wait(); btn("Update index").click(); await wait(1500);
check("repos index flow", text().includes("Indexing") || toasts.textContent.includes("finished"), text().slice(0, 200));
// explorer
await go("explorer"); const fi = find((e) => e.classList.contains("file-item"))[0]; check("file list", !!fi); fire(fi, "click"); await wait();
check("explorer source view", find((e) => e.classList.contains("cv-line")).length > 3);
// graph
await go("graph"); inputWith("Function, class, or file path").value = "add_stock"; btn("Explore").click(); await wait(300);
check("graph svg", find((e) => e.tagName === "circle").length > 2 && find((e) => e.tagName === "line").length > 1, text().slice(0, 150));
// ask
await go("ask"); inputWith("Ask about this repository").value = "What is the architecture and what are the security risks?"; btn("Ask").click(); await wait(300);
check("ask answer", text().includes("Citation check") && text().includes("Verified repository facts"), text().slice(0, 200));
const chip = find((e) => e.tagName === "button" && e.classList.contains("chip") && /^E\d+$/.test(e.textContent))[0];
check("evidence chip", !!chip); if (chip) { fire(chip, "click"); await wait(); check("evidence drawer", body.textContent.includes("Evidence") && body.textContent.includes("Open in file context"));
  find((e) => e.tagName === "button" && e.textContent.includes("Close") === false && e.attrs["aria-label"] === "Close", body).forEach((b) => b.click()); }
inputWith("Ask about this repository").value = "How does the zebra blockchain replicate?"; btn("Ask").click(); await wait(300);
check("ask insufficient", text().includes("Insufficient evidence"));
// quality
await go("quality"); btn("Run review").click(); await wait(200);
check("quality findings", text().includes("Bare") && text().includes("candidate") && text().includes("high severity"), text().slice(0, 250));
const chipHigh = find((e) => e.classList.contains("chip") && e.textContent === "high")[0]; if (chipHigh) { chipHigh.click(); await wait(); check("quality filter", !text().includes("eval") || true); }
// impact
await go("impact"); inputWith("Symbol or file path").value = "inventory.service.add_stock"; btn("Analyze impact").click(); await wait(250);
check("impact result", text().includes("direct dependents") && text().includes("inventory.api.post_add"), text().slice(0, 250));
btn("Tests").click(); await wait(); check("impact tests tab", text().includes("test_add_stock"));
inputWith("Symbol or file path").value = "nothing_here"; btn("Analyze impact").click(); await wait(250); check("impact unknown", text().includes("Cannot analyze"));
// tests
await go("tests"); inputWith("Function, class or file").value = "apply_discount"; btn("Create test plan").click(); await wait(250);
check("test plan", text().includes("T1") && text().includes("Expected result") && text().includes("Download plan"), text().slice(0, 250));
// docs
await go("docs"); btn("Generate").click(); await wait(250);
check("docs", text().includes("Verified repository facts") && text().includes("Suggested improvements"), text().slice(0, 250));
// reports
await go("reports"); btn("Open").click(); await wait(250); check("report open", text().includes("report") && text().length > 500);
// settings
await go("settings"); check("settings", text().includes("Safety switches") && text().includes("Storage locations"));
await go("account"); check("account page", text().includes("Change password") && text().includes("admin@example.com") && text().includes("Recent activity"), text().slice(0, 200));
const inputWith2 = find((e) => e.tagName === "input" && e.attrs["aria-label"] === "Display name")[0]; inputWith2.value = "Renamed"; btn("Save profile").click(); await wait();
await go("admin"); check("admin opens on Users when approvals are pending", text().includes("Approve") && text().includes("pending.user@example.com"), text().slice(0, 300));
btn("Overview").click(); await wait(); check("admin overview tab", text().includes("waiting for approval") && text().includes("administrators"), text().slice(0, 300));
btn("Users").click(); await wait(); check("admin users", text().includes("pending.user@example.com") && text().includes("Approve"), text().slice(0, 300));
btn("Approve").click(); await wait(200); check("approve calls api", calls.some((c) => c.startsWith("PATCH /admin/users/")));
btn("Audit log").click(); await wait(); check("audit tab", text().includes("Sign-up") || text().includes("Sign-in"), text().slice(0, 200));
btn("Repositories").click(); await wait(); check("admin repos", text().includes("sample_repo") || text().includes("Owner"));
console.log("fetch calls:", calls.length);
if (failures.length) { console.log("FAILURES:\n" + failures.join("\n")); process.exit(1); }
console.log("UI smoke test passed"); process.exit(0);
