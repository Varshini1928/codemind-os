// App shell: auth gate, sidebar, top bar, hash router. Pages are loaded lazily.
import { ApiError, api, onAuthRequired } from "./api.js";
import { renderLogin, renderSignup } from "./auth.js";
import { avatar, errorCallout, loading, statusPill } from "./components.js";
import { clear, h, icon, mount, toast } from "./dom.js";
import { currentRepo, onChange, refreshReady, refreshRepos, runCleanups, setRepo, state } from "./state.js";

const ROUTES = [
  { id: "overview", label: "Overview", icon: "home", group: "Workspace", load: () => import("./pages/overview.js") },
  { id: "repos", label: "Repositories", icon: "folder", group: "Workspace", load: () => import("./pages/repos.js") },
  { id: "explorer", label: "Explorer", icon: "file", group: "Workspace", load: () => import("./pages/explorer.js") },
  { id: "graph", label: "Knowledge graph", icon: "graph", group: "Workspace", load: () => import("./pages/graph.js") },
  { id: "ask", label: "Ask", icon: "ask", group: "Analyze", load: () => import("./pages/ask.js") },
  { id: "quality", label: "Code quality", icon: "shield", group: "Analyze", load: () => import("./pages/quality.js") },
  { id: "impact", label: "Change impact", icon: "zap", group: "Analyze", load: () => import("./pages/impact.js") },
  { id: "tests", label: "Test planner", icon: "tests", group: "Analyze", load: () => import("./pages/tests.js") },
  { id: "docs", label: "Documentation", icon: "book", group: "Analyze", load: () => import("./pages/docs.js") },
  { id: "reports", label: "Reports", icon: "file", group: "Library", load: () => import("./pages/reports.js") },
  { id: "settings", label: "Settings & privacy", icon: "sliders", group: "Library", load: () => import("./pages/settings.js") },
  { id: "account", label: "My account", icon: "lock", group: "Account", load: () => import("./pages/account.js") },
  { id: "admin", label: "Administration", icon: "shield", group: "Account", admin: true, load: () => import("./pages/admin.js") },
];
const visibleRoutes = () => ROUTES.filter((r) => !r.admin || state.user?.role === "admin");
const app = document.getElementById("app");
let els = {};

function applyTheme(t) {
  const saved = t || localStorage.getItem("codemind.theme") || (window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.setAttribute("data-theme", saved);
  localStorage.setItem("codemind.theme", saved);
  return saved;
}

function buildShell() {
  const nav = [];
  let group = "";
  for (const r of visibleRoutes()) {
    if (r.group !== group) { group = r.group; nav.push(h("div", { class: "nav-group", text: group })); }
    nav.push(h("a", { class: "nav-item", href: `#/${r.id}`, "data-route": r.id }, icon(r.icon), r.label));
  }
  els.repoSelect = h("select", { class: "select", style: { "max-width": "260px" }, "aria-label": "Repository", onchange: (e) => { setRepo(e.target.value); navigate(); } });
  els.pills = h("div", { class: "row-wrap" });
  els.theme = h("button", { class: "icon-btn", "aria-label": "Toggle theme", title: "Toggle light/dark", onclick: () => { applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark"); drawTheme(); } });
  els.content = h("div", { class: "content", id: "content" });
  els.userChip = state.user && state.session.auth_mode === "accounts"
    ? h("a", { class: "user-chip", href: "#/account", title: "My account" }, avatar(state.user, 30), h("span", { class: "user-name" }, state.user.display_name),
      state.user.role === "admin" ? h("span", { class: "badge b-primary", text: "admin" }) : null) : null;
  const shell = h("div", { class: "shell", id: "shell" },
    h("aside", { class: "sidebar", "aria-label": "Primary" },
      h("div", { class: "brand" }, h("div", { class: "brand-mark" }, icon("graph", 18)), h("div", {}, "CodeMind OS", h("small", { text: "Repository intelligence" }))),
      h("nav", {}, nav),
      h("div", { class: "sidebar-foot" }, h("div", { text: `v${state.session.version} · ${state.session.hosted ? "hosted" : "local"} mode` }),
        h("div", { text: state.session.hosted ? "Code is processed on this server." : "Your code never leaves this machine." }))),
    h("div", { class: "main" },
      h("header", { class: "topbar" },
        h("button", { class: "icon-btn menu-btn", "aria-label": "Menu", onclick: () => document.getElementById("shell").classList.toggle("nav-open") }, icon("menu")),
        els.repoSelect, els.pills, h("div", { class: "spacer" }), els.theme, els.userChip,
        state.session.auth_mode === "accounts" ? h("button", { class: "icon-btn", title: "Sign out", "aria-label": "Sign out", onclick: signOut }, icon("logout")) : null),
      h("main", { id: "main", tabindex: "-1" }, els.content)));
  mount(app, shell);
  drawTheme();
  drawTop();
}

function drawTheme() { mount(els.theme, icon(document.documentElement.getAttribute("data-theme") === "dark" ? "sun" : "moon")); }

function drawTop() {
  if (!els.repoSelect) return;
  mount(els.repoSelect, state.repos.length ? state.repos.map((r) => h("option", { value: r.id, selected: r.id === state.repoId ? "" : null, text: r.name })) : [h("option", { value: "", text: "No repository" })]);
  els.repoSelect.value = state.repoId;
  const r = currentRepo(), llm = state.ready?.llm, aiOn = llm?.available && llm?.model_installed;
  mount(els.pills, r ? statusPill(r.status) : null,
    h("span", { class: `badge ${aiOn ? "b-success" : "b-warn"}`, title: aiOn ? `Local model ${llm.model}` : (llm?.message || "No local AI model") }, h("i", { class: "dot" }), aiOn ? "AI online" : "AI offline"));
}

let navToken = 0;
async function navigate() {
  const id = (location.hash.replace(/^#\/?/, "") || "overview").split("?")[0];
  let route = visibleRoutes().find((r) => r.id === id) || ROUTES[0];
  if (state.user?.must_change_password && route.id !== "account") { route = ROUTES.find((r) => r.id === "account"); if (location.hash !== "#/account") location.hash = "#/account"; }
  const mine = ++navToken;
  runCleanups();
  document.querySelectorAll?.(".nav-item").forEach((a) => a.classList.toggle("active", a.getAttribute("data-route") === route.id));
  document.getElementById("shell")?.classList.remove("nav-open");
  document.title = `${route.label} · CodeMind OS`;
  mount(els.content, loading(5));
  try {
    const mod = await route.load();
    if (mine !== navToken) return;
    const root = h("div");
    mount(els.content, root);
    await mod.default(root);
  } catch (e) {
    if (mine === navToken) mount(els.content, errorCallout(e));
  }
}

async function signOut() {
  try { await api.logout(); } catch { /* cookie may already be gone */ }
  state.user = null; state.repos = []; state.repoId = ""; state.chats = {};
  location.hash = "#/login";
  location.reload();
}

function showAuth() {
  const view = location.hash.startsWith("#/signup") ? "signup" : "login";
  const go = (v) => { location.hash = `#/${v}`; showAuth(); };
  const done = async () => { location.hash = "#/overview"; await boot(); };
  (view === "signup" ? renderSignup : renderLogin)(app, state.session, { goLogin: () => go("login"), goSignup: () => go("signup"), onSuccess: done });
}

async function boot() {
  applyTheme();
  try { state.session = await api.session(); }
  catch (e) { mount(app, h("div", { class: "boot" }, errorCallout(e))); return; }
  state.user = state.session.user;
  if (state.session.auth_mode === "accounts" && !state.session.authenticated) { showAuth(); return; }
  if (/^#\/(login|signup)/.test(location.hash)) location.hash = "#/overview";
  buildShell();
  onChange(drawTop);
  try { await Promise.all([refreshRepos(), refreshReady()]); } catch (e) { if (e instanceof ApiError && e.status === 401) return; toast(e.message, "error"); }
  await navigate();
}

onAuthRequired(() => { if (state.session?.auth_mode === "accounts") { state.session.authenticated = false; state.user = null; location.hash = "#/login"; showAuth(); toast("Your session has ended. Please sign in again.", "info"); } });
window.addEventListener("hashchange", () => { if (!state.user && state.session?.auth_mode === "accounts") showAuth(); else navigate(); });
boot();
