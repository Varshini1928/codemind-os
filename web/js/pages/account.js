import { api } from "../api.js";
import { fmtDate, h, icon, mount, toast, confirmDialog } from "../dom.js";
import { avatar, badge, callout, card, errorCallout, field, loading, pageHead, statTile, statusPill, table } from "../components.js";
import { policyList } from "../auth.js";
import { refreshRepos, state } from "../state.js";
import { withBusy } from "./shared.js";

const ACTION = { signup: "Account created", login: "Signed in", login_failed: "Failed sign-in attempt", password_changed: "Password changed", profile_updated: "Profile updated",
  repo_registered: "Repository added", repo_removed: "Repository removed", password_reset: "Password reset by an administrator" };

export default async function account(root) {
  mount(root, pageHead("Account"), loading(5));
  let me;
  try { me = await api.me(); } catch (e) { mount(root, pageHead("Account"), errorCallout(e)); return; }
  const u = me.user;
  state.user = u;

  const name = h("input", { class: "input", value: u.display_name, maxlength: "60", "aria-label": "Display name" });
  const saveName = h("button", { class: "btn btn-primary btn-sm" }, "Save profile");
  saveName.onclick = () => withBusy(saveName, async () => { const r = await api.updateMe(name.value.trim()); state.user = r.user; toast("Profile updated", "success"); account(root); });

  const cur = h("input", { class: "input", type: "password", autocomplete: "current-password", "aria-label": "Current password" });
  const nw = h("input", { class: "input", type: "password", autocomplete: "new-password", "aria-label": "New password" });
  const nw2 = h("input", { class: "input", type: "password", autocomplete: "new-password", "aria-label": "Confirm new password" });
  const rules = policyList(() => nw.value, () => u.email);
  nw.addEventListener("input", rules.update);
  const savePw = h("button", { class: "btn btn-primary btn-sm" }, "Change password");
  savePw.onclick = () => withBusy(savePw, async () => {
    if (nw.value !== nw2.value) throw new Error("The new passwords do not match.");
    await api.changePassword(cur.value, nw.value);
    toast("Password changed. Other devices were signed out.", "success"); cur.value = nw.value = nw2.value = "";
    state.user = { ...state.user, must_change_password: false }; account(root);
  });
  const revoke = h("button", { class: "btn btn-sm" }, icon("logout", 14), "Sign out other devices");
  revoke.onclick = () => withBusy(revoke, async () => { const r = await api.revokeOthers(); toast(`Signed out ${r.revoked} other session(s)`, "success"); account(root); });

  mount(root,
    pageHead("Account", "Your profile, security and usage."),
    u.must_change_password ? callout("warn", h("strong", { text: "Choose a new password. " }), "An administrator issued you a temporary password. Set your own before continuing.") : null,
    h("div", { class: "card row", style: { gap: "16px", "margin-bottom": "16px" } }, avatar(u, 56),
      h("div", {}, h("h2", { text: u.display_name, style: { margin: "0" } }), h("div", { class: "muted", text: u.email })),
      h("div", { class: "right row-wrap" }, badge(u.role, u.role === "admin" ? "primary" : ""), statusPill(u.status), badge(`member since ${fmtDate(u.created_at).split(",")[0]}`))),
    h("div", { class: "grid grid-4", style: { "margin-bottom": "16px" } }, statTile(me.stats.repositories, "repositories"), statTile(me.stats.ready, "indexed & ready"), statTile(me.stats.reports, "saved reports"), statTile(me.sessions, "active sessions")),
    h("div", { class: "grid grid-2" },
      card("Profile", h("div", { class: "stack" }, field("Display name", name), field("Email", h("input", { class: "input", value: u.email, disabled: "" }), "Your email is your sign-in name and cannot be changed here."), h("div", {}, saveName))),
      card("Change password", h("div", { class: "stack" }, field("Current password", cur), field("New password", nw), rules.el, field("Confirm new password", nw2), h("div", {}, savePw))),
      card("Sessions", h("div", { class: "stack" }, h("p", { class: "muted", text: `You are signed in on ${me.sessions} device(s). Sessions expire after inactivity.` }), h("div", {}, revoke))),
      card("Your repositories", me.repositories.length ? table(["Name", "Status", "Indexed"], me.repositories.map((r) => [h("a", { href: "#/overview", onclick: () => { localStorage.setItem("codemind.repo", r.id); }, text: r.name }), statusPill(r.status), fmtDate(r.indexed_at)]))
        : h("p", { class: "muted", text: "You have not added a repository yet." }))),
    card("Recent activity", me.activity.length ? table(["When", "Event", "Detail"], me.activity.map((a) => [fmtDate(a.ts), ACTION[a.action] || a.action, a.detail || a.target || ""])) : h("p", { class: "muted", text: "No activity recorded yet." })));
}
