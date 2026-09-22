import os
BROKER_HOST = os.environ.get("SANDLOCK_BROKER_HOST", "")
BROKER_PORT = 8883
BROKER_USERNAME = os.environ.get("SANDLOCK_BROKER_USERNAME", "")
BROKER_PASSWORD = os.environ.get("SANDLOCK_BROKER_PASSWORD", "")
BASE_TOPIC = "sandlock/locker/A"
QOS = 1
DATABASE_FILE = "data/SandLock_Dashboard_Database.xlsx"


# Group 1: local reservation authority and explicit integration configuration.
import os
from pathlib import Path
RESERVATION_DATABASE = os.environ.get("SANDLOCK_RESERVATION_DB", "data/SandLock_Reservations.sqlite3")
DATABASE_FILE = os.environ.get("SANDLOCK_WORKBOOK", DATABASE_FILE)
USER_APP_PATH = Path(os.environ.get("SANDLOCK_USER_APP", str(Path(__file__).resolve().parent.parent / "user")))
RESERVATION_ORIGINS = tuple(x.strip() for x in os.environ.get("SANDLOCK_RESERVATION_ORIGINS", "").split(",") if x.strip())
LEGACY_TIMEZONE = os.environ.get("SANDLOCK_LEGACY_TIMEZONE")
MQTT_ENABLED = os.environ.get("SANDLOCK_MQTT_ENABLED", "1") == "1"
RESERVATION_WORKER = os.environ.get("SANDLOCK_RESERVATION_WORKER", "1") == "1"
HTTP_HOST = os.environ.get("SANDLOCK_HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("SANDLOCK_HTTP_PORT", "5000"))

# Group 2 sessions: plain HTTP is an explicit local development choice.
AUTH_COOKIE_SECURE = os.environ.get("SANDLOCK_LOCAL_HTTP", "0") != "1"

if MQTT_ENABLED and not all((BROKER_HOST, BROKER_USERNAME, BROKER_PASSWORD)):
    raise RuntimeError("MQTT enabled but required server configuration is missing")

# Fresh isolated stores require explicit SANDLOCK_INITIALIZE_STORAGE=1.
# Established storage is never silently recreated when its initialization marker exists.
