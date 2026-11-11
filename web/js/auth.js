// Sign-in and sign-up views (shown before the app shell when accounts are enabled).
import { api } from "./api.js";
import { callout, errorCallout } from "./components.js";
import { h, icon, mount } from "./dom.js";

const HERO = [["shield", "Evidence before automation", "Every answer cites the exact file and lines it relies on, and says so when evidence is missing."],
  ["lock", "Private by design", "Repositories are indexed read-only. Code is never executed or sent to third-party AI services."],
  ["graph", "Understand impact", "Dependency graph, change-impact reports, test plans and documentation from real code facts."]];

function layout(title, subtitle, ...body) {
  return h("div", { class: "auth" },
    h("aside", { class: "auth-hero" }, h("div", { class: "brand" }, h("div", { class: "brand-mark" }, icon("graph", 18)), h("div", {}, "CodeMind OS", h("small", { text: "Repository intelligence" }))),
      h("h2", { text: "Know your codebase before you change it." }),
      HERO.map(([ic, t, d]) => h("div", { class: "hero-point" }, icon(ic, 20), h("div", {}, h("strong", { text: t }), h("p", { text: d }))))),
    h("main", { class: "auth-main", id: "main" }, h("div", { class: "auth-card" }, h("h1", { text: title }), h("p", { class: "muted", text: subtitle }), ...body)));
}

function passwordField(id, label, autocomplete) {
  const input = h("input", { class: "input", id, type: "password", autocomplete, required: "", "aria-label": label });
  const toggle = h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => { input.type = input.type === "password" ? "text" : "password"; toggle.textContent = input.type === "password" ? "Show" : "Hide"; } }, "Show");
  return { input, el: h("div", { class: "field" }, h("label", { for: id, text: label }), h("div", { class: "row" }, input, toggle)) };
}

export const policy = (pw, email = "") => {
  const local = (email.split("@")[0] || "").toLowerCase();
  return [["At least 10 characters", pw.length >= 10], ["Not a common or all-numeric password", pw.length > 0 && !/^\d+$/.test(pw) && new Set(pw).size >= 4 && !["password123", "1234567890", "qwertyuiop", "letmein123"].includes(pw.toLowerCase())],
    ["Does not contain your email name", !(local.length >= 4 && pw.toLowerCase().includes(local))]];
};

export function policyList(getPw, getEmail = () => "") {
  const box = h("ul", { class: "checklist" });
  const draw = () => mount(box, policy(getPw(), getEmail()).map(([t, ok]) => h("li", { class: ok ? "ok" : "" }, icon(ok ? "check" : "x", 14), t)));
  draw();
  return { el: box, update: draw };
}

export function renderLogin(root, session, { goSignup, onSuccess }) {
  const email = h("input", { class: "input", id: "email", type: "email", autocomplete: "username", required: "", placeholder: "you@example.com" });
  const pw = passwordField("password", "Password", "current-password");
  const msg = h("div"), submit = h("button", { class: "btn btn-primary", type: "submit" }, "Sign in");
  const form = h("form", { class: "stack", novalidate: "" },
    h("div", { class: "field" }, h("label", { for: "email", text: "Email" }), email), pw.el, msg, submit,
    session.signup_mode !== "closed" || session.bootstrap ? h("p", { class: "muted small" }, session.bootstrap ? "No accounts exist yet. " : "New here? ",
      h("a", { href: "#/signup", onclick: (e) => { e.preventDefault(); goSignup(); }, text: session.bootstrap ? "Create the administrator account" : "Create an account" })) : h("p", { class: "muted small", text: "Sign-ups are closed. Ask an administrator for an account." }),
    h("p", { class: "muted small", text: "Forgot your password? An administrator can issue a temporary one." }));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!email.value.trim() || !pw.input.value) { mount(msg, errorCallout({ detail: "Enter your email and password." })); return; }
    submit.disabled = true; mount(msg);
    try { await api.login(email.value.trim(), pw.input.value); await onSuccess(); }
    catch (err) { mount(msg, errorCallout(err)); submit.disabled = false; pw.input.value = ""; pw.input.focus(); }
  });
  mount(root, layout("Welcome back", "Sign in to your CodeMind OS workspace.", form));
  email.focus();
}

export function renderSignup(root, session, { goLogin, onSuccess }) {
  const name = h("input", { class: "input", id: "name", autocomplete: "name", maxlength: "60", placeholder: "Ada Lovelace" });
  const email = h("input", { class: "input", id: "email", type: "email", autocomplete: "username", required: "", placeholder: "you@example.com" });
  const pw = passwordField("password", "Password", "new-password"), pw2 = passwordField("password2", "Confirm password", "new-password");
  const rules = policyList(() => pw.input.value, () => email.value);
  pw.input.addEventListener("input", rules.update); email.addEventListener("input", rules.update);
  const msg = h("div"), submit = h("button", { class: "btn btn-primary", type: "submit" }, session.bootstrap ? "Create administrator account" : "Create account");
  const note = session.bootstrap ? callout("info", h("strong", { text: "First account. " }), "You will be the administrator of this installation.")
    : session.signup_mode === "approval" ? callout("info", "New accounts must be approved by an administrator before you can sign in.") : null;
  const form = h("form", { class: "stack", novalidate: "" }, note, h("div", { class: "field" }, h("label", { for: "name", text: "Display name (optional)" }), name),
    h("div", { class: "field" }, h("label", { for: "email", text: "Email" }), email), pw.el, rules.el, pw2.el, msg, submit,
    h("p", { class: "muted small" }, "Already have an account? ", h("a", { href: "#/login", onclick: (e) => { e.preventDefault(); goLogin(); }, text: "Sign in" })));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (pw.input.value !== pw2.input.value) { mount(msg, errorCallout({ detail: "The two passwords do not match." })); return; }
    submit.disabled = true; mount(msg);
    try {
      const r = await api.signup({ email: email.value.trim(), password: pw.input.value, display_name: name.value.trim() });
      if (!r.pending) { await api.login(email.value.trim(), pw.input.value); await onSuccess(); return; }
      mount(root, layout("Almost there", "Your account was created.", callout("info", h("strong", { text: "Waiting for approval. " }), r.message),
        h("button", { class: "btn btn-primary", onclick: goLogin }, "Back to sign in")));
    } catch (err) { mount(msg, errorCallout(err)); submit.disabled = false; }
  });
  mount(root, layout(session.bootstrap ? "Set up CodeMind OS" : "Create your account", session.bootstrap ? "Create the first (administrator) account." : "Start analysing repositories with evidence-backed answers.", form));
  name.focus();
}
