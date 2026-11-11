import { api } from "./api.js";

export const state = {
  session: null, repos: [], repoId: localStorage.getItem("codemind.repo") || "", ready: null,
  chats: {}, cleanups: [], listeners: new Set(),
};

export const onChange = (fn) => { state.listeners.add(fn); return () => state.listeners.delete(fn); };
const emit = () => state.listeners.forEach((f) => f());

export function currentRepo() { return state.repos.find((r) => r.id === state.repoId) || null; }

export function setRepo(id) {
  state.repoId = id || "";
  localStorage.setItem("codemind.repo", state.repoId);
  emit();
}

export async function refreshRepos() {
  state.repos = await api.repos();
  if (!currentRepo() && state.repos.length) state.repoId = state.repos[0].id;
  if (!state.repos.length) state.repoId = "";
  emit();
  return state.repos;
}

export async function refreshReady() {
  try { state.ready = await api.ready(); } catch { state.ready = null; }
  emit();
  return state.ready;
}

export function addCleanup(fn) { state.cleanups.push(fn); }
export function runCleanups() { state.cleanups.splice(0).forEach((f) => { try { f(); } catch { /* ignore */ } }); }
