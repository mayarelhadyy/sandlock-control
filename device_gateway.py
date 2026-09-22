"""Credential-isolating transport. Existing wire contracts, no generic publish API."""
import json
import math
import threading
import time
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from reservation_service import ReservationError,parse_time,OPEN
from security import identifier,bounded_text
from device_store import DeviceStore
import sqlite3
import uuid
import application_events

class DeviceGateway:
    def __init__(self, publish, enabled, base_topic):
        self.publish,self.enabled,self.base=publish,enabled,base_topic
        self.pending={};self.lock=threading.RLock();self.journal=None;self.journal_path=None

    def bind(self,service):
        if self.journal_path!=service.store.path:
            self.journal=DeviceStore(service.store.path);self.journal_path=service.store.path
            self.pending={}

    def command(self,service,identity,booking_id,payload):
        self.bind(service);req=payload['requestId']
        with self.lock:
            self.expire()
            try:self.journal.start(req,identity,booking_id)
            except sqlite3.IntegrityError:raise ReservationError('Command request already exists',409)
            self.pending[req]={'session':identity['token_hash'],'bookingId':booking_id,'expires':time.monotonic()+7,'ack':None}
        try:self.send('cmd/unlock',payload)
        except Exception:
            with self.lock:
                self.journal.outcome(req,'unknown');self.pending.pop(req,None)
            raise
        self.journal.outcome(req,'published')
        return {'published':True,'mqttPublished':True,'requestId':req,'outcome':'published','physicalConfirmed':False}

    def send(self,suffix,payload,retain=False):
        if not self.enabled():raise ReservationError('Device transport is not configured',503)
        if not self.publish(self.base+'/'+suffix,payload,retain):raise ReservationError('Device publication is unconfirmed',503)

    @staticmethod
    def zone(body):
        try:
            name=body.get('timezone','UTC')
            if not isinstance(name,str) or len(name)>80:raise ValueError()
            return ZoneInfo(name)
        except Exception:raise ReservationError('Invalid timezone')

    def action(self,service,identity,booking_id,action,body):
        if not identifier(booking_id) or not isinstance(body,dict):raise ReservationError('Invalid device request')
        allowed={'set':{'timezone'},'time':{'timezone'},'clear':set(),'unlock':{'requestId','method','pin'}}
        if action not in allowed or set(body)-allowed[action]:raise ReservationError('Unsupported device request')
        # Serialize authorization/state check with publication so terminalization or
        # reassignment cannot occur between validating and sending a command.
        def operation(db):
            r=service.authorize(db,booking_id,identity['user_id'])
            if r['lockerId']!='A':raise ReservationError('No physical device configured for this locker',409)
            now=service.clock();stamp=now.isoformat(timespec='milliseconds').replace('+00:00','Z')
            start,end=parse_time(r['startTime']),parse_time(r['endTime'])
            if action=='clear':
                if r['status'] not in ('Completed','Cancelled'):raise ReservationError('Reservation is not terminal',409)
                if any(x['lockerId']==r['lockerId'] and x['bookingId']!=booking_id and x['status'] in OPEN for x in service.store.rows(db)):
                    raise ReservationError('Locker has another open reservation',409)
                self.send('reservation/clear',{'bookingId':booking_id,'timestamp':stamp})
                self.send('reservation/set','',True);return {'published':True}
            if r['status'] not in OPEN:raise ReservationError('Reservation is terminal',409)
            if action=='unlock':
                if now<start:raise ReservationError('Reservation has not started',409)
                method=body.get('method');req=body.get('requestId')
                if method not in ('Mobile','Reservation PIN') or not identifier(req):raise ReservationError('Invalid unlock request')
                if method=='Reservation PIN' and body.get('pin')!=r['pin']:raise ReservationError('Incorrect reservation PIN',403)
                payload=dict(requestId=req,bookingId=booking_id,locker=r['lockerId'],source=method.lower().replace(' ','-'),reservationPinVerified=method=='Reservation PIN',timestamp=stamp,epochMs=int(now.timestamp()*1000))
                return self.command(service,identity,booking_id,payload)
            zone=self.zone(body);local=now.astimezone(zone)
            if action=='set':db.execute('UPDATE device_intents SET timezone=?,done=0 WHERE booking_id=?',(str(zone),booking_id))
            if action=='set':
                self.send('reservation/set',dict(bookingId=booking_id,pin=r['pin'],startEpoch=int(start.timestamp()),endEpoch=int(end.timestamp()),startIso=r['startTime'],endIso=r['endTime'],localDate=start.astimezone(zone).strftime('%Y-%m-%d'),localStart=start.astimezone(zone).strftime('%H:%M'),localEnd=end.astimezone(zone).strftime('%H:%M'),reminderMinutes=15,lateFeeMultiplier=3,issuedAt=stamp),True)
            self.send('time/set',dict(source='mobile-browser',epochMs=int(now.timestamp()*1000),epochSeconds=int(now.timestamp()),isoUtc=stamp,localDate=local.strftime('%Y-%m-%d'),localTime=local.strftime('%H:%M:%S'),timezoneOffsetMinutes=-int(local.utcoffset().total_seconds()/60),timezone=str(zone)),True)
            return {'published':True}
        if action=='set':
            zone=self.zone(body)
            def remember(db):
                r=service.authorize(db,booking_id,identity['user_id'])
                if r['lockerId']!='A' or r['status'] not in OPEN:raise ReservationError('Reservation cannot be provisioned',409)
                db.execute('UPDATE device_intents SET timezone=? WHERE booking_id=?',(str(zone),booking_id))
            service.store.transaction(remember)
        try:return service.store.transaction(operation)
        except ReservationError as exc:
            if action=='unlock' and exc.code==403:
                self.bind(service)
                req=uuid.uuid4().hex
                self.journal.start(req,identity,booking_id);self.journal.outcome(req,'rejected-pin')
            raise

    def reconcile(self,service):
        if not self.enabled():return
        def operation(db):
            for item in db.execute('SELECT * FROM device_intents WHERE done=0').fetchall():
                r=service.store.get(db,item['booking_id'])
                if not r or r['revision']!=item['revision']:continue
                other=any(x['lockerId']=='A' and x['bookingId']!=r['bookingId'] and x['status'] in OPEN for x in service.store.rows(db))
                if r['status'] in ('Cancelled','Completed'):
                    if other:
                        db.execute('UPDATE device_intents SET done=1 WHERE booking_id=?',(r['bookingId'],));continue
                    self.send('reservation/clear',{'bookingId':r['bookingId'],'timestamp':service.clock().isoformat()})
                    self.send('reservation/set','',True)
                elif r['status'] in OPEN:
                    if not item['timezone']:continue
                    zone=ZoneInfo(item['timezone']);start,end=parse_time(r['startTime']),parse_time(r['endTime'])
                    self.send('reservation/set',dict(bookingId=r['bookingId'],pin=r['pin'],startEpoch=int(start.timestamp()),endEpoch=int(end.timestamp()),startIso=r['startTime'],endIso=r['endTime'],localDate=start.astimezone(zone).strftime('%Y-%m-%d'),localStart=start.astimezone(zone).strftime('%H:%M'),localEnd=end.astimezone(zone).strftime('%H:%M'),reminderMinutes=15,lateFeeMultiplier=3,issuedAt=service.clock().isoformat()),True)
                    now=service.clock();local=now.astimezone(zone)
                    self.send('time/set',dict(source='mobile-browser',epochMs=int(now.timestamp()*1000),epochSeconds=int(now.timestamp()),isoUtc=now.isoformat(),localDate=local.strftime('%Y-%m-%d'),localTime=local.strftime('%H:%M:%S'),timezoneOffsetMinutes=-int(local.utcoffset().total_seconds()/60),timezone=str(zone)),True)
                db.execute('UPDATE device_intents SET done=1 WHERE booking_id=?',(r['bookingId'],))
        service.store.transaction(operation)

    def expire(self):
        if self.journal:self.journal.expire()
        for key in list(self.pending):
            if self.pending[key]['expires']+60<time.monotonic():
                del self.pending[key];continue
            if self.pending[key]['expires']<time.monotonic() and self.pending[key]['ack'] is None:
                self.pending[key]['ack']={'requestId':key,'success':False,'message':'Command outcome unknown'}

    def receive_ack(self,payload,retained=False):
        if retained or not isinstance(payload,dict):return
        if payload.get('requestId') and payload.get('id') and payload['requestId']!=payload['id']:return
        values=[payload[k] for k in ('success','ok') if k in payload]
        if not values or any(type(v) is not bool for v in values) or len(set(values))!=1:return
        req=payload.get('requestId') or payload.get('id')
        if not identifier(req):return  # Cannot relay an unowned packet across authenticated sessions.
        with self.lock:
            self.expire()
            if req in self.pending:
                item=self.pending[req]
                if item['ack'] is not None:return
                if payload.get('locker','A')!='A' or payload.get('bookingId',item['bookingId'])!=item['bookingId']:return
                if self.journal:self.journal.outcome(req,'acknowledged' if values[0] else 'rejected')
                self.pending[req]['ack']={k:v for k,v in payload.items() if k in ('requestId','id','success','ok') and isinstance(v,(str,bool))}
                if payload.get('success') is False or payload.get('ok') is False:self.pending[req]['ack']['message']='Locker rejected the command'

    def acks(self,identity):
        with self.lock:
            self.expire();result=[]
            for key,item in list(self.pending.items()):
                if item['session']==identity['token_hash'] and item['ack'] is not None:
                    result.append(item['ack']);del self.pending[key]
            return result

    def event(self,service,identity,kind,body):
        if not isinstance(body,dict) or kind not in ('payment','access','feedback','user-upsert'):raise ReservationError('Unsupported event')
        if kind=='payment':
            from payment_service import compatible
            return compatible(service,identity,body)
        if kind=='user-upsert':
            last=body.get('paymentLast4','')
            if not isinstance(last,str) or (last and (len(last)!=4 or not last.isdigit())):raise ReservationError('Invalid profile event')
            data=dict(userId='USR-'+identity['mobile'][-6:],name=identity['name'],mobile=identity['mobile'],paymentLast4=last,status='active');suffix='user/upsert'
        else:
            bid=body.get('bookingId')
            if not identifier(bid):raise ReservationError('Invalid bookingId')
            r=service.get(bid,actor=identity['user_id'])
            data=dict(bookingId=bid,ownerMobile=r.get('ownerMobile',''),timestamp=datetime.now(timezone.utc).isoformat())
            suffix=kind
            if kind=='access':
                if body.get('method') not in ('Mobile','Reservation PIN'):raise ReservationError('Invalid access method')
                data.update(locker=r['lockerId'],method=body['method'],action='unlock',result='client-reported-attempt',door='unknown',source='user-app')
            else:
                rating=body.get('rating');comment=body.get('comment','')
                if type(rating)!=int or not 1<=rating<=5 or not bounded_text(comment,2000):raise ReservationError('Invalid feedback event')
                data.update(rating=rating,comment=comment)
        clean={k:v for k,v in data.items() if k not in ('timestamp','emittedAt')}
        event_id,created=application_events.record(service.store,identity['user_id'],'profile' if kind=='user-upsert' else kind,clean,body.get('eventId'))
        return {'accepted':True,'eventId':event_id,'created':created}

