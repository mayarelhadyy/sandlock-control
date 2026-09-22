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
