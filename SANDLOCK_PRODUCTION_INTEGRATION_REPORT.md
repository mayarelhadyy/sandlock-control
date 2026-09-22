# SandLock production integration report

## Completed code changes

Updated **C:/Users/mayar/Downloads/sandlock-app** and **C:/Users/mayar/Downloads/sandlock-control** in place after extracting the supplied ZIPs. No alternative application copies were created. ZIPs, earlier D: source folders, GitHub, production services/data and real broker credentials were not modified. No deployment, Git command, real MQTT connection or physical-device operation was performed.

### Root causes and corrections

| Root cause | Completed correction |
|---|---|
| Auth and reservation clients had separate backend configuration; reservation requests used `credentials: same-origin` across Vercel/Railway. | One public `config.js.backendOrigin`; shared `backend-client.js` builds every User auth/reservation/device API URL, includes credentials and preserves CSRF/no-store. |
| SameSite=None alone cannot guarantee a cookie survives third-party-cookie restrictions. | Secure HttpOnly **Partitioned** User cookie with a new host-only name; login verifies a session round trip before showing authenticated UI. Unsupported/blocked cookies produce a clear stable error. Admin remains separate and first-party. |
| Parallel 401 responses and logout forced `/user/` document replacement; old responses could race a newer account/session. | Single-flight startup restoration, generation/identity fencing, one current-session expiry event, in-place sign-out and private-memory cleanup. Outage recovery preserves the session; no automatic reload loop. |
| CORS/preflight/origin handling was inconsistent and did not expose the response identity header. | Exact allowed User origin, credentialed CORS on relevant errors, validated OPTIONS, `Vary: Origin`, exposed `X-SandLock-User`; CSRF/ownership/revocation remain enforced. Admin APIs are not opened to Vercel. |
| Worker offline asset misses could resolve to undefined; old cache/version/path assumptions survived deployment patches. | Versioned release, cache-failure-safe Response fallbacks, sensitive/cross-origin exclusions, old-cache cleanup, legacy worker retirement, explicit worker `/user/*` redirects and Vercel redirects. No forced controller-change reload. |
| Flask development startup ignored Railway PORT. | Waitress 3.0.2, four threads/one process, Railway start configuration and PORT binding; explicit one-hop proxy trust configuration. |

Removed unused development preview/debug helpers and replaced stale same-origin deployment documentation. No CSS, images, password policy, reservation/financial/storage/device algorithms or MQTT wire contracts were changed. The 29 supplied CSS/image/workbook files compared byte-for-byte equal to the ZIP baselines. Accounts/reservations require **no migration**. The new User cookie name requires one sign-in after redeployment; existing records remain intact.

### Files changed/added

- **User:** `config.js`, `backend-client.js` (new), `auth-client.js`, `reservation-client.js`, `app.js`, `index.html`, `pwa.js`, `service-worker.js`, `vercel.json` (new), `README.md`.
- **Control:** `config.py`, `server.py`, `requirements.txt`, `.env.example`, `.gitignore` (new), `railway.json` (new), `README.md`.
- **Regression harnesses (new):** `tests/test_integration.py`, `tests/frontend.test.cjs`, `tests/serve_browser.py`, `tests/browser.cjs`; this report. No supplied test suite existed in the ZIPs.

## Tests and results

Windows, Python 3.12.3, Node 24.19.0, Playwright 1.62.1, Edge 153.0.4234.48. Browser tests used **different sites** (`app.frontend.test` / `api.backend.test`) over local HTTPS, real Waitress/Flask/SQLite/Excel and third-party-cookie-phaseout enabled. Secure partitioned cookie storage, transmission and HttpOnly isolation were observed. MQTT was disabled, data/accounts were synthetic, and no production endpoint was used for these tests.

| Executed suite | PASS | SIMULATED | FAIL |
|---|---:|---:|---:|
| Flask/SQLite/Excel integration: 14 tests | 14 | 0 | 0 |
| Client/worker/static regressions: 14 tests | 3 | 11 | 0 |
| Real-browser suite: 14 tests | 11 | 3 | 0 |
| **Total: 42** | **28** | **14** | **0** |

No final PARTIAL/BLOCKED result within the 42 executed tests. Node's runner reports all 14 assertions passing; 11 use stubbed transport/cache/event scenarios and are conservatively classified SIMULATED here.

**Backend PASS coverage:** registration/login/session; secure cookie and deletion attributes; credentialed API; profile mutation; CSRF rejection without reservation change; untrusted origins; allowed/rejected preflight; readable no-store 401; expiry; server logout revocation; User/Admin separation and Owner login; booking creation/retry/completion; cross-user read rejection; cancellation; simultaneous competing bookings (one accepted); Owner cancellation observed by the owner account.

**Browser results:** B01 anonymous stable screen/assets PASS; B02 registration/login/cookie/API PASS; B03 refresh restoration PASS; B04 CSRF/create/retry/complete PASS; B05 worker/cache privacy PASS; B06 actual browser offline shell/missing-asset 503/recovery PASS; B07 injected backend outage SIMULATED; B08 simultaneous expired-session 401 without reload PASS; B09 sign-in/logout/revocation PASS; B10 artificially suppressed cookie SIMULATED; B11 locally served legacy redirect and responsive shell PASS; B12 synthetic old-worker/cache upgrade SIMULATED; B13 no JavaScript exceptions/wrong-origin API requests PASS; B14 first-party Owner login/dashboard/logout behind local TLS proxy PASS.

**Client/worker checks:** static manifest/HTML/CSS/worker asset existence/version alignment, deployment redirect structure and invalid configuration rejection PASS. Mocked credentialed transport, malformed response, obsolete auth/401 response fencing, single expiry, cache exclusions, offline fallbacks, cache-storage failures and worker redirects SIMULATED.

Python syntax/imports, all frontend/Owner/test JavaScript syntax, deployment JSON and dependency installation also succeeded. Actual Waitress startup honored the test PORT. Mobile User (390×844) and desktop Owner (1366×900) screenshots were inspected; expected styling remained intact. This is targeted visual verification, not a full repeat UI audit.

**Failures found and resolved:** the browser test caught an installed worker retaining `/user/` despite the server redirect; explicit worker redirection fixed it and the full browser suite passed again. An added concurrency test initially chose intentionally unavailable Locker C (both requests correctly rejected); correcting its fixture to available B verified one acceptance/one conflict without changing business rules. A sandbox subprocess restriction was resolved by authorized execution; no application change was needed.

## Actions still required manually

1. Upload the corrected **contents** of each official folder to its corresponding project using your normal workflow. No upload/commit/deployment was performed here.
2. **Railway:** keep the existing volume and exact `SANDLOCK_RESERVATION_DB` / `SANDLOCK_WORKBOOK` paths. Set/check:
   - `SANDLOCK_RESERVATION_ORIGINS=https://sandlock-app.vercel.app` (no trailing slash)
   - `SANDLOCK_LOCAL_HTTP=0`, `SANDLOCK_COOKIE_PARTITIONED=1`
   - `SANDLOCK_TRUST_PROXY=1`, `SANDLOCK_HTTP_HOST=0.0.0.0`
   - `SANDLOCK_INITIALIZE_STORAGE=0`, `SANDLOCK_RESERVATION_WORKER=1`
   - Let Railway supply `PORT`. Retain authorized private MQTT configuration; no credential rotation is part of this update.
3. Redeploy Railway first. Ensure its start command is `python -B server.py` (remove any old conflicting override), Waitress installed, one service replica, correct PORT binding and no development-server warning. Do **not** replace the live workbook, delete initialization markers or recreate storage. Proxy trust is for the Railway edge only.
4. **Vercel:** Framework Preset Other, root containing `index.html`, no build command, output root `.`. Use the supplied `vercel.json`; remove conflicting old `/user` or API rewrite settings. No frontend secrets/environment variables are needed. Redeploy the frontend after Railway.
5. Refresh existing tabs normally; sign in once under the new cookie name. Reopen installed PWAs to load the update. If an old installation remains stuck, close all tabs and clear only that site's old worker/cache as a troubleshooting fallback. Never clear backend storage.
6. Verify the actual deployed register → login → API → reservation → refresh → logout journey, plus Owner login at `https://sandlock-control-production.up.railway.app/static/login.html`. Check trusted CORS, Set-Cookie and CSRF behavior in Network tools without sharing token values.

## Remaining verification / limitations

| Not executed | Classification and reason |
|---|---|
| Live Vercel/Railway redeployment, edge headers, volume wiring and live end-to-end flow | **BLOCKED** pending your deployment; local checks cannot certify provider configuration. |
| Safari/iOS/Firefox and installed mobile PWA cookie behavior | **BLOCKED**: those browsers/devices were unavailable. Partitioned cookies help supported browsers; browsers/policies refusing cross-site cookies cannot be guaranteed. The app now detects failed cookie retention instead of looping. |
| Physical unlock, ESP32 ACK/door state and real broker delivery/authorization | **BLOCKED / outside this task**: intentionally no real broker or hardware use. MQTT/device code and protocol were preserved, not physically certified. |

These three unexecuted verification areas are separate from the 42-test automated total. No collected revenue/payment-provider success or physical opening is claimed. Existing multi-replica/MQTT coordination constraints remain; this change uses one process and does not certify zero-downtime overlapping device workers.

Temporary test credentials, certificates, synthetic databases and installed test dependency directories were removed after verification; reusable harnesses remain. Development test servers were stopped.

References: [MDN partitioned cookies](https://developer.mozilla.org/en-US/docs/Web/Privacy/Guides/Third-party_cookies/Partitioned_cookies) and [Waitress deployment](https://docs.pylonsproject.org/projects/waitress/en/stable/usage.html). Deployment is still required; local success is not a claim that the live sites have already changed.
