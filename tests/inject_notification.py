"""Inject synthetic door observations into the local browser-test store only."""
import sys,os,json,uuid
from pathlib import Path
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path[:0]=[str(ROOT),str(ROOT/'.test-deps')]
runtime=(ROOT/'tests/.runtime').resolve();paths=json.loads((runtime/'browser-storage.json').read_text())
for value in paths.values():
 if not Path(value).resolve().is_relative_to(runtime):raise RuntimeError('Not a local test path')
os.environ.update(SANDLOCK_MQTT_ENABLED='0',SANDLOCK_PUSH_ENABLED='0',SANDLOCK_RESERVATION_WORKER='0',SANDLOCK_INITIALIZE_STORAGE='0',SANDLOCK_RESERVATION_DB=paths['db'],SANDLOCK_WORKBOOK=paths['workbook'])
import server as s
from reservation_service import iso
with s.reservations.store.connect() as db:row=db.execute('SELECT user_id FROM accounts WHERE login=?',(sys.argv[1],)).fetchone()
if not row:raise RuntimeError('Synthetic user missing')
# Finish only this harness's previous synthetic reservations on repeated runs.
for previous in s.reservations.list():
 if previous['bookingId'].startswith('NB-') and previous['status'] in ('Active','Overdue'):
  s.reservations.transition(previous['bookingId'],'complete',previous['revision'])
s.notifications.observe('A','closed')
now=datetime.now(timezone.utc)
s.reservations.create(dict(bookingId='NB-'+uuid.uuid4().hex,userId=row[0],lockerId='A',pin='1234',startTime=iso(now),endTime=iso(now+timedelta(minutes=30))))
for door in ('open','open','closed','open'):s.notifications.observe('A',door)
