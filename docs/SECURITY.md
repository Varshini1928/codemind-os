# Security and privacy

## Threat model
The analysed repository is **untrusted**. CodeMind OS must not execute it, be steered by it, or leak it.

| Control | Implementation |
|---|---|
| Read-only, no execution | Indexing only reads bytes and parses with `ast`/tree-sitter/regex. No hooks, build scripts or installers are run. Git is called with fixed argument lists, `core.hooksPath=/dev/null`, `core.fsmonitor=false`, 20 s timeout and a scrubbed environment. |
| Path safety | `resolve_inside` rejects absolute paths, `..`, NUL bytes and symlink escapes; repository paths reject `/`, system dirs, your home dir and any repo containing the storage dir. |
| Symlinks | Never followed during the walk; recorded as excluded (`symlink`). |
| Secrets | Excluded by filename (`.env*` except examples, `*.pem`, `id_rsa*`, …) and by content (private-key blocks, AWS/GitHub/Slack/Google/Stripe tokens, high-entropy key assignments). Password-like assignments are *not* excluded (so they can be reported) but are redacted before logging/prompting. **Secret scanning is imperfect; it cannot guarantee every credential is found.** |
| Prompt injection | Evidence is wrapped in `<evidence>` tags, closing tags neutralised, secrets redacted, and the system prompt declares it untrusted data. The model has no tools: no shell, filesystem-write or network access. LLM output only becomes claims that are then verified. |
| Local only | Ollama host must be loopback (override needs `CODEMIND_ALLOW_REMOTE_OLLAMA=true`); HTTP client ignores proxy variables; embeddings run locally; Chroma/HF/DNT telemetry disabled. Model weights can be downloaded only if you opt in (`CODEMIND_ALLOW_MODEL_DOWNLOAD` or the explicit script). |
| Writes | Reports/indexes live in the storage dir. Writing into a repository needs `CODEMIND_ALLOW_REPO_WRITES=true` **and** `approved=true`, never overwrites, stays inside the root. |
| Test execution | Needs `CODEMIND_ALLOW_TEST_EXECUTION=true` **and** `approved=true`. Runs in a temp copy with scrubbed env, timeout, CPU/memory/file-size limits (POSIX). **This is not a security sandbox**: code can still use the network and read files your user can read. Use a VM/container for untrusted repositories. |
| Cloning | Disabled until `CODEMIND_CLONE_WORKSPACE` is set; https/ssh URLs only, no credentials in URLs, destination must be a new folder in the workspace, hooks/file/ext protocols disabled. |
| API | Binds to 127.0.0.1; refuses other hosts unless `CODEMIND_ALLOW_NETWORK_BIND=true` (the API has **no authentication** – do not expose it). Request size limit, validated schemas, generic 500 messages, redacting log filter. |
| Deletion | `DELETE /repositories/{id}/index`, `DELETE /repositories/{id}`, report deletion; the dashboard asks for confirmation. |

## Accounts and access control
* **Passwords**: PBKDF2-HMAC-SHA256 (600,000 iterations, random 16-byte salt, constant-time compare); minimum 10 characters, blocks common/all-numeric passwords and the email name; dummy hash verification for unknown emails equalises timing; generic "incorrect email or password" for unknown users and wrong passwords.
* **Brute-force controls**: account lockout (5 failures → 15 minutes) plus per-IP rate limits on login (20/min) and sign-up (5/hour). Lockout and approval messages appear only after the correct password, except for the lockout notice itself.
* **Sessions**: random 256-bit tokens in an `HttpOnly`, `SameSite=Strict` cookie (`Secure` automatically over HTTPS); only the SHA-256 of the token is stored server-side; 7-day absolute and 24-hour idle expiry; password change and admin actions revoke sessions.
* **CSRF**: besides `SameSite=Strict`, every state-changing API call must send `X-Requested-With: codemind`.
* **Roles**: `user` and `admin`. `/api/admin/*` and changing server settings are admin-only (enforced in one central rule set, `backend/authz.py`, unit-tested). Users cannot change their own role/status, and the last active administrator cannot be demoted, disabled or deleted.
* **Isolation**: every repository has an owner. Repository, job and report routes return *404* (not 403) for other people's data, so existence does not leak. Unowned legacy repositories are admin-only until the first administrator claims them. Local-folder indexing is administrator-only (and disabled when hosted).
* **Sign-up policy**: `open`, `approval` (default when hosted; new accounts stay *pending*) or `closed`. On a **local** install the first account becomes the administrator; when **hosted**, nobody can claim admin by visiting first - the administrator is seeded from `CODEMIND_ADMIN_EMAIL`/`CODEMIND_ADMIN_PASSWORD` and the server refuses to start without one.
* **Audit log**: sign-ups, sign-ins, failed attempts, approvals, role/status changes, password changes/resets, deletions, repository add/remove.
* **Known gaps**: no email verification, no self-service password reset (admin-issued temporary passwords instead), no multi-factor authentication, rate limits are in-memory (per process), usernames are emails, and the account-exists message on sign-up reveals registered emails.

## Hosted mode (`CODEMIND_HOSTED=true`, e.g. Render)
Hosting changes the privacy model: code is cloned to and stored on **your server** (see `docs/DEPLOY_RENDER.md`). Controls: accounts forced on; startup refuses without an administrator; `/api/docs` disabled; local-path registration disabled; repository writes and test execution forced off regardless of environment; clone limited to https URLs on an allow-list of hosts (default github.com, gitlab.com, bitbucket.org), size-capped, per-user repository quota, and removed when the repository is unregistered; strict headers (`Content-Security-Policy` allowing only same-origin scripts/styles, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, `Cache-Control: no-store` for API responses). The web UI renders all repository-derived text through DOM text nodes (never `innerHTML`) and loads no external scripts, fonts or images. TLS comes from the hosting platform.

## Where data is stored
`CODEMIND_STORAGE_DIR` (default `~/.codemind_os`): `codemind.db` (metadata, chunks incl. source text, parse cache, jobs), `vectors/`, `graphs/`, `reports/`, `review/`, `logs/`, `models/`. Delete the folder to remove everything. The dashboard's Settings page shows these paths.

## Known limits
Pattern-based secret detection (false negatives/positives); the file-size limit and exclusions are heuristic; chunks of non-excluded files are stored in plaintext in SQLite; the in-process dashboard has no user authentication; the optional test runner is not isolated from the network.
