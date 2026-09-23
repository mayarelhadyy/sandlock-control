"""Private notification history and retryable Web Push, sharing the existing SQLite store."""
import base64, hashlib, json, logging, secrets, threading, time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
from reservation_service import ReservationError, parse_time, iso

log = logging.getLogger(__name__)
def decode_key(value, size):
    if not isinstance(value,str) or len(value)>200: raise ValueError('Invalid key')
    raw=base64.b64decode(value+'='*(-len(value)%4),altchars=b'-_',validate=True)
    if len(raw)!=size: raise ValueError('Invalid key length')
    return raw

def public_key(private):
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    key=ec.derive_private_key(int.from_bytes(decode_key(private,32),'big'),ec.SECP256R1())
    return base64.urlsafe_b64encode(key.public_key().public_bytes(Encoding.X962,PublicFormat.UncompressedPoint)).decode().rstrip('=')

class Notifications:
    def __init__(self, reservations, config, sender=None):
        self.reservations=reservations; self.store=reservations.store; self.config=config
        self.clock=time.time; self.sender=sender or self.send; self.dispatch_lock=threading.Lock()
        if config.PUSH_ENABLED:
            try:
                if public_key(config.VAPID_PRIVATE_KEY)!=config.VAPID_PUBLIC_KEY:raise ValueError()
                if not config.VAPID_SUBJECT.startswith('mailto:') or '@' not in config.VAPID_SUBJECT:raise ValueError()
            except Exception:raise RuntimeError('Valid matching VAPID keys and mailto contact are required when push is enabled') from None
        # Additive/idempotent migration of the existing DB; never reinitialize it.
        def migrate(db):
            for sql in (
              'CREATE TABLE IF NOT EXISTS locker_notification_state (locker_id TEXT PRIMARY KEY, door TEXT NOT NULL, observed_at TEXT NOT NULL)',
              'CREATE TABLE IF NOT EXISTS notifications (notification_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, booking_id TEXT NOT NULL, locker_id TEXT NOT NULL, event_type TEXT NOT NULL, message TEXT NOT NULL, occurred_at TEXT NOT NULL, read_at TEXT)',
              'CREATE INDEX IF NOT EXISTS notification_user_time ON notifications(user_id,occurred_at DESC)',
              'CREATE TABLE IF NOT EXISTS push_subscriptions (subscription_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_hash TEXT NOT NULL, channel TEXT NOT NULL, body TEXT NOT NULL, active INTEGER NOT NULL, updated_at REAL NOT NULL)',
              'CREATE TABLE IF NOT EXISTS notification_deliveries (notification_id TEXT NOT NULL, subscription_id TEXT NOT NULL, channel TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0, last_status INTEGER, PRIMARY KEY(notification_id,subscription_id))'
            ):db.execute(sql)
        self.store.transaction(migrate)

    def observe(self, locker, door, retained=False):
        from reservation_service import LOCKERS
        if retained or locker not in LOCKERS or door not in ('open','closed'):return []
        stamp=iso(datetime.fromtimestamp(self.clock(),timezone.utc))
        def save(db):
            previous=db.execute('SELECT door FROM locker_notification_state WHERE locker_id=?',(locker,)).fetchone()
            if previous and previous[0]==door:return []
            db.execute('INSERT INTO locker_notification_state VALUES(?,?,?) ON CONFLICT(locker_id) DO UPDATE SET door=excluded.door,observed_at=excluded.observed_at',(locker,door,stamp))
            # First closed report is a baseline, not proof of a close transition.
            if not previous and door=='closed':return []
            candidates=[r for r in self.store.rows(db) if r['lockerId']==locker and r['status'] in ('Confirmed','Active','Overdue') and parse_time(r['startTime'])<=parse_time(stamp)]
            if len(candidates)!=1:
                if candidates:log.warning('Door notification skipped: ambiguous reservation ownership')
                return []
            reservation=candidates[0];uid=self.reservations.owner(db,reservation['userId'])
            if not db.execute("SELECT 1 FROM accounts WHERE user_id=? AND role='user'",(uid,)).fetchone():return []
            nid=secrets.token_hex(16);kind='door-opened' if door=='open' else 'door-closed'
            verb='opened' if door=='open' else 'closed'
            message=f'Locker {locker} {verb} on {stamp} (backend receipt time).'
            db.execute('INSERT INTO notifications VALUES(?,?,?,?,?,?,?,NULL)',(nid,uid,reservation['bookingId'],locker,kind,message,stamp))
            for sub in db.execute('SELECT subscription_id,channel FROM push_subscriptions WHERE user_id=? AND active=1',(uid,)):
                db.execute('INSERT INTO notification_deliveries(notification_id,subscription_id,channel,state) VALUES(?,?,?,?)',(nid,sub[0],sub[1],'pending'))
            return [nid]
        return self.store.transaction(save)


    def reservation_time_alerts(self):
        """Persist one 15-minute reminder and one user-facing grace warning per booking."""
        now=datetime.fromtimestamp(self.clock(),timezone.utc)
        stamp=iso(now)
        def save(db):
            created=[]
            for reservation in self.store.rows(db):
                if reservation['status'] not in ('Active','Overdue'):continue
                end=parse_time(reservation['endTime'])
                event_type=None;message=None
                if end-timedelta(minutes=15) <= now < end:
                    event_type='reservation-ending-soon'
                    message=f"Locker {reservation['lockerId']} reservation ends in 15 minutes. Extend your time or end the reservation on time."
                elif end <= now < end+timedelta(minutes=10):
                    event_type='reservation-grace-period'
                    # Product copy intentionally communicates 5 minutes; the backend
                    # keeps a 10-minute technical grace period for connectivity issues.
                    message=f"Locker {reservation['lockerId']} reservation has ended. Please finish within 5 minutes to avoid late fees."
                if not event_type:continue
                uid=self.reservations.owner(db,reservation['userId'])
                if not db.execute("SELECT 1 FROM accounts WHERE user_id=? AND role='user'",(uid,)).fetchone():continue
                if db.execute('SELECT 1 FROM notifications WHERE user_id=? AND booking_id=? AND event_type=? LIMIT 1',(uid,reservation['bookingId'],event_type)).fetchone():continue
                nid=secrets.token_hex(16)
                db.execute('INSERT INTO notifications VALUES(?,?,?,?,?,?,?,NULL)',(nid,uid,reservation['bookingId'],reservation['lockerId'],event_type,message,stamp))
                for sub in db.execute('SELECT subscription_id,channel FROM push_subscriptions WHERE user_id=? AND active=1',(uid,)):
                    db.execute('INSERT INTO notification_deliveries(notification_id,subscription_id,channel,state) VALUES(?,?,?,?)',(nid,sub[0],sub[1],'pending'))
                created.append(nid)
            return created
        return self.store.transaction(save)

    def history(self, identity, before=None):
        if before is not None:
            try:before=iso(parse_time(before))
            except Exception:raise ReservationError('Invalid notification cursor') from None
        uid=identity['user_id']
        with self.store.connect() as db:
            rows=db.execute('SELECT * FROM notifications WHERE user_id=? AND (? IS NULL OR occurred_at<?) ORDER BY occurred_at DESC,notification_id DESC LIMIT 51',(uid,before,before)).fetchall()
            # Cursor is time based; include full millisecond ties rather than losing records.
            if len(rows)>50:
                cutoff=rows[49]['occurred_at']
                rows=db.execute('SELECT * FROM notifications WHERE user_id=? AND (? IS NULL OR occurred_at<?) AND occurred_at>=? ORDER BY occurred_at DESC,notification_id DESC',(uid,before,before,cutoff)).fetchall()
                more=db.execute('SELECT 1 FROM notifications WHERE user_id=? AND occurred_at<? LIMIT 1',(uid,cutoff)).fetchone()
            else:more=False;cutoff=None
            unread=db.execute('SELECT count(*) FROM notifications WHERE user_id=? AND read_at IS NULL',(uid,)).fetchone()[0]
            subs=[dict(id=r[0],channel=r[1]) for r in db.execute('SELECT subscription_id,channel FROM push_subscriptions WHERE user_id=? AND session_hash=? AND active=1',(uid,identity['token_hash']))]
        return dict(notifications=[dict(notificationId=r['notification_id'],bookingId=r['booking_id'],lockerId=r['locker_id'],eventType=r['event_type'],message=r['message'],timestamp=r['occurred_at'],read=bool(r['read_at'])) for r in rows],unreadCount=unread,nextCursor=cutoff if more else None,push=dict(enabled=self.config.PUSH_ENABLED,publicKey=self.config.VAPID_PUBLIC_KEY if self.config.PUSH_ENABLED else '',subscriptions=subs))

    def mark_read(self, uid, nid):
        with self.store.connect() as db:
            if not db.execute('SELECT 1 FROM notifications WHERE notification_id=? AND user_id=?',(nid,uid)).fetchone():raise ReservationError('Notification not found',404)
            db.execute('UPDATE notifications SET read_at=COALESCE(read_at,?) WHERE notification_id=? AND user_id=?',(iso(datetime.fromtimestamp(self.clock(),timezone.utc)),nid,uid))

    @staticmethod
    def validate_subscription(body):
        from cryptography.hazmat.primitives.asymmetric import ec
        try:
            endpoint=body['endpoint'];parts=urlsplit(endpoint)
            host=parts.hostname or ''
            allowed=host in ('fcm.googleapis.com','updates.push.services.mozilla.com','web.push.apple.com') or host.endswith('.notify.windows.com')
            if not isinstance(endpoint,str) or len(endpoint)>2048 or parts.scheme!='https' or not allowed or parts.username or parts.password or parts.port not in (None,443) or parts.fragment:raise ValueError()
            keys=body['keys'];ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),decode_key(keys['p256dh'],65));decode_key(keys['auth'],16)
            return dict(endpoint=endpoint,keys=dict(p256dh=keys['p256dh'],auth=keys['auth']))
        except Exception:raise ReservationError('Invalid or unsupported push subscription') from None

    def subscribe(self, identity, body):
        if not self.config.PUSH_ENABLED:raise ReservationError('Push delivery is not configured; notification history remains available',503)
        info=self.validate_subscription(body);sid=hashlib.sha256(info['endpoint'].encode()).hexdigest();uid=identity['user_id']
        def save(db):
            row=db.execute('SELECT * FROM push_subscriptions WHERE subscription_id=?',(sid,)).fetchone()
            if row and row['user_id']!=uid:raise ReservationError('Subscription belongs to another account; reset this browser subscription first',409)
            db.execute('UPDATE push_subscriptions SET active=0 WHERE user_id=? AND session_hash NOT IN (SELECT token_hash FROM sessions WHERE expires>?)',(uid,self.clock()))
            if not row and db.execute('SELECT count(*) FROM push_subscriptions WHERE user_id=? AND active=1',(uid,)).fetchone()[0]>=10:raise ReservationError('Too many active notification devices',409)
            channel=row['channel'] if row and row['active'] and row['session_hash']==identity['token_hash'] and json.loads(row['body'])==info else secrets.token_urlsafe(24)
            db.execute('INSERT INTO push_subscriptions VALUES(?,?,?,?,?,1,?) ON CONFLICT(subscription_id) DO UPDATE SET session_hash=excluded.session_hash,channel=excluded.channel,body=excluded.body,active=1,updated_at=excluded.updated_at',(sid,uid,identity['token_hash'],channel,json.dumps(info),self.clock()))
            return dict(id=sid,channel=channel)
        return self.store.transaction(save)

    def remove(self, uid, sid):
        with self.store.connect() as db:
            row=db.execute('SELECT user_id FROM push_subscriptions WHERE subscription_id=?',(sid,)).fetchone()
            if row and row[0]!=uid:raise ReservationError('Subscription not found',404)
            db.execute('UPDATE push_subscriptions SET active=0 WHERE subscription_id=? AND user_id=?',(sid,uid))

    def revoke_session(self, session_hash):
        with self.store.connect() as db:db.execute('UPDATE push_subscriptions SET active=0 WHERE session_hash=?',(session_hash,))

    def send(self, subscription, payload):
        from pywebpush import webpush
        import requests
        # Trusted push-provider destinations only; never follow redirects to arbitrary hosts.
        class Transport(requests.Session):
            def request(self,*args,**kwargs):
                kwargs['allow_redirects']=False
                return super().request(*args,**kwargs)
        headers={'Urgency':'high','Content-Type':'application/octet-stream'}
        if (urlsplit(subscription['endpoint']).hostname or '').endswith('.notify.windows.com'):
            headers['X-WNS-Type']='wns/raw'
        with Transport() as session:
            session.trust_env=False
            return webpush(subscription_info=subscription,data=json.dumps(payload),vapid_private_key=self.config.VAPID_PRIVATE_KEY,vapid_claims={'sub':self.config.VAPID_SUBJECT},ttl=86400,timeout=8,requests_session=session,headers=headers)

    def dispatch(self, limit=20):
        if not self.config.PUSH_ENABLED or not self.dispatch_lock.acquire(blocking=False):return
        try:
            for _ in range(limit):
                now=self.clock()
                def claim(db):
                    row=db.execute("SELECT d.*,n.user_id,n.locker_id,n.event_type,n.occurred_at,s.body,s.active,s.channel AS current_channel,s.session_hash FROM notification_deliveries d JOIN notifications n USING(notification_id) JOIN push_subscriptions s USING(subscription_id) WHERE d.state='pending' AND d.next_attempt<=? ORDER BY n.occurred_at LIMIT 1",(now,)).fetchone()
                    if not row:return None
                    row=dict(row)
                    valid=db.execute("SELECT 1 FROM sessions WHERE token_hash=? AND user_id=? AND kind='user' AND expires>?",(row['session_hash'],row['user_id'],now)).fetchone()
                    if not row['active'] or row['channel']!=row['current_channel'] or not valid or now-parse_time(row['occurred_at']).timestamp()>86400:
                        db.execute("UPDATE notification_deliveries SET state='cancelled' WHERE notification_id=? AND subscription_id=?",(row['notification_id'],row['subscription_id']));return {}
                    db.execute('UPDATE notification_deliveries SET attempts=attempts+1,next_attempt=? WHERE notification_id=? AND subscription_id=?',(now+120,row['notification_id'],row['subscription_id']))
                    return row
                row=self.store.transaction(claim)
                if row is None:break
                if not row:continue
                payload=dict(notificationId=row['notification_id'],lockerId=row['locker_id'],eventType=row['event_type'],timestamp=row['occurred_at'],channel=row['channel'])
                code=0
                try:
                    response=self.sender(json.loads(row['body']),payload);code=getattr(response,'status_code',201)
                except Exception as exc:
                    response=getattr(exc,'response',None);code=getattr(response,'status_code',0) or getattr(exc,'status_code',0) or 0
                    log.warning('Web Push delivery deferred (HTTP %s)',code) # Never log endpoints, keys or payloads.
                state='sent' if 200<=code<300 else 'failed' if code in (400,404,410,413) or row['attempts']>=7 else 'pending'
                def finish(db):
                    db.execute('UPDATE notification_deliveries SET state=?,last_status=?,next_attempt=? WHERE notification_id=? AND subscription_id=?',(state,code,self.clock()+min(3600,5*2**row['attempts']),row['notification_id'],row['subscription_id']))
                    if code in (404,410):db.execute('UPDATE push_subscriptions SET active=0 WHERE subscription_id=? AND channel=?',(row['subscription_id'],row['channel']))
                self.store.transaction(finish)
        finally:self.dispatch_lock.release()
