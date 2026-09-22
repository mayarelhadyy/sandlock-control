"""Transactional reservation authority. Excel is a projection, never a writer here."""
import json
import sqlite3
import os
from pathlib import Path


class ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()

def guarded_connect(path, **kwargs):
    path=Path(path).resolve();marker=Path(str(path)+'.initialized')
    if not path.exists():
        if marker.exists() or os.environ.get('SANDLOCK_INITIALIZE_STORAGE')!='1':
            raise sqlite3.OperationalError('Storage requires explicit initialization or recovery')
        path.parent.mkdir(parents=True,exist_ok=True)
        mode='rwc'
    else:mode='rw'
    db=sqlite3.connect(path.as_uri()+'?mode='+mode,uri=True,factory=ClosingConnection,**kwargs)
    try:
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise sqlite3.DatabaseError('Storage integrity check failed')
    except Exception:db.close();raise
    return db

class ReservationStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS financial_events (
                    reference TEXT PRIMARY KEY, booking_id TEXT NOT NULL, kind TEXT NOT NULL,
                    body TEXT NOT NULL, created_at TEXT NOT NULL, projected INTEGER NOT NULL DEFAULT 0, projection_version INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(booking_id,kind));
                CREATE TABLE IF NOT EXISTS application_events (event_id TEXT PRIMARY KEY, actor TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, projected INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS device_intents (booking_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, timezone TEXT, done INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS reservations (
                    booking_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    locker_id TEXT NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS legacy_owners (
                    legacy_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, reviewed_by TEXT NOT NULL, reviewed_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS reservation_owner ON reservations(user_id);
                CREATE TABLE IF NOT EXISTS reservation_outbox (
                    booking_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    action TEXT NOT NULL, source TEXT NOT NULL, body TEXT NOT NULL,
                    projected INTEGER NOT NULL DEFAULT 0,
                    notified INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (booking_id, revision));
            ''')
            if 'projection_version' not in [r[1] for r in db.execute('PRAGMA table_info(financial_events)')]:
                db.execute('ALTER TABLE financial_events ADD COLUMN projection_version INTEGER NOT NULL DEFAULT 1')
        Path(self.path+'.initialized').touch(exist_ok=True)

    def connect(self):
        db = guarded_connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=10000')
        return db

    def transaction(self, operation):
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            result = operation(db)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def rows(db):
        return [json.loads(r['body']) for r in db.execute('SELECT body FROM reservations')]

    @staticmethod
    def get(db, booking_id):
        row = db.execute('SELECT body FROM reservations WHERE booking_id=?', (booking_id,)).fetchone()
        return json.loads(row['body']) if row else None

    @staticmethod
    def save(db, value, action, source):
        body = json.dumps(value, ensure_ascii=False)
        db.execute('''INSERT INTO reservations VALUES (?,?,?,?,?)
            ON CONFLICT(booking_id) DO UPDATE SET user_id=excluded.user_id,
            locker_id=excluded.locker_id,status=excluded.status,body=excluded.body''',
            (value['bookingId'], value['userId'], value['lockerId'], value['status'], body))
        if value['lockerId']=='A':
            db.execute('INSERT INTO device_intents(booking_id,revision) VALUES(?,?) ON CONFLICT(booking_id) DO UPDATE SET revision=excluded.revision,done=0',(value['bookingId'],value['revision']))
        db.execute('INSERT INTO reservation_outbox(booking_id,revision,action,source,body) VALUES(?,?,?,?,?)',
                   (value['bookingId'], value['revision'], action, source, json.dumps({k:v for k,v in value.items() if k!='pin'})))

    def pending(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM reservation_outbox WHERE projected=0 OR notified=0 ORDER BY rowid')]

    def mark(self, booking_id, revision, column):
        if column not in ('projected', 'notified'):
            raise ValueError('Invalid outbox column')
        with self.connect() as db:
            db.execute(f'UPDATE reservation_outbox SET {column}=1 WHERE booking_id=? AND revision=?', (booking_id, revision))
