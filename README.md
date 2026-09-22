# SandLock Admin / integrated backend — clean final copy

This package is the cumulative Groups 1–7 implementation. It contains the Owner UI plus the shared Flask authentication, reservation authority, device gateway, financial, durability and reporting modules. SQLite is authoritative; Excel is a reporting projection. There are no accounts, sessions, reservation records or credentials in this package. The included Excel file is a blank header-only runtime template, not a data migration.

## Local setup (Windows PowerShell)

Extract both ZIPs under the same parent, leaving `SandLock_Admin_Final` beside `SandLock_User_Final`. Open a terminal in `SandLock_Admin_Final`:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:SANDLOCK_MQTT_ENABLED='0'
$env:SANDLOCK_LOCAL_HTTP='1'
$env:SANDLOCK_HTTP_HOST='127.0.0.1'
$env:SANDLOCK_HTTP_PORT='5000'
$env:SANDLOCK_USER_APP=(Resolve-Path '..\SandLock_User_Final').Path
$env:SANDLOCK_INITIALIZE_STORAGE='1'
python -B manage_accounts.py create-admin YOUR_ADMIN_LOGIN --name 'Owner'
# Choose your own password at the private prompts; no default account exists.
$env:SANDLOCK_INITIALIZE_STORAGE='0'
python -B server.py
```

On Linux/macOS, create/activate a Python venv, install `requirements.txt`, and export the same environment variables. Set `SANDLOCK_USER_APP` to the absolute path of the extracted User folder. Run `python3 -B manage_accounts.py create-admin YOUR_ADMIN_LOGIN --name Owner` once with initialization enabled, then disable initialization and run `python3 -B server.py`.

Owner login: http://127.0.0.1:5000/static/login.html
User registration/login: http://127.0.0.1:5000/user/
The protected Owner root is http://127.0.0.1:5000/.

`.env.example` documents settings; it is NOT automatically read. The preserved `run_dashboard` scripts also require these environment variables first and may install dependencies. No MQTT connection is made with `SANDLOCK_MQTT_ENABLED=0`. Missing broker settings fail closed if MQTT is explicitly enabled. Do not delete storage initialization markers to bypass recovery protections.

## Deployment/configuration boundary

The tested architecture serves User and Owner from the same Flask origin. Explicitly set `SANDLOCK_USER_APP`: the unchanged application default expects a sibling named `user`, not the new final folder name. This environment variable corrects the packaging path without changing application code. Admin can serve its UI/APIs without the User folder, but `/user/` requires that folder.

Use persistent writable paths for SQLite and the reporting workbook; default paths are under this Admin folder. Supply overrides through `SANDLOCK_RESERVATION_DB` and `SANDLOCK_WORKBOOK` if needed. Existing real data requires a separate reviewed migration/backup operation; none is included or performed here. A blank workbook is required before first use because the current runtime does not create its initial workbook schema.

For later HTTPS deployment, unset local HTTP or set `SANDLOCK_LOCAL_HTTP=0`; retain secure cookies, same-origin authentication/CSRF, protected PIN handling and no-store responses. A separately hosted User site needs a deliberately configured same-origin reverse proxy for `/auth/` and `/api/`; changing only the reservation API URL is insufficient. No production URL, broker secret, external payment integration or deployment is configured here. Keep secrets server-side.

Physical-device certification remains deferred. Demo booking values are not collected revenue. This copy operation adds no business/device features and does not constitute the Final Deep E2E audit.
