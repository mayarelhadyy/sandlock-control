# SandLock Safari same-origin authentication report

## Result and root cause

Implemented in the existing `C:\Users\mayar\Downloads\sandlock-app` and `C:\Users\mayar\Downloads\sandlock-control` folders, preserving the notification implementation. Local tests: **71 total — 30 PASS, 41 SIMULATED, 0 FAIL**. Simulated tests executed successfully but are not claims of real provider/device delivery.

**Safari: REAL DEVICE VERIFICATION REQUIRED.** Real Vercel/Railway edge behavior and iPhone authentication/push delivery were not tested or deployed.

The inspected client sent login/session/API requests directly from Vercel to Railway and required a cross-site SameSite=None/Partitioned cookie. The reported real-iPhone cookie rejection is consistent with that architecture; this task did not independently reproduce the failure on an iPhone. The previous privacy-setting workaround has been removed.

Before: browser on Vercel → cross-site Railway requests/cookies.
After: browser → same-origin Vercel User routes → fixed Railway upstream. Cookie creation and subsequent browser requests occur on the frontend host. Owner stays on Railway.

## Exact files changed

Frontend: `backend-client.js`, `config.js`, `auth-client.js`, `vercel.json`, `index.html`, `service-worker.js`, `README.md`.
Backend: `server.py`, `config.py`, `.env.example`, `README.md`.
Tests/evidence: `tests/test_integration.py`, `tests/frontend.test.cjs`, `tests/browser.cjs`, `tests/browser-results.json`.
Report: `SANDLOCK_SAFARI_SAME_ORIGIN_AUTH_REPORT.md`.

No reservation, finance, auth database schema, notification implementation, MQTT protocol, CSS, layout or production storage changes. No Git/deployment/production requests/broker/hardware use. Original D: sources and downloaded ZIPs were untouched. Temporary dependencies and synthetic test storage were removed after testing.

## Exact proxy behavior

All 26 entries below target **only** `https://sandlock-control-production.up.railway.app`; the destination column is its path. Fixed source/destination paths preserve methods, bodies and query strings (including notification pagination). Dynamic identifiers match only the shown single-segment bounded patterns. Backend route methods, authentication, ownership and CSRF checks still apply. No arbitrary destination or catch-all backend route exists.

| Vercel source | Railway destination path |
|---|---|
| `/auth/user/register` | `/auth/user/register` |
| `/auth/user/login` | `/auth/user/login` |
| `/auth/user/session` | `/auth/user/session` |
| `/auth/user/logout` | `/auth/user/logout` |
| `/auth/user/profile` | `/auth/user/profile` |
| `/api/v1/reservations` | `/api/v1/reservations` |
| `/api/v1/financial/quote` | `/api/v1/financial/quote` |
| `/api/v1/lockers` | `/api/v1/lockers` |
| `/api/v1/device/state` | `/api/v1/device/state` |
| `/api/v1/notifications` | `/api/v1/notifications` |
| `/api/v1/push-subscriptions` | `/api/v1/push-subscriptions` |
| `/api/v1/push-subscriptions/remove` | `/api/v1/push-subscriptions/remove` |
| `/api/v1/reservations/:bookingId([A-Za-z0-9_.:-]{1,128})` | `/api/v1/reservations/:bookingId` |
| `/api/v1/reservations/:bookingId([A-Za-z0-9_.:-]{1,128})/pin` | `/api/v1/reservations/:bookingId/pin` |
| `/api/v1/reservations/:bookingId([A-Za-z0-9_.:-]{1,128})/cancel` | `/api/v1/reservations/:bookingId/cancel` |
| `/api/v1/reservations/:bookingId([A-Za-z0-9_.:-]{1,128})/complete` | `/api/v1/reservations/:bookingId/complete` |
| `/api/v1/device/:bookingId([A-Za-z0-9_.:-]{1,128})/set` | `/api/v1/device/:bookingId/set` |
| `/api/v1/device/:bookingId([A-Za-z0-9_.:-]{1,128})/time` | `/api/v1/device/:bookingId/time` |
| `/api/v1/device/:bookingId([A-Za-z0-9_.:-]{1,128})/clear` | `/api/v1/device/:bookingId/clear` |
| `/api/v1/device/:bookingId([A-Za-z0-9_.:-]{1,128})/unlock` | `/api/v1/device/:bookingId/unlock` |
| `/api/v1/device/commands/:requestId([A-Za-z0-9_.:-]{1,128})` | `/api/v1/device/commands/:requestId` |
| `/api/v1/notifications/:notificationId([A-Za-z0-9_-]{1,128})/read` | `/api/v1/notifications/:notificationId/read` |
| `/api/v1/events/payment` | `/api/v1/events/payment` |
| `/api/v1/events/access` | `/api/v1/events/access` |
| `/api/v1/events/feedback` | `/api/v1/events/feedback` |
| `/api/v1/events/user-upsert` | `/api/v1/events/user-upsert` |

Auth routes provide register/login/session/logout/profile. Reservation routes provide list/create/read/PIN/cancel/complete; quote/lockers support booking. Device routes preserve the existing authenticated gateway. Event routes preserve existing app events. Notification and subscription routes preserve history/read/push management.

Not forwarded: `/auth/admin/*`, `/api/overview`, `/api/cmd/unlock`, Owner reservation routes, `/api/v1/legacy-reservations/import`, `/download/*`, backend static files or arbitrary `/api/v1/*` routes. Local proxy checks returned 404 for excluded Admin/export paths. The `/auth/:path*` and `/api/:path*` header rules only disable caching; they are NOT forwarding rules. Legacy `/user` redirects remain.

Vercel external rewrites are the native reverse-proxy mechanism: [official rewrites documentation](https://vercel.com/docs/routing/rewrites), [external reverse proxy guide](https://vercel.com/kb/guide/vercel-reverse-proxy-rewrites-external). Local tests implement this allowlist with an HTTPS proxy; they do not certify deployed Vercel routing/header forwarding.

## Cookie, CSRF and session behavior

User cookie: `__Host-sandlock_user_session_v3`, Secure, HttpOnly, SameSite=Lax, Path=/, no Domain, no Partitioned, existing seven-day maximum. Host-only Set-Cookie received through Vercel belongs to the frontend hostname. No cookie/token is exposed to JavaScript or browser storage. Fetch uses `credentials: same-origin`; a remote backendOrigin setting cannot redirect the transport. Login still verifies a session round trip before entering the signed-in UI.

Owner cookie/policy remains unchanged. Logout still revokes the server session and clears the same first-party cookie. Session generation fencing, account isolation and outage/expiry handling remain. The distinct cookie name avoids accepting old partitioned cookies. Old cookies are ignored and old server sessions expire under the existing policy; no bulk session/data migration is performed. Users sign in again once and re-enable push for the new session.

CSRF and trusted-Origin validation are unchanged. Vercel must forward the original frontend Origin, Cookie and X-CSRF-Token to Railway and return Set-Cookie. The configured frontend origin remains required because upstream Host can be Railway. No arbitrary Origin trust, wildcard credentialed CORS or Admin CORS was added. Backend no-store responses are retained, with explicit Vercel browser/CDN no-store headers for auth/API paths.

## Service worker and notifications

Release `sandlock-same-origin-v1`, asset queries `same-origin-1`. Existing worker auth/API/download bypass already handles same-origin traffic; it remains intact. Version update removes old SandLock caches and installs updated assets without a forced reload. Static/offline recovery remains functional. Push handlers, payload validation, IndexedDB binding, deduplication and safe click routing were preserved.

History, unread/read state and subscriptions now use the same-origin transport automatically. Notification pagination query strings are preserved. VAPID keys remain server-side and unchanged; no new Vercel secret is needed. Existing notification permissions may persist, but users must Enable Notifications again to bind the new authenticated session. Real Web Push OS delivery remains unverified; Safari Web Push requires a supported Home Screen installation, independently of normal Safari login.

## Tests and classifications

Environment: Windows, Python 3.12, Node 24, Edge/Chromium 153; local Waitress + SQLite + synthetic workbook; loopback HTTPS proxy with temporary self-signed certificate. Third-party-cookie blocking enabled in Chromium. TLS validation relaxation applied only to local test browser. MQTT and external push disabled/mocked.

| Suite | Total | PASS | SIMULATED | FAIL |
|---|---:|---:|---:|---:|
| Flask/API/SQLite integration | 14 | 14 | 0 | 0 |
| Backend notification regression | 16 | 0 | 16 | 0 |
| Frontend tests | 15 | 4 | 11 | 0 |
| Notification worker tests | 8 | 0 | 8 | 0 |
| HTTPS browser tests | 18 | 12 | 6 | 0 |
| Total | 71 | 30 | 41 | 0 |

Commands: `python -B -m unittest discover -s tests -p test_*.py -v`; `node --test tests/frontend.test.cjs tests/notifications-worker.test.cjs`; `python -B tests/serve_browser.py` plus `node tests/browser.cjs`.

Backend PASS: registration/login/session cookie and restore, API credentials/CSRF/profile, unauthenticated rejection/error headers, invalid/expired session, logout revocation, trusted/untrusted/invalid-header preflight, untrusted mutation Origin, Owner login/separation, reservation create/retry/complete, cancellation, concurrent booking and Owner cancellation.

Backend notification SIMULATED (N01–N16): owner/time/delivery, duplicate/open-close/restart, legacy routing, cross-user history/read, auth/CSRF/revocation, subscription ownership/remove/logout, expired subscription, provider retry/restart, expired session, retained/unknown input, injected persistence failure isolation, private-key exposure, existing-storage compatibility, concurrent event deduplication, real encryption/VAPID with mocked transport, disabled/invalid configuration. Real Flask/SQLite are used; event/provider dependencies are synthetic, hence conservative SIMULATED classification.

Frontend PASS: remote configuration cannot redirect auth transport, asset consistency, restricted rewrite allowlist, notification pagination URL. Frontend SIMULATED: CSRF transport, malformed backend response, obsolete session response, concurrent 401, prior-account 401, worker dynamic exclusion, uncached offline asset, offline navigation, cache failure/network success, cache+network failure, legacy redirect (VM mocks).

Worker SIMULATED: exact locker/time and private-data exclusion, concurrent duplicate delivery, distinct open/closed events, logout clearing, replacement-account binding, malformed payload, safe click routing, subscription expiry.

| Browser scenario | Result |
|---|---|
| B01 root/assets + stable anonymous UI | PASS |
| B02 same-origin registration/login/cookie/API with third-party cookies blocked | PASS |
| B03 refresh restores cookie session | PASS |
| B04 CSRF create/idempotent retry/complete through browser | PASS |
| N-B01 notification history/unread/read/local timestamp/refresh | SIMULATED |
| B05 real SW install and sensitive cache exclusion | PASS |
| B06 offline shell and uncached asset valid 503 | PASS |
| B07 delayed backend outage preserves session and recovery | SIMULATED |
| B08 concurrent 401 stable sign-out without reload | PASS |
| B09 UI sign-in and logout remains stable/revoked | PASS |
| B10 blocked cookies fail visibly without signed-in UI | SIMULATED |
| B11 legacy /user route redirects to root; desktop/mobile shell | PASS |
| B12 old worker/cache upgrades without reload loop | SIMULATED |
| B13 no browser JS exceptions or wrong-origin API requests | PASS |
| B14 first-party Owner login/dashboard/logout behind TLS proxy | PASS |
| N-B02 unsupported browser/denied permission/no startup prompt | SIMULATED |
| N-B03 separate account cannot see previous history | PASS |
| N-B04 explicit enable/disable/subscription failure and worker binding | SIMULATED |

The first-party browser test verifies Secure/HttpOnly/Lax, no partition key, frontend cookie domain and JavaScript invisibility. Network assertions verify User requests never go directly to the backend test origin. Real Owner login remains on the separate backend test origin. Notification push permission/provider/OS dependencies remain simulated.

## Issues found during implementation

- Initial path validation rejected query strings, which would break notification pagination. Corrected before completion; added and passed an explicit pagination regression.
- Initial proxy-isolation browser check used Playwright's Node request client, which does not inherit Chromium's synthetic DNS mapping (ENOTFOUND). Changed that check to browser fetch; reran all 18 browser scenarios successfully. This was a test harness failure, not an application authentication failure.
- No unresolved local test failures. No UI redesign; existing mobile/desktop shell and notification checks passed at their stated classifications.

## Settings and exact manual deployment order

1. Preserve Railway's existing volume, database/workbook paths, storage markers, accounts and VAPID keys. Keep `SANDLOCK_INITIALIZE_STORAGE=0`, `SANDLOCK_LOCAL_HTTP=0`, `SANDLOCK_TRUST_PROXY=1`, `SANDLOCK_RESERVATION_ORIGINS=https://sandlock-app.vercel.app`, existing Waitress command and worker settings. Do not initialize or replace any data. Remove obsolete `SANDLOCK_COOKIE_PARTITIONED` if present; it is no longer read. No other new Railway variables.
2. Deploy the updated backend folder to Railway manually. Verify startup against the existing storage and that Owner login still works. Do not enable initialization to bypass a storage error.
3. Promptly deploy the updated frontend folder to Vercel, including `vercel.json`. Framework Other, no build command, output project root, no frontend secrets/environment override. The old frontend's cross-site login is incompatible with Lax cookies during this coordinated rollout; plan a brief authentication interruption.
4. Verify the deployed `/auth/user/session` returns unauthenticated JSON rather than a static page, User proxy responses are no-store, and excluded Owner routes are not forwarded. Inspect headers without sharing token values. Verify Origin/CSRF/Cookie/Set-Cookie propagation on the actual edge.
5. Refresh/reopen existing tabs/PWA to load the new worker/assets, sign in again, and explicitly Enable Notifications for this session. Keep VAPID keys stable. Do not change Safari privacy settings.

## Exact real-iPhone acceptance test

**REAL DEVICE VERIFICATION REQUIRED** — keep Prevent Cross-Site Tracking and normal privacy protections enabled.

1. Open `https://sandlock-app.vercel.app/` in Safari after the manual deployments. Confirm current assets/worker; close/reopen old tabs if needed.
2. Register a designated test account or log in. Confirm there is no cross-site-cookie warning and the signed-in dashboard appears. Through Safari remote inspection where available, verify User login/session/API URLs stay on `sandlock-app.vercel.app`, with a frontend-host Secure/HttpOnly/Lax cookie, and no direct Railway User authentication requests. Do not copy/share its value.
3. Refresh, close/reopen Safari and verify session restoration under the existing expiry policy. List/create a permitted test reservation; test permitted cancellation and completion separately. Confirm matching Owner state. These manual mutations should use designated test data only.
4. Log out; verify protected data is inaccessible and prior session is revoked. Log in again, then switch to another test account and confirm previous reservations/PIN/history are absent.
5. Verify missing-CSRF mutation and anonymous protected API requests are rejected; excluded Admin proxy paths remain unavailable. Do not attempt destructive production tests.
6. Check notification history, unread count, mark-read and pagination for the correct account. For Web Push, install to Home Screen on supported iOS, launch it, sign in and explicitly Enable Notifications. Use an authorized test notification source; verify delivered alert/time/click route and disable/remove behavior. Recheck after account switch. Real OS delivery is a separate acceptance gate.
7. Load the shell, go offline, verify safe offline behavior without private API cache, reconnect and restore. Confirm no reload loop. Verify the Owner dashboard separately on Railway.

No deployment or real-iPhone success is claimed by this report.
