# Security model and hosted-deployment requirements

PathGuard runs locally by default. This page describes what protects data today and what must be added **before** PathGuard is hosted anywhere other people can reach.

## What is in place locally

| Area | Current behaviour |
|---|---|
| Identity | A seeded **demo identity**, used only when `PATHGUARD_DEMO_IDENTITY_ENABLED=true`. Every request acts as `usr_demo0001` (Demo Planner). Tests can pick another *seeded demo* user with the `X-PathGuard-Demo-User` header. Non-demo users can never be selected this way. |
| Public demo (visitor identity) | With `PATHGUARD_VISITOR_IDENTITY_ENABLED=true`, each browser gets its own anonymous account in an HMAC-signed, HttpOnly, SameSite=Lax cookie. The cookie is Secure in production. A tampered cookie gets a fresh account, and the demo header is ignored. Visitors only reach their own projects through the normal membership checks. Projects untouched for the retention period are deleted. There are 20 projects per browser, and request bodies over 2 MB are rejected. This identifies a browser; it does not authenticate a person. See [hosting.md](hosting.md). |
| Production guard | Startup fails if `PATHGUARD_ENV=production` while demo identity is enabled. With demo identity off, every API request returns 401 until real authentication is added. |
| Access control | Every project route checks membership. Non-members get 404, so project IDs don't leak. Members get 403 for actions their role doesn't allow. Roles: owner (everything), editor (edit, publish versions), reviewer (read, review), viewer (read). |
| Concurrent edits | Draft and graph writes carry the `revision` they were based on. A stale revision returns 409 `revision_conflict` and nothing is written. |
| Transactions | Each write request runs in one database transaction. A failure partway through rolls back every record it touched. |
| Immutable versions | A database trigger rejects `UPDATE` on `scenario_versions`. Each version also stores a SHA-256 hash of its contents, which is checked on every read. |
| Audit trail | `audit_events` is append-only: a trigger rejects `UPDATE` and `DELETE`. It records project creation and deletion, version publication, member changes, evidence and evidence-link changes, estimate changes and approvals, and accept/reject review decisions. |
| Network exposure | Compose binds Postgres (26432), the API (8600) and the web app (3600) to `127.0.0.1` only. |
| External processing | `PATHGUARD_EXTERNAL_PROCESSING_ENABLED=false` by default. Each project also has its own `external_processing_opt_in` flag (default false). Both must be true before any project data may leave the machine (relevant only to the optional AI step). |
| Secrets | The Compose database password `pathguard` is a local demo value. Don't reuse it anywhere else. |

## Required before any hosted deployment

These are requirements, not implemented features.

### Transport security (TLS)
- Terminate TLS 1.2+ (prefer 1.3) at a reverse proxy or load balancer, and redirect all HTTP to HTTPS.
- Send `Strict-Transport-Security`, and set `PATHGUARD_CORS_ORIGINS` to the real HTTPS origin only.
- Encrypt the API-to-Postgres connection (`sslmode=verify-full` in the database URL, using the provider's CA).

### Production authentication
- Set `PATHGUARD_ENV=production` and `PATHGUARD_DEMO_IDENTITY_ENABLED=false`. The demo header must not be honoured. The config already refuses the unsafe combination.
- Add an OpenID Connect (OIDC) provider (for example a self-hosted Keycloak, to keep cost at $0). Validate the token's signature, issuer, audience and expiry in `app/auth.py:current_user`, and map the token's subject to a `users` row.
- Use short-lived access tokens, kept in HTTP-only, Secure, SameSite cookies or held in memory. Add CSRF protection if cookies are used.
- Rate-limit authentication and write endpoints.

### Database encryption and hardening
- Turn on encryption at rest: managed-provider storage encryption, or full-disk encryption (LUKS/BitLocker) for a self-hosted volume. Encrypt backups too.
- Run the app as a least-privilege role that can't `TRUNCATE` or alter `audit_events` and `scenario_versions`, and can't disable triggers. Run migrations with a separate owner role.
- Use a strong, rotated database password from a secret store, not from `compose.yaml`.
- Keep Postgres off the public internet (private network or allow-list).

### Operations
- Ship `audit_events` to write-once storage if a tamper-evident history is required beyond database triggers.
- Back up regularly and test restores.
