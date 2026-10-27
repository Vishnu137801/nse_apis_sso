# Lendrix to NSE FastAPI SSO

This integration authenticates an existing Lendrix customer in the NSE FastAPI service. It does not register NSE accounts or expose NSE business APIs to customer SSO sessions. SSO is disabled unless `SSO_ENABLED=true`.

## Expected Node.js verification contract

The Node.js backend source is not included in this FastAPI repository. The following is the contract FastAPI expects; verify it against the deployed Node.js source/image before relying on or deploying the integration.

FastAPI accepts the 43-character unpadded base64url code format expected from a 32-byte random code and sends:

```http
POST /auth/sso/verify-code
Authorization: Bearer <SSO_SERVICE_SECRET>
Content-Type: application/json

{"code":"<one-time-code>"}
```

The configured FastAPI URL should be:
`https://registration.lendrixventech.com/auth/sso/verify-code`.

Node success response:

```json
{"success":true,"customer":{"lendrix_uuid":"<customer-uuid>"}}
```

FastAPI validates `success` and the nested UUID, then uses only that verified UUID as the customer identity. It does not trust identity fields supplied alongside the code.

The Node verification endpoint is expected to return:

- `400 INVALID_SSO_CODE` for malformed, unknown, expired, consumed, or wrong-audience codes.
- `401` for missing or incorrect service credentials.
- `403 SSO_NOT_ELIGIBLE` if the customer is no longer approved. Redemption consumes the code before this eligibility result.
- `429` when the verification rate limit is reached.
- `503` if the service secret is not configured or a database operation fails.

FastAPI maps invalid codes to 400, ineligible customers to 403, and rate limiting to 429. Bad service credentials and unexpected upstream errors become generic 503 responses. FastAPI does not automatically retry redemption.

The Node code should be bound to `SSO_NSE_AUDIENCE` at issuance and redemption (default `nse-mutual-funds`); no audience is sent in the verify request. Confirm that the deployed Docker image `vk141/registration:1.26.1` has this same contract and audience configuration before rollout; this repository cannot verify the deployed image contents.

## FastAPI endpoints

- `POST /api/v1/auth/sso/exchange`: requires a valid code and an exact allowed `Origin`. On success, issues the customer session cookie and returns safe session metadata, the active mapped client codes, and a session-bound CSRF token.
- `GET /api/v1/auth/sso/me`: validates the cookie and returns the verified Lendrix UUID, current active client-code mappings, and a CSRF token. Mapping changes are read from the database, not trusted from stale JWT claims.
- `POST /api/v1/auth/sso/logout`: requires the session cookie, exact allowed `Origin`, and matching `X-CSRF-Token`. It persists the session revocation before clearing the cookie.

An authenticated customer with no active mapping gets `linking_required: true` and an empty client-code list. Mapping must be provisioned through a separately verified process. The service does not create NSE accounts or link using PAN, email, or mobile.

### Customer-authorized read endpoints

- `GET /api/v1/customer/schemes`: requires the SSO session cookie, but no investor client-code mapping. It returns the live `SCH` scheme-master file as `text/plain`, compatible with the existing React parser. NSE authentication stays server-side; responses are not cached by browsers.
- `GET /api/v1/customer/registration/status`: requires the SSO session cookie and uses the UUID from the verified session. It returns only active verified mappings for that UUID plus the latest customer UCC attempt state. `registration_status` is `registered`, `pending`, `failed`, `unlinked`, `linking_required`, or `unknown`.

These are deliberately narrow customer endpoints. The legacy `/api/v1/reports/master-download` route remains protected by the app bearer token, and the customer-specific `/api/v1/mf/*` router is not mounted. The registration-status endpoint requires the `customer_ucc_attempts` table from migration 002 in addition to the mapping table from migration 001. Apply the documented migrations through the approved database process; FastAPI startup does not create these tables.

## Cookie, CSRF, and CORS

The default `__Host-nse_sso_session` cookie is `Secure`, `HttpOnly`, `Path=/`, `SameSite=Lax`, and has no `Domain`. Keep the SSO frontend and API on HTTPS subdomains of the same site for this cookie policy. The exchange response never includes the session JWT. Responses containing session data use `Cache-Control: no-store`.

The browser must use credentialed requests (`credentials: "include"`), keep the CSRF token in memory, and send it in `X-CSRF-Token` for logout. The authorization code should be short-lived in browser state and removed from the URL promptly. The frontend should bind the callback to the initiating login flow to prevent login CSRF.

`ALLOWED_ORIGINS` controls browser CORS; `SSO_ALLOWED_ORIGINS` is the separate exact-origin allowlist checked by the SSO exchange and logout endpoints. Both must include the actual frontend origin. The production example includes `https://lendrixventech.com` and the existing Mutual Funds subdomain. Add `https://www.lendrixventech.com` only if the frontend is actually served from `www`; do not use wildcard origins.

The configured production allowlists must not contain HTTP localhost origins. For local browser-based SSO, use a separate development backend/configuration and a trusted HTTPS frontend origin (for example `https://localhost:8081` with a locally trusted development certificate), adding that exact origin to both allowlists only in the development environment. If local HTTPS is not available, keep SSO disabled locally. Do not copy development origins into production configuration. The production CORS fallback contains HTTPS production origins only; local origins must be explicit.

An exchange 403 with detail `Origin not allowed` is produced by FastAPI before it calls Node.js. A different exchange 403 (`Lendrix account is not eligible for SSO`) means Node.js rejected the customer, and FastAPI logs that upstream status separately. The exact browser `Origin` and response detail are needed to identify which cause occurred in production; the generic access-denied UI and an HTTP status alone are not sufficient.

## Legacy APIs and release scope

Only the SSO router and narrow customer read router use the customer cookie instead of the legacy bearer dependency. Existing legacy login aliases and NSE business routers retain their existing authentication and request contracts. The previous `/api/v1/mf/*` customer-specific business router is intentionally not mounted; its source remains untouched and its routes return 404.

## Environment variables

FastAPI:

- `SSO_ENABLED` — keep `false` until migrations and integration checks are complete.
- `NSE_BASE_URL`, `NSE_LOGIN_USER_ID`, `NSE_API_SECRET`, `NSE_API_LICENSE_KEY`, `NSE_MEMBER_ID`, and optional `NSE_PROXY` — existing server-side NSE integration settings used by the scheme download.
- `LENDRIX_SSO_VERIFY_URL` — HTTPS Node verification URL.
- `LENDRIX_SSO_SERVER_SECRET` — shared value matching Node's `SSO_NSE_SERVICE_SECRET`; minimum 32 characters.
- `SSO_JWT_SECRET` — separate signing secret, minimum 32 characters, distinct from `JWT_SECRET` and the shared service secret.
- `SSO_SESSION_EXPIRE_MINUTES` — 1–1440; default 60.
- `SSO_COOKIE_NAME` — defaults to `__Host-nse_sso_session`.
- `SSO_ALLOWED_ORIGINS` — required when SSO is enabled; exact HTTPS frontend origins, comma-separated. No origin is implicitly trusted by the setting default.
- `ALLOWED_ORIGINS` — browser CORS allowlist, separate from the SSO origin check; include the frontend origin.

Node.js:

- `SSO_NSE_SERVICE_SECRET` — must match FastAPI's `LENDRIX_SSO_SERVER_SECRET`.
- `SSO_NSE_AUDIENCE` — intended consumer audience; default `nse-mutual-funds`.
- `SSO_CODE_TTL_SECONDS` — optional, 1–300 seconds; default 60.

Do not send any service or JWT signing secret to the frontend. Configure distinct random values in the deployment secret store.

## Database migrations

NSE FastAPI SSO migrations:

1. `migrations/001_add_sso_tables.sql` — creates the UUID-to-client-code mapping table and session-revocation table.
2. `migrations/003_sso_active_mapping_uniqueness.sql` — enforces one active owner per NSE client code. It fails if existing active duplicate mappings prevent the unique index from being created.
3. `migrations/004_add_nse_sso_sessions.sql` — stores customer UUID, session expiry/revocation metadata, and a SHA-256 hash of the JWT `jti`; it never stores the JWT.

Node.js SSO migration:

- `registration_backend/src/migrations/010_sso_authorization_codes.sql` — creates the one-time authorization-code table required by code issue and redemption. The Node migration script requires explicit `--apply`.

SSO exchange commits a session record before setting the cookie. If a valid existing cookie belongs to the identity just verified by Node.js, exchange reuses that session; it does not reuse a session for a different customer. `/me` and SSO-protected customer routes require the record to exist and be unexpired and unrevoked. Each authenticated request reads the session record; the service does not update `last_used_at` per request. Logout marks the session revoked and retains the existing revocation record for compatibility. Existing browser JWTs created before migration 004 have no session record and must authenticate again after the migration and code rollout.

`migrations/002_customer_sso_onboarding.sql` is required only when deploying the customer registration-status/UCC onboarding functionality that uses `customer_ucc_attempts`; it is not required for the SSO session itself.

Before applying anything, take a database backup, inspect actual table/index definitions and migration history, and audit duplicate active client-code mappings. Apply only missing migrations in the relevant database, first in staging, through the approved change process. Do not rely on `IF NOT EXISTS` to detect schema drift. Migration 003 may block writes while PostgreSQL builds the unique index; schedule accordingly. Migration 004 is additive; rollback by disabling SSO and deploying the previous application, and retain the table until session data retention requirements have been reviewed. No migration is run by FastAPI startup, and none was run as part of this work.

## Deployment sequence

1. Confirm the deployed Node image's request/response contract, service secret, audience, and that migration 010 has been applied to the Node database.
2. Back up and inspect the NSE database. Apply missing FastAPI migrations 001, 003, and 004 only after duplicate mapping audit and staging validation; do not drop existing tables during rollback.
3. Configure FastAPI secrets and exact origins with `SSO_ENABLED=false`; deploy the FastAPI image through the normal approved rollout, without changing the Node image.
4. From staging, test code success, invalid/expired/reused code behavior, cookie attributes, `/me`, CSRF rejection, logout/replay rejection, and the existing legacy login.
5. Enable SSO only after those checks pass. Keep `/api/v1/mf/*` unmounted for this release.

Do not deploy this code or enable SSO until the deployed Node image and required database migrations are confirmed.

## Offline verification

SSO tests use mocked HTTP and database calls. The offline FastAPI runner blocks external network access, does not call NSE, and does not execute migrations. Node SSO tests use a temporary in-memory PostgreSQL-compatible database; they do not contact production.
