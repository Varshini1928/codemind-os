import { api } from "../api.js";
import { confirmDialog, fmtDate, fmtNum, h, icon, mount, toast } from "../dom.js";
import { avatar, badge, callout, card, emptyState, errorCallout, loading, pageHead, statTile, statusPill, table, tabs } from "../components.js";
import { refreshRepos, state } from "../state.js";

const ACTION_LABEL = { signup: "Sign-up", login: "Sign-in", login_failed: "Failed sign-in", logout: "Sign-out", password_changed: "Password changed", password_reset: "Password reset",
  role_changed: "Role changed", user_approved: "User approved", user_enabled: "User enabled", user_disabled: "User disabled", user_deleted: "User deleted",
  repo_registered: "Repository added", repo_removed: "Repository removed", admin_seeded: "Admin seeded", profile_updated: "Profile updated", user_set_pending: "Set to pending" };

function showSecret(title, secret) {
  const done = () => overlay.remove();
  const overlay = h("div", { class: "overlay" }, h("div", { class: "modal", role: "dialog", "aria-modal": "true" }, h("h3", { text: title }),
    h("p", { class: "muted", text: "Copy this now — it is shown only once. The user must choose a new password at their next sign-in." }),
    h("pre", { class: "code-block", text: secret }),
    h("div", { class: "modal-actions" }, h("button", { class: "btn", onclick: async () => { try { await navigator.clipboard.writeText(secret); toast("Copied", "success"); } catch { toast("Copy failed — select the text manually", "error"); } } }, "Copy"),
      h("button", { class: "btn btn-primary", onclick: done }, "Done"))));
  document.body.append(overlay);
}

export default async function admin(root) {
  mount(root, pageHead("Administration"), loading(6));
  let stats, users, repos, audit;
  try { [stats, users, repos, audit] = await Promise.all([api.adminStats(), api.adminUsers(), api.adminRepos(), api.adminAudit(100)]); }
  catch (e) { mount(root, pageHead("Administration"), errorCallout(e)); return; }
  const me = state.user;
  let q = "", fStatus = "", fRole = "";
  const reload = () => admin(root);
  const act = async (fn, okMsg) => { try { await fn(); toast(okMsg, "success"); reload(); } catch (e) { toast(e.detail || e.message, "error", 7000); } };

  const usersView = () => {
    const box = h("div");
    const draw = () => {
      const list = users.filter((u) => (!fStatus || u.status === fStatus) && (!fRole || u.role === fRole) && (!q || (u.email + u.display_name).toLowerCase().includes(q.toLowerCase())));
      mount(box, table(["User", "Role", "Status", "Repos", "Sessions", "Last sign-in", "Actions"], list.map((u) => {
        const self = u.id === me.id;
        const btn = (label, fn, cls = "btn btn-sm") => h("button", { class: cls, onclick: fn }, label);
        return [h("div", { class: "row" }, avatar(u, 32), h("div", {}, h("strong", { text: u.display_name }), self ? badge("you", "primary") : null, h("div", { class: "muted small", text: u.email }))),
          badge(u.role, u.role === "admin" ? "primary" : ""), statusPill(u.status), String(u.repositories), String(u.sessions), fmtDate(u.last_login_at),
          h("div", { class: "row-wrap" }, self ? h("span", { class: "muted small", text: "—" }) : [
            u.status === "pending" ? btn("Approve", () => act(() => api.adminUpdate(u.id, { status: "active" }), "User approved"), "btn btn-sm btn-primary") : null,
            u.status === "active" ? btn("Disable", async () => { if (await confirmDialog("Disable user", `${u.email} will be signed out and unable to sign in.`, "Disable", true)) act(() => api.adminUpdate(u.id, { status: "disabled" }), "User disabled"); }) : null,
            u.status === "disabled" ? btn("Enable", () => act(() => api.adminUpdate(u.id, { status: "active" }), "User enabled")) : null,
            btn(u.role === "admin" ? "Make user" : "Make admin", async () => { if (await confirmDialog("Change role", u.role === "admin" ? `Remove administrator rights from ${u.email}?` : `Give ${u.email} full administrator access?`, "Change role")) act(() => api.adminUpdate(u.id, { role: u.role === "admin" ? "user" : "admin" }), "Role updated"); }),
            btn("Reset password", async () => { if (await confirmDialog("Reset password", `Issue a temporary password for ${u.email}? Their sessions end immediately.`, "Reset")) { try { const r = await api.adminReset(u.id); showSecret(`Temporary password for ${u.email}`, r.temporary_password); reload(); } catch (e) { toast(e.detail || e.message, "error"); } } }),
            btn(icon("trash", 14), async () => { if (await confirmDialog("Delete user", `Permanently delete ${u.email} and all of their repositories, indexes and reports?`, "Delete user", true)) act(() => api.adminDelete(u.id), "User deleted"); }, "btn btn-sm btn-ghost")])];
      }), { empty: "No users match the filters." }));
    };
    const search = h("input", { class: "input", style: { width: "240px" }, placeholder: "Search name or email…", "aria-label": "Search users", oninput: (e) => { q = e.target.value; draw(); } });
    const st = h("select", { class: "select", style: { width: "150px" }, "aria-label": "Status filter", onchange: (e) => { fStatus = e.target.value; draw(); } }, [["", "All statuses"], ["active", "Active"], ["pending", "Pending"], ["disabled", "Disabled"]].map(([v, t]) => h("option", { value: v, text: t, selected: v === fStatus ? "" : null })));
    const ro = h("select", { class: "select", style: { width: "140px" }, "aria-label": "Role filter", onchange: (e) => { fRole = e.target.value; draw(); } }, [["", "All roles"], ["admin", "Admins"], ["user", "Users"]].map(([v, t]) => h("option", { value: v, text: t })));
    draw();
    return h("div", {}, h("div", { class: "row-wrap", style: { "margin-bottom": "12px" } }, search, st, ro), box);
  };

  const pending = stats.users.pending;
  const overview = () => h("div", { class: "stack" },
    pending ? callout("warn", h("strong", { text: `${pending} account(s) waiting for approval. ` }), h("a", { href: "#/admin", onclick: (e) => { e.preventDefault(); fStatus = "pending"; show(1); } }, "Review them")) : null,
    h("div", { class: "grid grid-6" }, statTile(stats.users.total, "users"), statTile(stats.users.active, "active"), statTile(stats.users.pending, "pending"), statTile(stats.users.admins, "administrators"),
      statTile(stats.repositories.total, "repositories"), statTile(fmtNum(stats.repositories.chunks), "indexed chunks")),
    h("div", { class: "grid grid-2" },
      card("Server", h("dl", { class: "kv" }, h("dt", { text: "Mode" }), h("dd", { text: stats.hosted ? "Hosted" : "Local" }), h("dt", { text: "Sign-ups" }), h("dd", { text: stats.signup_mode }),
        h("dt", { text: "Storage used" }), h("dd", { text: `${(stats.storage_bytes / 1048576).toFixed(1)} MB` }), h("dt", { text: "Saved reports" }), h("dd", { text: String(stats.reports) }),
        h("dt", { text: "Jobs" }), h("dd", { text: Object.entries(stats.jobs).map(([k, v]) => `${k}: ${v}`).join(", ") || "none yet" }))),
      card("Policies", h("div", { class: "stack" }, h("p", { class: "muted", text: "Set by environment variables on the server (CODEMIND_SIGNUP_MODE=open|approval|closed, CODEMIND_MAX_REPOS_PER_USER, CODEMIND_ALLOWED_GIT_HOSTS)." }),
        h("p", { class: "muted", text: "Users only ever see their own repositories, jobs and reports. Administrators can inspect everything from this page." })))));

  const reposView = () => table(["Repository", "Owner", "Status", "Files", "Chunks", "Indexed", ""], repos.map((r) => [h("div", {}, h("strong", { text: r.name }), h("div", { class: "muted small mono", text: r.git_url || r.root })),
    r.owner_email ? r.owner_email : badge("unowned", "warn"), statusPill(r.status), String(r.file_count ?? 0), String(r.chunk_count ?? 0), fmtDate(r.indexed_at),
    h("button", { class: "btn btn-sm btn-ghost", title: "Remove repository", onclick: async () => { if (await confirmDialog("Remove repository", `Remove ${r.name} and its index and reports?`, "Remove", true)) act(() => api.unregister(r.id), "Repository removed"); } }, icon("trash", 14))]), { empty: "No repositories yet." });

  const auditView = () => table(["When", "Actor", "Event", "Target", "Detail"], audit.map((a) => [fmtDate(a.ts), a.actor_email || "system", badge(ACTION_LABEL[a.action] || a.action, /fail|delet|disabl/.test(a.action) ? "warn" : ""), a.target || "", a.detail || ""]), { empty: "No audit events yet." });

  let tabHost;
  const defs = [{ label: "Overview", render: overview }, { label: "Users", count: users.length, render: usersView }, { label: "Repositories", count: repos.length, render: reposView }, { label: "Audit log", render: auditView }];
  const show = (i) => { tabHost = tabs(defs, i); mount(holder, tabHost); };
  const holder = h("div");
  mount(root, pageHead("Administration", "Manage users, approvals, repositories and review security-relevant activity.", h("button", { class: "btn", onclick: reload }, icon("refresh", 16), "Refresh")), holder);
  show(pending ? 1 : 0);
  if (pending) fStatus = "pending";
}
