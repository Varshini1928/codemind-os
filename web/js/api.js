// Thin client for the CodeMind OS API. Authentication uses an HttpOnly session cookie (not readable from JavaScript);
// state-changing requests carry a custom header as CSRF protection.
export class ApiError extends Error {
  constructor(status, kind, detail) { super(detail || kind || `HTTP ${status}`); this.status = status; this.kind = kind; this.detail = detail; }
}

const authListeners = new Set();
export const onAuthRequired = (fn) => authListeners.add(fn);

async function req(method, path, { body, query, text } = {}) {
  const url = new URL(`/api${path}`, window.location.origin);
  for (const [k, v] of Object.entries(query || {})) if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
  const headers = { Accept: text ? "text/plain" : "application/json", "X-Requested-With": "codemind" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(url, { method, credentials: "same-origin", headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, "network", "Cannot reach the CodeMind server. Is it running?");
  }
  if (res.status === 401 && !path.startsWith("/auth/")) { authListeners.forEach((f) => f()); }
  if (!res.ok) {
    let data = {};
    try { data = await res.json(); } catch { /* not JSON */ }
    throw new ApiError(res.status, data.error, data.detail || (Array.isArray(data.detail) ? "Invalid request" : res.statusText));
  }
  if (res.status === 204) return null;
  return text ? res.text() : res.json();
}

const R = (id) => `/repositories/${encodeURIComponent(id)}`;
export const api = {
  session: () => req("GET", "/session"),
  signup: (body) => req("POST", "/auth/signup", { body }),
  login: (email, password) => req("POST", "/auth/login", { body: { email, password } }),
  logout: () => req("POST", "/auth/logout"),
  me: () => req("GET", "/me"),
  updateMe: (display_name) => req("PATCH", "/me", { body: { display_name } }),
  changePassword: (current_password, new_password) => req("POST", "/me/password", { body: { current_password, new_password } }),
  revokeOthers: () => req("POST", "/me/sessions/revoke-others"),
  adminUsers: () => req("GET", "/admin/users"),
  adminUpdate: (id, patch) => req("PATCH", `/admin/users/${encodeURIComponent(id)}`, { body: patch }),
  adminReset: (id) => req("POST", `/admin/users/${encodeURIComponent(id)}/reset-password`),
  adminDelete: (id) => req("DELETE", `/admin/users/${encodeURIComponent(id)}`),
  adminStats: () => req("GET", "/admin/stats"),
  adminAudit: (limit = 100) => req("GET", "/admin/audit", { query: { limit } }),
  adminRepos: () => req("GET", "/admin/repositories"),
  ready: () => req("GET", "/ready"),
  settings: () => req("GET", "/settings"),
  putSetting: (key, value) => req("PUT", "/settings", { body: { key, value } }),
  repos: () => req("GET", "/repositories"),
  repo: (id) => req("GET", R(id)),
  register: (body) => req("POST", "/repositories", { body }),
  index: (id, full = false) => req("POST", `${R(id)}/index`, { body: { full } }),
  job: (id) => req("GET", `/jobs/${encodeURIComponent(id)}`),
  cancelJob: (id) => req("POST", `/jobs/${encodeURIComponent(id)}/cancel`),
  deleteIndex: (id) => req("DELETE", `${R(id)}/index`),
  unregister: (id) => req("DELETE", R(id)),
  overview: (id) => req("GET", `${R(id)}/overview`),
  files: (id, q) => req("GET", `${R(id)}/files`, { query: q }),
  symbols: (id, q, k = 20) => req("GET", `${R(id)}/search`, { query: { q, kind: "symbol", k } }),
  search: (id, q, mode = "hybrid", k = 10) => req("GET", `${R(id)}/search`, { query: { q, mode, k } }),
  neighborhood: (id, q) => req("GET", `${R(id)}/graph/neighborhood`, { query: q }),
  graphExport: (id) => req("GET", `${R(id)}/graph/export`),
  source: (id, path, start = 1, end = 400) => req("GET", `${R(id)}/source`, { query: { path, start, end } }),
  ask: (id, question, use_llm) => req("POST", `${R(id)}/ask`, { body: { question, use_llm } }),
  quality: (id, focus, use_llm) => req("POST", `${R(id)}/quality-review`, { body: { focus, use_llm } }),
  impact: (id, target, change, depth, use_llm) => req("POST", `${R(id)}/impact`, { body: { target, change, depth: depth || null, use_llm } }),
  tests: (id, target, use_llm) => req("POST", `${R(id)}/test-plan`, { body: { target, use_llm } }),
  docs: (id, kind, use_llm) => req("POST", `${R(id)}/documentation`, { body: { kind, use_llm } }),
  exportTests: (id, target) => req("POST", `${R(id)}/test-plan/export`, { body: { target } }),
  reports: (repo_id) => req("GET", "/reports", { query: { repo_id } }),
  report: (repo, rid, fmt = "markdown") => req("GET", `/reports/${encodeURIComponent(repo)}/${encodeURIComponent(rid)}`, { query: { fmt }, text: true }),
  deleteReport: (repo, rid) => req("DELETE", `/reports/${encodeURIComponent(repo)}/${encodeURIComponent(rid)}`),
};
