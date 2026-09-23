from decimal import Decimal
import payment_service as finance
import copy
import json
import math
import re
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from reservation_store import ReservationStore

OPEN = {'Confirmed', 'Active', 'Overdue'}
TERMINAL = {'Cancelled', 'Completed'}
STATUSES = {'pending':'Pending','reserved':'Confirmed','confirmed':'Confirmed',
            'active':'Active','overdue':'Overdue','completed':'Completed',
            'cancelled':'Cancelled','canceled':'Cancelled'}
LOCKERS = {'A':20, 'B':18, 'C':22, 'D':20, 'E':16, 'F':16}
# Preserve the existing fleet's demo/unavailable defaults.
DEFAULT_AVAILABILITY = dict(A='available', B='available', C='reserved', D='occupied', E='offline', F='offline')


class ReservationError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def parse_time(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise ReservationError('An explicit timezone is required for reservation timestamps')


def iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def user_key(name, mobile):
    text = ' '.join(str(name).strip().lower().split()) + '|' + ''.join(c for c in str(mobile) if c.isdigit())
    h = 1469598103934665603
    for c in text:
        h = ((h ^ ord(c)) * 1099511628211) & ((1 << 64) - 1)
    chars = '0123456789abcdefghijklmnopqrstuvwxyz'
    out = ''
    while h:
        h, rem = divmod(h, 36)
        out = chars[rem] + out
    return 'u_' + (out or '0')


def normalize_legacy(raw, timezone_name=None):
    if not isinstance(raw, dict):
        raise ReservationError('Reservation must be an object')
    r = copy.deepcopy(raw)
    old_id = r.get('id')
    if old_id and r.get('bookingId') and old_id != r['bookingId']:
        raise ReservationError('Conflicting id and bookingId', 409)
    r['bookingId'] = r.get('bookingId') or old_id or r.get('Booking ID')
    r['lockerId'] = r.get('lockerId') or r.get('locker') or r.get('Locker')
    r['ownerName'] = r.get('ownerName') or r.get('User Name') or ''
    r['ownerMobile'] = r.get('ownerMobile') or r.get('User Mobile') or ''
    r['userId'] = r.get('userId') or r.get('ownerKey')
    if not r['userId'] and r['ownerName'] and r['ownerMobile']:
        r['userId'] = user_key(r['ownerName'], r['ownerMobile'])
    r['pin'] = r.get('pin') if 'pin' in r else r.get('PIN')
    status = str(r.get('status') or r.get('Status') or '').lower()
    if status not in STATUSES:
        raise ReservationError('Unknown or missing legacy status')
    r['status'] = STATUSES[status]
    for target, old, excel, local, excel_local in [('startTime','startIso','Start ISO','from','Start Time'),('endTime','endIso','End ISO','to','End Time')]:
        value = r.get(target) or r.get(old) or r.get(excel)
        if not value:
            if not timezone_name:
                raise ReservationError('Legacy local times require an explicit timezone')
            try:
                naive = datetime.fromisoformat(f"{r.get('date') or r.get('Date')}T{r.get(local) or r.get(excel_local)}")
                zone = ZoneInfo(timezone_name)
                aware = naive.replace(tzinfo=zone)
                if aware.replace(fold=0).utcoffset() != aware.replace(fold=1).utcoffset():
                    raise ValueError('Ambiguous daylight-saving time')
                if aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != naive:
                    raise ValueError('Nonexistent local time')
                value = iso(aware)
            except Exception:
                raise ReservationError('Legacy schedule cannot be resolved unambiguously')
        r[target] = iso(parse_time(value))
    for key, excel in [('createdAt','Created At'),('updatedAt','Updated At'),('completedAt','Completed At'),('cancelledAt','Cancelled At'),('hourlyRate','Hourly Rate'),('total','Base Amount'),('lateFee','Late Fee'),('finalTotal','Final Total'),('paymentStatus','Payment Status')]:
        if key not in r and r.get(excel) is not None:
            r[key] = r[excel]
    r.pop('id', None)
    return r


class ReservationService:
    def __init__(self, path, clock=None):
        self.store = ReservationStore(path)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _reconcile(self, db):
        now = self.clock()
        for r in self.store.rows(db):
            if r['status'] not in OPEN:
                continue
            phase = 'Confirmed' if now < parse_time(r['startTime']) else 'Active' if now < parse_time(r['endTime']) else 'Overdue'
            # Clock corrections must not move an accepted lifecycle backwards.
            if {'Confirmed':0,'Active':1,'Overdue':2}[phase] > {'Confirmed':0,'Active':1,'Overdue':2}[r['status']]:
                r.update(status=phase, updatedAt=iso(now), revision=r['revision']+1)
                self.store.save(db, r, 'clock', 'backend')

    @staticmethod
    def owner(db, user_id):
        row=db.execute('SELECT user_id FROM legacy_owners WHERE legacy_id=?',(user_id,)).fetchone()
        return row[0] if row else user_id

    def authorize(self, db, booking_id, actor):
        r=self.store.get(db,booking_id)
        if not r or (actor and self.owner(db,r['userId'])!=actor):
            raise ReservationError('Reservation not found',404)
        return r

    def list(self, user_id=None):
        def operation(db):
            self._reconcile(db)
            rows = self.store.rows(db)
            return sorted((finance.view(r,self.clock()) for r in rows if user_id is None or self.owner(db,r['userId'])==user_id), key=lambda r:r['createdAt'], reverse=True)
        return self.store.transaction(operation)

    def get(self, booking_id, actor=None):
        def operation(db):
            self.authorize(db,booking_id,actor)
            self._reconcile(db)
            r = self.store.get(db, booking_id)
            if not r:
                raise ReservationError('Reservation not found', 404)
            return finance.view(r,self.clock())
        return self.store.transaction(operation)

    def create(self, raw, legacy=False, timezone_name=None, source='user'):
        data = normalize_legacy(raw, timezone_name) if legacy else copy.deepcopy(raw)
        if not isinstance(data, dict):
            raise ReservationError('Reservation must be an object')
        for key in ('bookingId','userId','lockerId'):
            if not isinstance(data.get(key), str) or not data[key].strip():
                raise ReservationError(f'Missing or invalid {key}')
            if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',data[key]):
                raise ReservationError(f'Invalid {key} format')
        for key in ('ownerName','ownerMobile','zone','note'):
            if key in data and not isinstance(data[key],str):
                raise ReservationError(f'Invalid {key}')
        if 'id' in data and data['id'] != data['bookingId']:
            raise ReservationError('Conflicting id and bookingId', 409)
        if data['lockerId'] not in LOCKERS:
            raise ReservationError('Invalid lockerId')
        pin = data.get('pin')
        if not isinstance(pin, str) or len(pin)!=4 or not pin.isascii() or not pin.isdigit() or pin=='0000':
            raise ReservationError('PIN must be four digits other than 0000')
        start, end = parse_time(data.get('startTime')), parse_time(data.get('endTime'))
        if end <= start:
            raise ReservationError('End time must follow start time')
        for key in ('hourlyRate','total','paidAmount','lateFee','finalTotal'):
            if key in data:finance.money(data[key])
        def operation(db):
            self._reconcile(db)
            existing = self.store.get(db, data['bookingId'])
            if existing:
                comparisons = {**data, 'startTime':iso(start),'endTime':iso(end)}
                if any(existing[k] != comparisons[k] for k in ('userId','lockerId','startTime','endTime','pin')):
                    raise ReservationError('bookingId already exists with different creation data', 409)
                return existing, False
            now = self.clock()
            status = data.get('status','Confirmed') if legacy else 'Confirmed'
            if status=='Pending':
                raise ReservationError('Pending drafts are not accepted reservations')
            if not legacy and (start < now.replace(second=0,microsecond=0) or end > now+timedelta(hours=24)):
                raise ReservationError('Reservation must be within the next 24 hours')
            if not legacy and data.get('status','Pending') not in ('Pending','Confirmed'):
                raise ReservationError('Creation cannot set lifecycle state')
            if status in OPEN:
                for r in self.store.rows(db):
                    if r['status'] in OPEN and (r['lockerId']==data['lockerId'] or self.owner(db,r['userId'])==self.owner(db,data['userId'])):
                        raise ReservationError('Locker or user already has an open reservation', 409)
                if not legacy and DEFAULT_AVAILABILITY[data['lockerId']]!='available':
                    raise ReservationError('Locker is unavailable', 409)
                status = 'Confirmed' if now<start else 'Active' if now<end else 'Overdue'
            rate = LOCKERS[data['lockerId']]
            total = finance.amount_for(start,end,rate)
            # Existing display/financial history survives explicit legacy import.
            preserved = {k:copy.deepcopy(v) for k,v in data.items() if k in ('ownerName','ownerMobile','zone','note','paymentStatus','paidAmount','lateFee','finalTotal','completedAt','cancelledAt','reminderSent','reminderSentAt','refundStatus')}
            if not legacy:preserved={k:v for k,v in preserved.items() if k in ('ownerName','ownerMobile','zone','note')}
            value = dict(preserved,bookingId=data['bookingId'],userId=data['userId'],lockerId=data['lockerId'],status=status,
                         startTime=iso(start),endTime=iso(end),pin=pin,createdAt=iso(parse_time(data['createdAt'])) if legacy and data.get('createdAt') else iso(now),
                         updatedAt=iso(now),revision=1,hourlyRate=data.get('hourlyRate',rate) if legacy else rate,
                         total=data.get('total',total) if legacy else total)
            for key in ('hourlyRate','total','lateFee','finalTotal','paidAmount'):
                if key in value:
                    try:
                        value[key]=float(finance.money(value[key]))
                        if not math.isfinite(value[key]) or value[key]<0:raise ValueError()
                    except (ValueError,TypeError):raise ReservationError(f'Invalid {key}')
            if not legacy:
                value.update(financialVersion=1,financialStatus='demo-only',paymentStatus='demo-only',lateFee=0)
                finance.accept(db,value,'base',value['total'])
            self.store.save(db,value,'import' if legacy else 'create',source)
            return value, True
        return self.store.transaction(operation)

    def transition(self, booking_id, action, expected_revision=None, source='user', actor=None, admin_actor=None):
        if action not in ('cancel','complete'):
            raise ReservationError('Unknown reservation action')
        target = 'Cancelled' if action=='cancel' else 'Completed'
        def operation(db):
            self.authorize(db,booking_id,actor)
            self._reconcile(db)
            r = self.store.get(db, booking_id)
            if not r:raise ReservationError('Reservation not found',404)
            if r['status']==target:return r,False
            if r['status'] in TERMINAL:raise ReservationError('Reservation is already terminal',409)
            if not isinstance(expected_revision,int) or isinstance(expected_revision,bool):raise ReservationError('expectedRevision is required')
            if expected_revision != r['revision']:raise ReservationError('Reservation changed; refresh and retry',409)
            now=self.clock()
            # A user may cancel before or during a session. Before start, the demo
            # booking value is zeroed; once the session has started, the base value
            # remains and any applicable late assessment is finalized.
            if action=='cancel' and source!='admin' and r['status'] not in ('Confirmed','Active','Overdue'):
                raise ReservationError('This reservation can no longer be cancelled',409)
            if action=='complete' and r['status'] not in ('Active','Overdue'):
                raise ReservationError('Only an active or overdue reservation can be completed',409)
            r.update(status=target,updatedAt=iso(now),revision=r['revision']+1)
            r['cancelledAt' if action=='cancel' else 'completedAt']=iso(now)
            if action=='cancel':
                before=now<parse_time(r['startTime'])
                if before:
                    r.update(lateFee=0,finalTotal=0,financialStatus='cancelled-zero',refundStatus='not-supported')
                else:
                    fee=finance.late_fee_for(parse_time(r['endTime']),now,r['hourlyRate'],3,10)
                    r.update(lateFee=fee,finalTotal=float(finance.money(Decimal(str(r['total']))+Decimal(str(fee)))),financialStatus='demo-final',refundStatus='not-supported')
                    if r.get('financialVersion')==1:finance.accept(db,r,'late',fee)
            else:
                fee=finance.late_fee_for(parse_time(r['endTime']),now,r['hourlyRate'],3,10)
                r.update(lateFee=fee,finalTotal=float(finance.money(Decimal(str(r['total']))+Decimal(str(fee)))))
                if r.get('financialVersion')==1:
                    r['financialStatus']='demo-final'
                    finance.accept(db,r,'late',fee)
            if admin_actor:r['actionActor']=admin_actor
            db.execute('UPDATE financial_events SET projected=0,projection_version=projection_version+1 WHERE booking_id=?',(booking_id,))
            self.store.save(db,r,action,source)
            return r,True
        return self.store.transaction(operation)

    def extend(self, booking_id, expected_revision=None, actor=None, minutes=60):
        if minutes != 60:raise ReservationError('Only a one-hour extension is supported')
        def operation(db):
            self.authorize(db,booking_id,actor)
            self._reconcile(db)
            r=self.store.get(db,booking_id)
            if not r:raise ReservationError('Reservation not found',404)
            if r['status'] not in ('Active','Overdue'):raise ReservationError('Only an active reservation can be extended',409)
            if not isinstance(expected_revision,int) or isinstance(expected_revision,bool):raise ReservationError('expectedRevision is required')
            if expected_revision!=r['revision']:raise ReservationError('Reservation changed; refresh and retry',409)
            now=self.clock();end=parse_time(r['endTime'])
            if now>end+timedelta(minutes=10):raise ReservationError('The extension grace period has ended',409)
            if int(r.get('extensionCount',0))>=1:raise ReservationError('This reservation has already been extended once',409)
            new_end=end+timedelta(hours=1)
            r.update(endTime=iso(new_end),status='Active',updatedAt=iso(now),revision=r['revision']+1,lateFee=0,extensionCount=1)
            r['total']=finance.amount_for(parse_time(r['startTime']),new_end,r['hourlyRate'])
            r.pop('finalTotal',None)
            if r.get('financialVersion')==1:finance.accept(db,r,'extension',r['hourlyRate'])
            self.store.save(db,r,'extend','user')
            return r,True
        return self.store.transaction(operation)

    def availability(self):
        rows=self.list()
        result=[]
        for locker,default in DEFAULT_AVAILABILITY.items():
            current=next((r for r in rows if r['lockerId']==locker and r['status'] in OPEN),None)
            result.append(dict(lockerId=locker,status=('reserved' if current['status']=='Confirmed' else 'occupied') if current else default,
                               bookingId=current['bookingId'] if current else None))
        return result


def excel_record(r):
    # Keep canonical timestamps in UTC, but make owner-facing report fields readable
    # in the operating timezone. Start ISO / End ISO remain canonical UTC values.
    zone=ZoneInfo('Africa/Cairo')
    start,end=parse_time(r['startTime']),parse_time(r['endTime'])
    local_start,local_end=start.astimezone(zone),end.astimezone(zone)
    return {'Booking ID':r['bookingId'],'Locker':r['lockerId'],'User Mobile':r.get('ownerMobile',''),
            'User Name':r.get('ownerName',''),'Date':local_start.strftime('%d %b %Y'),'Start Time':local_start.strftime('%I:%M %p'),
            'End Time':local_end.strftime('%I:%M %p'),'Start ISO':r['startTime'],'End ISO':r['endTime'],
            'Hourly Rate':r.get('hourlyRate',0),'Base Amount':r.get('total',0),'Late Fee':r.get('lateFee',0),
            'Final Total':r.get('finalTotal',0),'Financial Outcome':r.get('financialStatus','legacy-review'),'Status':r['status'],'Payment Status':r.get('paymentStatus',''),
            'Created At':r['createdAt'],'Completed At':r.get('completedAt',''),'Cancelled At':r.get('cancelledAt',''),
            'User ID':r['userId'],'Updated At':r['updatedAt'],'Revision':r['revision']}
