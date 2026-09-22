"""Durable application records; broker echoes are not persistence acknowledgements."""
import json,hashlib,uuid
from datetime import datetime,timezone
from reservation_service import ReservationError
from security import identifier

def accept(db,actor,kind,data,event_id=None):
    body=json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    if event_id is not None and not identifier(event_id):raise ReservationError('Invalid event identifier')
    key=actor+':'+kind+':'+(event_id or hashlib.sha256(body.encode()).hexdigest())
    old=db.execute('SELECT body FROM application_events WHERE event_id=?',(key,)).fetchone()
    if old:
        if old['body']!=body:raise ReservationError('Event identifier already used with different content',409)
        return key,False
    db.execute('INSERT INTO application_events(event_id,actor,kind,body,created_at) VALUES(?,?,?,?,?)',(key,actor,kind,body,datetime.now(timezone.utc).isoformat()))
    return key,True

def record(store,actor,kind,data,event_id=None):
    return store.transaction(lambda db:accept(db,actor,kind,data,event_id))
