# Deploying CodeMind OS on Render

## What a hosted deployment is (and is not)
CodeMind OS is *local-first*. On Render it becomes a **hosted instance you own**: the web UI and API run on Render's servers, and repositories are **cloned onto that server** (public HTTPS Git URLs only) and indexed there. Consequences you should accept before deploying:

* Code you add is processed and stored on Render's infrastructure (until you delete it or the free instance's disk is wiped). Only add code you are allowed to process there.
* **There is no local Ollama on Render** (it needs far more RAM than the free tier). The instance runs in *deterministic mode*: retrieval, graph, rule-based review, impact analysis, test planning and docs all work, with citations; the "AI interpretation" layer is off and the UI says so. Pointing `CODEMIND_OLLAMA_HOST` at your own Ollama server (plus `CODEMIND_ALLOW_REMOTE_OLLAMA=true`) is possible, but then evidence is sent to that server.
* Hosted mode is hardened: **user accounts required** (sign-up needs admin approval by default), local-path registration disabled, repository writes and test execution forced off, clone hosts allow-listed (`github.com`, `gitlab.com`, `bitbucket.org`), clone size capped, API docs disabled, strict CSP headers.
* **Per-user isolation:** every user sees only their own repositories, jobs and reports; administrators can see and manage everything from the Admin page. Quotas (`CODEMIND_MAX_REPOS_PER_USER`) limit abuse.

## Steps
1. **Push the project to GitHub** (private repo recommended):
   ```bash
   git init && git add . && git commit -m "CodeMind OS"
   git branch -M main && git remote add origin https://github.com/<you>/codemind-os.git && git push -u origin main
   ```
   (`.gitignore` already excludes `.env`, databases and runtime data.)
2. **Render dashboard → New → Blueprint** → connect the repository → Render reads `render.yaml` → *Apply*. (Alternatively *New → Web Service → Docker*, then add the environment variables from `render.yaml` by hand.)
3. When Render asks, enter **`CODEMIND_ADMIN_EMAIL`** (your login email). Render generates `CODEMIND_ADMIN_PASSWORD` for you.
4. Wait for the Docker build (a few minutes). Health check: `/api/health`.
5. **Sign in as administrator**: open `https://<service>.onrender.com`, sign in with `CODEMIND_ADMIN_EMAIL` and the generated `CODEMIND_ADMIN_PASSWORD` (service → *Environment*). Then open **My account → Change password** and set your own. The environment password is only used to create the account the first time and is never reset afterwards.
6. **Repositories → Git URL**: paste a public repository URL (e.g. `https://github.com/pallets/click`), *Add repository*, then *Index*. Start with a small repository on the free plan.

## Free-plan limits to expect
| Limit | Effect |
|---|---|
| 512 MB RAM | Large repositories can run out of memory; `CODEMIND_MAX_FILES`/`MAX_FILE_BYTES`/`MAX_CLONE_MB` in `render.yaml` keep indexing small. |
| Ephemeral disk | Repositories, indexes and reports disappear on each deploy/restart; re-add and re-index. Add a **persistent disk** (paid) and set `CODEMIND_STORAGE_DIR=/var/data/codemind` plus `CODEMIND_CLONE_WORKSPACE=/var/data/workspace` to keep them. |
| Sleeps after 15 min idle | The first request after sleep takes ~30–60 s; running jobs are lost when it sleeps (they are marked failed on restart). |
| Single process | Background indexing runs in-process; keep it to one instance. |

## Operating notes
* **Users**: people open the site and click *Create an account*; with the default `approval` mode their account stays *pending* until you approve it on **Administration → Users** (a banner shows pending accounts). You can also disable users, change roles, reset passwords (a temporary password is shown once) and delete users (their repositories and reports are removed). There is no email sending, so password resets go through an administrator.
* **Audit log**: Administration → Audit log lists sign-ups, sign-ins, failed attempts, approvals, role changes and deletions.
* **Locked out?** Set `CODEMIND_ADMIN_EMAIL` to an existing admin's address and redeploy: that account is re-activated and promoted (its password is not changed). To get a new password, create a second admin first.
* **Private repositories** are not supported on the hosted instance (no credentials are accepted, by design).
* **Updating a repository**: remove it and add it again (the clone is re-fetched); there is no `git pull` button.
* **Custom settings**: any `CODEMIND_*` variable from `.env.example` can be set in Render's Environment tab (`CODEMIND_ALLOWED_GIT_HOSTS=github.com` to restrict further).
* **Logs**: Render dashboard → Logs. Secrets are redacted by the logging filter.

## Not verified yet
This configuration was written but **not deployed or built by the author** (no Docker/Render access in the authoring environment). If the build or start fails, send the Render log excerpt.
