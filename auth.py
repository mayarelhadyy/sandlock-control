"""Server-controlled prototype accounts and revocable sessions; no broker identity."""
import hashlib
import secrets
import re
import time
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from reservation_service import ReservationError

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

class Auth:
    def __init__(self, store):
        self.store = store
        self.clock = time.time
        with store.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS accounts (
              user_id TEXT PRIMARY KEY, login TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
              role TEXT NOT NULL CHECK(role IN ('user','admin')), name TEXT NOT NULL, mobile TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
              token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL,
              csrf TEXT NOT NULL, expires REAL NOT NULL, last_seen REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS legacy_owners (
              legacy_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, reviewed_by TEXT NOT NULL, reviewed_at REAL NOT NULL);
            ''')
            cols={r['name'] for r in db.execute('PRAGMA table_info(accounts)').fetchall()}
            if 'created_at' not in cols:
                db.execute('ALTER TABLE accounts ADD COLUMN created_at TEXT')

    @staticmethod
    def public(row):
        return {k:row[k] for k in ('user_id','login','role','name','mobile')} | {'userId':row['user_id']}

    def register(self, body, role='user'):
        login=str(body.get('login','')).strip().lower()
        password=body.get('password','')
        name=body.get('name',''); mobile=body.get('mobile','')
        password_min = 8 if role == 'user' else 12
        if not re.fullmatch(r'[a-z0-9_.-]{3,64}',login) or not isinstance(password,str) or not password_min<=len(password)<=128:
            raise ReservationError(f'Login must be 3–64 letters/digits/._-; password must be {password_min}–128 characters')
        if not isinstance(name,str) or not 2<=len(name.strip())<=120:
            raise ReservationError('Invalid profile')
        if role == 'user' and (not isinstance(mobile,str) or not re.fullmatch(r'[0-9]{11}', mobile)):
            raise ReservationError('Mobile number must be exactly 11 digits.')
        if role != 'user' and (not isinstance(mobile,str) or len(mobile)>30):
            raise ReservationError('Invalid profile')
        uid='usr_'+secrets.token_hex(16)
        hashed=generate_password_hash(password,method='scrypt')
        def save(db):
            if db.execute('SELECT 1 FROM accounts WHERE login=?',(login,)).fetchone():
                raise ReservationError('Login unavailable',409)
            db.execute('INSERT INTO accounts(user_id,login,password_hash,role,name,mobile,created_at) VALUES (?,?,?,?,?,?,?)',(uid,login,hashed,role,name.strip(),mobile,datetime.now(timezone.utc).isoformat()))
            return self.public(db.execute('SELECT * FROM accounts WHERE user_id=?',(uid,)).fetchone())
        return self.store.transaction(save)

    def login(self, body, kind):
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM accounts WHERE login=?',(str(body.get('login','')).strip().lower(),)).fetchone()
        password=body.get('password','')
        if not row or not isinstance(password,str) or len(password)>128 or not check_password_hash(row['password_hash'],password) or row['role']!=kind:
            raise ReservationError('Invalid credentials',401)
        token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32); now=self.clock()
        with self.store.connect() as db:
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?)',(digest(token),row['user_id'],kind,csrf,now+(28800 if kind=='admin' else 604800),now))
        return token,csrf,self.public(row)


    def demo_admin_session(self):
        """Create/reuse an isolated local demo owner identity. Caller must gate this by config."""
        login='sandlock-demo-owner'
        with self.store.connect() as db:
            row=db.execute("SELECT * FROM accounts WHERE login=? AND role='admin'",(login,)).fetchone()
            if not row:
                uid='usr_'+secrets.token_hex(16)
                db.execute('INSERT INTO accounts(user_id,login,password_hash,role,name,mobile,created_at) VALUES (?,?,?,?,?,?,?)',(uid,login,generate_password_hash(secrets.token_urlsafe(32),method='scrypt'),'admin','Demo Owner','',datetime.now(timezone.utc).isoformat()))
                row=db.execute('SELECT * FROM accounts WHERE user_id=?',(uid,)).fetchone()
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32);now=self.clock()
        with self.store.connect() as db:
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?)',(digest(token),row['user_id'],'admin',csrf,now+28800,now))
        return token,csrf,self.public(row)

    def resolve(self, token, kind):
        if not token:return None
        with self.store.connect() as db:
            row=db.execute('SELECT a.*,s.csrf,s.expires,s.last_seen,s.token_hash FROM sessions s JOIN accounts a USING(user_id) WHERE s.token_hash=? AND s.kind=? AND a.role=?',(digest(token),kind,kind)).fetchone()
        if not row or row['expires']<=self.clock() or (kind=='admin' and self.clock()-row['last_seen']>=1800):return None
        return dict(row)

    def revoke(self, token):
        with self.store.connect() as db:db.execute('DELETE FROM sessions WHERE token_hash=?',(digest(token or ''),))

    def touch(self, row):
        with self.store.connect() as db:db.execute('UPDATE sessions SET last_seen=? WHERE token_hash=?',(self.clock(),row['token_hash']))

    def map_owner(self, legacy_id, user_id, admin_id):
        def operation(db):
            target=db.execute("SELECT 1 FROM accounts WHERE user_id=? AND role='user'",(user_id,)).fetchone()
            actor=db.execute("SELECT 1 FROM accounts WHERE user_id=? AND role='admin'",(admin_id,)).fetchone()
            if not target or not actor:raise ReservationError('Valid User and reviewing Admin are required')
            if db.execute('SELECT 1 FROM accounts WHERE user_id=?',(legacy_id,)).fetchone():raise ReservationError('Cannot remap an authenticated account')
            old=db.execute('SELECT user_id FROM legacy_owners WHERE legacy_id=?',(legacy_id,)).fetchone()
            if old and old[0]!=user_id:raise ReservationError('Existing ownership mapping requires manual review',409)
            if not db.execute('SELECT 1 FROM reservations WHERE user_id=?',(legacy_id,)).fetchone():raise ReservationError('Legacy owner not found',404)
            ids={user_id,legacy_id}|{r[0] for r in db.execute('SELECT legacy_id FROM legacy_owners WHERE user_id=?',(user_id,))}
            opens=[r for r in self.store.rows(db) if r['userId'] in ids and r['status'] in ('Confirmed','Active','Overdue')]
            if len(opens)>1:raise ReservationError('Multiple open reservations require review before assignment',409)
            db.execute('INSERT OR IGNORE INTO legacy_owners VALUES (?,?,?,?)',(legacy_id,user_id,admin_id,self.clock()))
        return self.store.transaction(operation)
