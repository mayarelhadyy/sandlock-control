"""Local device command journal. Never stores wire payloads or PINs."""
import sqlite3,time
from pathlib import Path
from reservation_store import guarded_connect
class DeviceStore:
    def __init__(self,path):
        self.path=str(path)+'.device.sqlite3'
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS commands (request_id TEXT PRIMARY KEY, actor TEXT, session TEXT, booking TEXT, locker TEXT, created REAL, expires REAL, outcome TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS command_events (event_id INTEGER PRIMARY KEY, request_id TEXT, actor TEXT, booking TEXT, locker TEXT, timestamp REAL, outcome TEXT)")
            db.execute("INSERT INTO command_events(request_id,actor,booking,locker,timestamp,outcome) SELECT request_id,actor,booking,locker,?,'unknown' FROM commands WHERE outcome IN ('pending','published')",(time.time(),))
            db.execute("UPDATE commands SET outcome='unknown' WHERE outcome IN ('pending','published')")
        Path(self.path+'.initialized').touch(exist_ok=True)
    def connect(self):return guarded_connect(self.path,timeout=10)
    def start(self,req,identity,booking):
        with self.connect() as db:
            db.execute("INSERT INTO commands VALUES (?,?,?,?,?,?,?,?)",(req,identity['user_id'],identity['token_hash'],booking,'A',time.time(),time.time()+7,'pending'))
            db.execute('INSERT INTO command_events(request_id,actor,booking,locker,timestamp,outcome) VALUES (?,?,?,?,?,?)',(req,identity['user_id'],booking,'A',time.time(),'pending'))
    def outcome(self,req,value):
        with self.connect() as db:
            db.execute("INSERT INTO command_events(request_id,actor,booking,locker,timestamp,outcome) SELECT request_id,actor,booking,locker,?,? FROM commands WHERE request_id=? AND outcome IN ('pending','published')",(time.time(),value,req))
            db.execute("UPDATE commands SET outcome=? WHERE request_id=? AND outcome IN ('pending','published')",(value,req))
    def expire(self):
        with self.connect() as db:
            db.execute("INSERT INTO command_events(request_id,actor,booking,locker,timestamp,outcome) SELECT request_id,actor,booking,locker,?,'unknown' FROM commands WHERE expires<? AND outcome IN ('pending','published')",(time.time(),time.time()))
            db.execute("UPDATE commands SET outcome='unknown' WHERE expires<? AND outcome IN ('pending','published')",(time.time(),))
    def get(self,req,actor):
        self.expire()
        with self.connect() as db:
            db.row_factory=sqlite3.Row
            row=db.execute('SELECT request_id,outcome FROM commands WHERE request_id=? AND actor=?',(req,actor)).fetchone()
            return dict(row) if row else None
    def events(self):
        self.expire()
        with self.connect() as db:
            db.row_factory=sqlite3.Row
            return [dict(r) for r in db.execute('SELECT * FROM command_events')]

