# SandLock backend and Owner dashboard

SQLite is authoritative; Excel is the existing reporting projection. The User application is hosted separately on Vercel. No data migration or database initialization is required for this integration update.

## Railway production

1. Deploy this folder with Python dependencies from `requirements.txt`. `railway.json` starts `python -B server.py`, using Waitress (four threads, one process). Remove an old conflicting Railway start-command override or set it to the same command. Do not use `flask run` or multiple application replicas with the current in-process MQTT/recovery workers.
2. Set `SANDLOCK_RESERVATION_ORIGINS=https://sandlock-app.vercel.app` (exact origin, no trailing slash); `SANDLOCK_LOCAL_HTTP=0`; `SANDLOCK_COOKIE_PARTITIONED=1`; `SANDLOCK_TRUST_PROXY=1`; `SANDLOCK_HTTP_HOST=0.0.0.0`; `SANDLOCK_INITIALIZE_STORAGE=0`; `SANDLOCK_RESERVATION_WORKER=1`.
3. Railway supplies `PORT`; it overrides `SANDLOCK_HTTP_PORT`. Retain the existing persistent volume, `SANDLOCK_RESERVATION_DB` and `SANDLOCK_WORKBOOK` paths and existing storage markers. Do not replace a live workbook with the bundled blank template. Do not delete/recreate a database or enable initialization to bypass a storage error.
4. Retain existing authorized private MQTT settings and `SANDLOCK_MQTT_ENABLED` deliberately. No broker credentials were changed or tested. Keep them server-side. `.env.example` is documentation, not an automatically loaded environment file.
5. Proxy trust must be enabled only behind the Railway edge: the application trusts one forwarded host/protocol hop. Do not expose this listener directly to untrusted traffic with proxy trust enabled. Keep one service replica; rolling overlap/multi-replica device coordination is outside this update.

Owner sign-in: https://sandlock-control-production.up.railway.app/static/login.html. Owner dashboard: `/` after signing in. Existing Admin accounts are preserved; no production account/default password was created. `SANDLOCK_USER_APP` is optional and not needed for the separate Vercel deployment.

User cookies have a new `__Host-sandlock_user_session_v2` name with Secure/HttpOnly/SameSite=None/Partitioned attributes and seven-day expiry. Users sign in again once; reservations/accounts remain intact. Admin cookies remain separate, first-party, eight-hour maximum with the existing inactivity policy. CSRF tokens, session revocation, ownership checks and sensitive no-store responses are retained. CORS is allowed only on User routes for the configured origin, including relevant error responses; Admin APIs are not opened cross-origin.

## Local regression tests (no production services)

Install `requirements.txt` in your normal development Python environment. Test scripts initialize **fresh synthetic databases only** under `tests/.runtime`; they never use configured production paths and force MQTT/recovery workers off. They may read `.test-deps` when locally installed there.

```powershell
python -B -m unittest discover -s tests -p test_*.py -v
node --test tests/frontend.test.cjs
```

For browser tests, install Playwright in your test environment (`npm install --no-save playwright`, or configure NODE_PATH to an existing installation). The default browser is Windows Edge; set `SANDLOCK_BROWSER` to another installed Chromium executable if needed. Generate a temporary self-signed test certificate using OpenSSL (not a real credential):

```powershell
New-Item -ItemType Directory -Force tests/.runtime
openssl req -x509 -newkey rsa:2048 -nodes -keyout tests/.runtime/key.pem -out tests/.runtime/cert.pem -days 1 -subj /CN=localhost
python -B tests/serve_browser.py
# In a second terminal:
node tests/browser.cjs
```

The launcher binds Waitress only to 127.0.0.1:8795; the browser harness binds its local HTTPS proxy only to 127.0.0.1:8796. It uses app.frontend.test and api.backend.test with Chromium host overrides, fresh Owner/User test accounts and a synthetic workbook. TLS verification is relaxed **in the test browser only** for this self-signed certificate. Test ports must be free. Stop the launcher after testing. The generated test certificates, accounts, databases, screenshots and dependency directories are not deployment inputs and are ignored.

## Redeployment verification

Deploy backend first, verify its build installed Waitress and it binds Railway PORT without Flask's development-server warning, then deploy the frontend. Refresh existing tabs normally so the new worker/assets take over. If an older installed PWA cannot update, close all app tabs and reopen; clearing only that site's old cache/service worker is a troubleshooting fallback (never delete backend storage).

Verify registration/login, session after refresh, credentialed locker/reservation requests, CSRF rejection, successful permitted mutation, logout and Owner login. Inspect browser Network headers without sharing session-cookie/token values. Real Vercel redirects/headers, Railway edge behavior and supported mobile browsers still require post-deployment verification. See `SANDLOCK_PRODUCTION_INTEGRATION_REPORT.md` for local evidence and limitations.


## Locker notifications / Web Push

Notification history is private and backend-persisted even with push disabled. `notifications.py` adds four tables and one index to the **existing** SQLite database transactionally at startup: `notifications`, `locker_notification_state`, `push_subscriptions`, `notification_deliveries`. Existing accounts, sessions, bookings, finance and workbook contents are not replaced. Keep `SANDLOCK_INITIALIZE_STORAGE=0` on established storage. No manual migration command is required.

Generate one VAPID key pair locally after installing requirements:

```powershell
python manage_push_keys.py --contact mailto:YOUR_MONITORED_EMAIL_ADDRESS
```

Replace the placeholder with your real monitored email address, for example `mailto:operator@your-domain.example`. This creates **.env.vapid** without printing keys or overwriting an existing file. Keep it private; it is ignored and must never be uploaded to GitHub/Vercel. Copy these four values into Railway's private variable settings:

- `SANDLOCK_PUSH_ENABLED=1`
- `SANDLOCK_VAPID_PUBLIC_KEY` = generated public key
- `SANDLOCK_VAPID_PRIVATE_KEY` = generated private key
- `SANDLOCK_VAPID_SUBJECT` = your `mailto:` contact

The file is NOT automatically loaded. Keep the key pair stable across deployments. Invalid/missing matching keys with push enabled fail startup explicitly. The default is `SANDLOCK_PUSH_ENABLED=0`: history works, external delivery is disabled. No Vercel environment or routing change is required; the public key comes through the authenticated backend API. Never expose the private key in frontend configuration.

Deploy the backend with the existing volume and normal single-process Waitress command, then the frontend. Users choose **Notifications → Enable Notifications** explicitly. Existing notification permission alone does not register a push subscription. The new worker release is `sandlock-notifications-v1` with `notifications-1` assets. Existing in-page reservation reminders remain in place; booking no longer triggers a permission prompt.

The current MQTT input remains `sandlock/locker/A/door`; only explicit open/closed reports are used. The backend selects the started, non-terminal reservation for that locker and resolves its authenticated owner. It never uses a recipient/locker ID from the payload. Repeated identical door states and retained reports do not create duplicate alerts; open → closed → open creates distinct events. A first closed report establishes a baseline. Without device event IDs/verified timestamps, missed or out-of-order transitions and exact physical-event time cannot be certified; displayed dates/times are UTC backend receipt times converted to the viewing device's timezone.

Push is handled by a separate retry worker, never in the MQTT callback. Jobs survive restart, use a 120-second claim lease, up to eight attempts with exponential delays, an eight-second HTTP timeout and 24-hour event/provider TTL. 404/410 deactivates subscriptions. History remains when delivery fails. Provider acceptance is not proof of OS display. The sender permits HTTPS Google/Mozilla/Apple/Microsoft push endpoints only, refuses redirects and omits sensitive exception bodies from logs.

Subscriptions belong to the authenticated account and current revocable User session. Signing out disables that session's subscriptions; disabling alerts affects this device. New sends stop when the session expires (existing seven-day User session policy). Sign in and enable again when necessary. An opaque worker binding prevents a new account from displaying queued old-account alerts; it is not an authentication token. Browser history is never persisted locally. Already-seen OS notifications cannot be retroactively unseen; in-flight provider delivery cannot be recalled, so the worker also checks its current binding.

Real push delivery still needs validation with your VAPID configuration and target devices. iOS/iPadOS Web Push requires a supported Home Screen installation and user gesture; Android/desktop delivery depends on browser/OS permission and background policies. Do not treat these alerts as a guaranteed physical safety alarm. No physical firmware/protocol change was made.

Additional tests:

```powershell
python -B -m unittest discover -s tests -p test_*.py -v
node --test tests/frontend.test.cjs tests/notifications-worker.test.cjs
# Start tests/serve_browser.py using the earlier local TLS setup, then:
node tests/browser.cjs
```

All harnesses force real MQTT and Web Push off. Push transport, permission/subscription cases and door observations are simulated where needed. `tests/inject_notification.py` accepts only the fresh browser test paths under `tests/.runtime`; it cannot use the configured production store. See `SANDLOCK_NOTIFICATIONS_REPORT.md` for classifications and remaining verification.
