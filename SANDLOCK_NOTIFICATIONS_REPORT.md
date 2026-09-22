# SandLock notification implementation report

## Status and scope

Implemented locally in the same `C:/Users/mayar/Downloads/sandlock-app` and `sandlock-control` folders. **External push delivery is not yet production-certified:** your VAPID configuration, deployment and real-device acceptance tests remain required. No Git operation, deployment, production data access, real push-provider request, broker connection or physical-locker operation was performed. Earlier D: sources and supplied ZIPs were untouched.

## Implementation

**Flow:** existing `sandlock/locker/A/door` → explicit open/closed observation → transactional lookup of the started, non-terminal reservation for that exact locker → authenticated/explicitly mapped owner → persistent notification and delivery jobs → separate Web Push retry worker → encrypted Push API payload → existing service worker → localized OS notification → private history view.

No new MQTT topic, device capability, reservation rule or firmware contract was invented. The actual live topic maps only Locker A. The notification service can route other locker IDs when supplied by a trusted integration; Locker B was tested synthetically. The existing one-open-reservation-per-user rule remains unchanged; multiple-record routing was verified using synthetic legacy records, not by weakening booking validation.

Notifications identify the locker, event, full date/time including seconds, and timezone. Internally timestamps are UTC **backend receipt times**. The existing protocol does not supply a verified physical-event timestamp, so the UI/push does not falsely claim that precision. Only explicit `open`/`closed` door reports count; ACK/unlock success is not treated as a door opening.

### Storage, security and failure handling

- Additive, idempotent startup migration adds `notifications`, `locker_notification_state`, `push_subscriptions`, `notification_deliveries`, plus an owner/time index, to the existing SQLite store. No account/reservation/financial table is replaced, and no workbook/schema initialization is required. Migration was tested with initialization disabled and existing accounts/sessions/bookings preserved.
- Records include notification/user/booking/locker IDs, event/message, UTC timestamp and read timestamp. Authenticated history includes unread count and pagination; users can mark only their own records read.
- Four endpoints only: `GET /api/v1/notifications`; `POST /api/v1/notifications/<id>/read`; `POST /api/v1/push-subscriptions`; `POST /api/v1/push-subscriptions/remove`. Existing cookies, CORS, CSRF and server-derived identity apply unchanged. Client/device user IDs cannot select recipients.
- Subscriptions bind to the authenticated account and session. HTTPS provider allowlisting and key validation prevent arbitrary server-side URL requests; redirects are disabled. Private VAPID keys stay server-side. Push payloads omit PINs, names, mobile numbers, account IDs and reservation IDs.
- Door state persists across restart: repeated identical reports/retained messages do not duplicate history. Closed → open permits the next genuine opening; first closed establishes a baseline. Ambiguous ownership produces no misdirected notification. Without reliable device event IDs, a missing close or out-of-order replay cannot be perfectly distinguished; this remains a protocol limitation.
- Delivery jobs survive restart; 120-second claims prevent concurrent dispatch, retries are bounded at eight attempts, timeout is eight seconds, event/provider TTL is 24 hours. 404/410 deactivates invalid subscriptions. History survives push failure. Queue success means provider acceptance, **not confirmed handset receipt**.
- Logout deactivates that session's subscriptions; session expiry prevents new sends. Local opaque worker binding plus serial push processing/deduplication suppresses old-account or duplicate queued payloads. No session tokens or notification history are stored in localStorage/IndexedDB. IndexedDB holds only opaque binding/deduplication identifiers. Notifications already seen before logout cannot be recalled.

### User interface / worker

Added a Home bell/unread badge, Profile entry and styled history screen with exact timestamps, read/unread state, mark-read, empty/error/loading/retry states and earlier-history loading. Existing card/button/type/mobile layout patterns were reused. Permission is requested **only** by Enable Notifications; unsupported, denied, activation failure, missing/expired subscription and disable states are explicit. Account changes and logout clear private view data, including delayed responses.

Worker release `sandlock-notifications-v1` / assets `notifications-1` adds push, click/focus, opaque binding and subscription-change handling. Existing offline Responses, cache exclusions, legacy redirects and no-reload behavior remain. Click targets are fixed to this app/history, never a payload-supplied external URL. Booking no longer prompts for permission implicitly; existing in-page 15-minute reminders remain, and were not converted into background push reminders.

## Files changed / added

- **User:** new `notifications.js`; changed `index.html`, `styles.css` (notification-only rules), `app.js`, `auth-client.js`, `service-worker.js`, `README.md`.
- **Control:** new `notifications.py`, `manage_push_keys.py`; changed `server.py`, `config.py`, `requirements.txt` (`pywebpush==2.5.0`), `.env.example`, `.gitignore`, `README.md`.
- **Tests:** new `tests/test_notifications.py`, `tests/notifications-worker.test.cjs`, `tests/inject_notification.py`; updated `tests/test_integration.py`, `tests/serve_browser.py`, `tests/browser.cjs`, `tests/frontend.test.cjs`; this report.
- Reservation, authentication storage/password rules, financial/device modules, broker configuration, `railway.json`, `vercel.json`, frontend API configuration and the supplied workbook were not changed.

## Verification

Real local Flask/Waitress/SQLite/Excel and Edge over two local HTTPS sites were used. External push/device/permission behaviors were mocked or injected where stated. All existing regression suites were rerun.

| Suite | Tests | PASS | SIMULATED | FAIL |
|---|---:|---:|---:|---:|
| Existing Flask/API/database regressions | 14 | 14 | 0 | 0 |
| Existing client/worker/static regressions | 14 | 3 | 11 | 0 |
| Existing browser regressions | 14 | 11 | 3 | 0 |
| New notification backend tests | 16 | 0 | 16 | 0 |
| New push-worker tests | 8 | 0 | 8 | 0 |
| New notification browser tests | 4 | 1 | 3 | 0 |
| **Total executed** | **70** | **29** | **41** | **0** |

The new backend suite uses real APIs/SQLite for ownership, read-state and migration, but is conservatively classified SIMULATED as a whole because its door events/push transport are synthetic. All 70 automated assertions passed; a mock's success is never called physical push success.

**Required coverage:** N01 correct A owner/locker/time and dispatch; N02 closed/duplicate/separate openings and restart; N03 multiple legacy reservations and B routing; N04 cross-user history/read denial and read count; N05 unauthenticated/CSRF/revoked access; N06 subscription ownership/retry/logout; N07 expired provider subscription; N08 provider outage/restart retry; N09 session expiry; N10 retained/unknown/baseline/no-owner reports; N11 notification failure isolation; N12 URL/key validation/private-key nonexposure; N13 additive migration without initialization; N14 concurrent duplicate events; N15 real encryption/VAPID signing with mocked HTTP (including provider-specific headers); N16 disable/missing-key rejection.

Eight worker tests cover exact B/date/time content, simultaneous duplicate suppression, separate openings/closed alerts, logout, account replacement, malformed payloads, safe click routing and subscription expiry. Four new browser tests cover history/read/refresh/unread display, denied/unsupported/no-startup-prompt states, logout/account switching, and explicit enable/disable/subscription failure with a real worker binding but mocked Push API/provider registration.

Existing registration/login/refresh/logout, CORS/CSRF/cookies, reservation create/retry/cancel/complete/concurrency, Owner dashboard and service-worker offline/update checks remain passing. Python/JS syntax, dependency installation, deployment JSON and private-key utility key-match/no-overwrite checks also passed. Mobile 390×844 and desktop 1366×900 screenshots were inspected; no unrelated UI redesign or overflow was introduced.

**Issues resolved during work:** corrected notification text encoding caught by screenshot review; added account-switch view fencing and serialized push handling; adjusted test fixtures to compare persistent data rather than time-varying calculated fields, recognize fragment links, and complete only their own prior synthetic booking on rerun. Final regression failures: none.

## Manual setup and deployment

1. Install updated backend requirements locally, then generate a stable private key pair:
   `python manage_push_keys.py --contact mailto:YOUR_MONITORED_EMAIL_ADDRESS`
   Replace the placeholder with your email address. The tool creates `.env.vapid`, refuses overwrite and does not print keys. Keep this file private; **never upload it to GitHub or Vercel**.
2. Copy these values privately into Railway:

| Variable | Value |
|---|---|
| `SANDLOCK_PUSH_ENABLED` | `1` when ready to enable delivery; default `0` retains history only |
| `SANDLOCK_VAPID_PUBLIC_KEY` | Generated public key |
| `SANDLOCK_VAPID_PRIVATE_KEY` | Generated private key; backend secret only |
| `SANDLOCK_VAPID_SUBJECT` | `mailto:` followed by your monitored email address |

3. Preserve every existing volume path, cookie/origin setting and start command. Keep `SANDLOCK_INITIALIZE_STORAGE=0`. Redeploy the backend first; its additive migration runs automatically. Do not recreate/delete the database or replace the workbook. Keep the existing single-instance deployment boundary.
4. Redeploy the updated frontend. **No Vercel environment, rewrite or routing change is required.** Normal worker update/refresh loads the new version. Users open the bell → Enable Notifications explicitly. Keep VAPID keys stable; rotation requires resubscription.
5. Verify a controlled real notification on each supported target platform before relying on delivery. Confirm foreground, background, closed-tab, click-to-history, logout, and reopening after session expiry. Enabling is tied to the current sign-in: after logout or expiry, sign in and enable again. The existing User session lasts up to seven days.

## BLOCKED / real verification required

- **External Web Push/provider → actual OS display:** not executed. Real production VAPID configuration, user consent and target devices are required. Provider acceptance, browser shutdown, power policies, offline duration and notification settings can all affect delivery.
- **Android installed PWA and iOS/iPadOS/Safari:** not physically tested. Supported iOS/iPadOS Web Push requires a Home Screen web app and explicit user gesture; desktop/Android requirements vary. Existing cross-site session-cookie restrictions also still apply. No universal browser claim is made.
- **Real broker/ESP32 event quality:** not exercised. Confirm sensor open/closed reporting, non-retained live delivery, missed/out-of-order transitions and timestamp expectations. Existing protocol cannot certify exact physical-event time; no firmware changes were made.
- **Live Railway migration/deployment:** not performed. Local migration/regression success is not proof that the live deployment has been updated.

These four unexecuted acceptance areas are outside the 70-test executed total. History cannot be recorded during an unrecoverable database write failure; that failure is logged without stopping device handling. Backend/OS push is best-effort, not a guaranteed physical security alarm. Temporary local test credentials/data/dependencies were removed after testing; reusable harnesses remain and local test servers were stopped.

References: [MDN Push API](https://developer.mozilla.org/en-US/docs/Web/API/Push_API), [Apple Web Push requirements](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers), [pywebpush](https://github.com/web-push-libs/pywebpush), [Microsoft raw push headers](https://learn.microsoft.com/en-us/windows/apps/develop/notifications/push-notifications/raw-notification-overview).
