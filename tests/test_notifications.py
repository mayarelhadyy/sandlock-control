import unittest, secrets, json, base64, time
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime, timezone, timedelta
import test_integration as base
from notifications import Notifications,public_key
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
s=base.s

def b64(value):return base64.urlsafe_b64encode(value).decode().rstrip('=')
def configuration():
 private=b64(ec.generate_private_key(ec.SECP256R1()).private_numbers().private_value.to_bytes(32,'big'))
 return SimpleNamespace(PUSH_ENABLED=True,VAPID_PRIVATE_KEY=private,VAPID_PUBLIC_KEY=public_key(private),VAPID_SUBJECT='mailto:test@example.invalid')
def subscription():
 key=ec.generate_private_key(ec.SECP256R1())
 return dict(endpoint='https://fcm.googleapis.com/fcm/send/'+secrets.token_hex(16),keys=dict(p256dh=b64(key.public_key().public_bytes(Encoding.X962,PublicFormat.UncompressedPoint)),auth=b64(secrets.token_bytes(16))))

class NotificationTests(unittest.TestCase):
 def setUp(self):
  self.api=base.Integration();self.api.setUp();r=self.api.login_user();self.csrf=r.json['csrf'];self.uid=r.json['user']['userId'];self.ids=[];self.sent=[]
  self.original=s.notifications;self.cfg=configuration();self.n=Notifications(s.reservations,self.cfg,sender=lambda sub,payload:self.sent.append((sub,payload)))
  s.notifications=self.n
  with s.reservations.store.connect() as db:
   for table in ('notification_deliveries','push_subscriptions','notifications','locker_notification_state'):db.execute('DELETE FROM '+table)
 def tearDown(self):
  s.notifications=self.original
  with s.reservations.store.connect() as db:
   for bid in self.ids:db.execute('DELETE FROM reservations WHERE booking_id=?',(bid,))
 def call(self,path,method='GET',body=None,csrf=True):return self.api.request('/api/v1'+path,method,body,self.csrf if csrf else None)
 def identity(self):
  token=self.api.c.get_cookie(s.cookie_name('user'),domain='sandlock-control-production.up.railway.app').value
  return s.auth.resolve(token,'user')
 def book(self,locker='A'):
  now=datetime.now(timezone.utc);bid='N-'+secrets.token_hex(8);self.ids.append(bid)
  r=self.call('/reservations','POST',{'bookingId':bid,'lockerId':locker,'startTime':base.iso(now),'endTime':base.iso(now+timedelta(minutes=30)),'pin':'1234'})
  self.assertEqual(r.status_code,201,r.json);return r.json['reservation']
 def event(self,door,**extra):
  msg=SimpleNamespace(topic='sandlock/locker/A/door',payload=json.dumps(dict(door=door,**extra)).encode(),retain=False)
  s.on_message(None,None,msg)
 def history(self):return self.call('/notifications').json
 def enable(self):
  info=subscription();r=self.call('/push-subscriptions','POST',info);self.assertEqual(r.status_code,200,r.json);return info,r.json
 def test_01_door_owner_time_and_dispatch(self):
  booking=self.book();info,sub=self.enable();self.event('open',userId='forged',lockerId='B')
  history=self.history();self.assertEqual(history['unreadCount'],1);n=history['notifications'][0]
  self.assertEqual((n['lockerId'],n['bookingId'],n['eventType']),('A',booking['bookingId'],'door-opened'));self.assertIn(n['timestamp'],n['message']);self.assertTrue(n['timestamp'].endswith('Z'))
  self.n.dispatch();self.assertEqual(len(self.sent),1);payload=self.sent[0][1];self.assertEqual(payload['lockerId'],'A');self.assertEqual(payload['channel'],sub['channel']);self.assertNotIn('pin',payload);self.assertNotIn('userId',payload)
 def test_02_close_duplicates_separate_openings_restart(self):
  self.book();self.event('open');self.event('open');self.assertEqual(self.history()['unreadCount'],1)
  again=Notifications(s.reservations,self.cfg,sender=lambda *args:None);self.assertEqual(again.observe('A','open'),[])
  self.event('closed');self.event('closed');self.event('open');self.assertEqual(self.history()['unreadCount'],3)
 def test_03_multiple_legacy_records_route_b_without_rule_change(self):
  a=self.book();b={**a,'bookingId':'N-'+secrets.token_hex(8),'lockerId':'B'};self.ids.append(b['bookingId'])
  s.reservations.store.transaction(lambda db:s.reservations.store.save(db,b,'test-fixture','synthetic'))
  self.n.observe('B','open');n=self.history()['notifications'][0];self.assertEqual(n['lockerId'],'B');self.assertEqual(n['bookingId'],b['bookingId']);self.assertIn('Locker B',n['message']);self.assertNotIn('Locker A',n['message'])
 def test_04_cross_user_history_and_read(self):
  self.book();self.event('open');nid=self.history()['notifications'][0]['notificationId'];other=base.Integration();other.setUp();csrf=other.login_user().json['csrf']
  self.assertEqual(other.request('/api/v1/notifications').json['notifications'],[])
  self.assertEqual(other.request('/api/v1/notifications/'+nid+'/read','POST',{},csrf).status_code,404);self.assertEqual(self.history()['unreadCount'],1)
  self.assertEqual(self.call('/notifications/'+nid+'/read','POST',{}).status_code,200);self.assertEqual(self.history()['unreadCount'],0)
  self.assertEqual(self.call('/notifications/'+nid+'/read','POST',{}).status_code,200)
 def test_05_auth_csrf_and_revoked_access(self):
  anon=s.app.test_client();self.assertEqual(anon.get('/api/v1/notifications',base_url=base.BASE).status_code,401)
  self.assertEqual(self.call('/push-subscriptions','POST',subscription(),False).status_code,403)
  self.api.request('/auth/user/logout','POST',{},self.csrf);self.assertEqual(self.call('/notifications').status_code,401)
 def test_06_subscription_ownership_removal_and_logout(self):
  info,sub=self.enable();repeat=self.call('/push-subscriptions','POST',info);self.assertEqual(repeat.json,sub)
  other=base.Integration();other.setUp();csrf=other.login_user().json['csrf'];self.assertEqual(other.request('/api/v1/push-subscriptions','POST',info,csrf).status_code,409)
  self.assertEqual(other.request('/api/v1/push-subscriptions/remove','POST',{'id':sub['id']},csrf).status_code,404)
  self.book();self.event('open');self.api.request('/auth/user/logout','POST',{},self.csrf);self.n.dispatch();self.assertEqual(self.sent,[])
 def test_07_expired_subscription_deactivated_history_kept(self):
  self.book();self.enable();self.event('open');self.n.sender=lambda *args:SimpleNamespace(status_code=410);self.n.dispatch()
  self.assertEqual(self.history()['unreadCount'],1);self.assertEqual(self.history()['push']['subscriptions'],[])
 def test_08_provider_failure_retry_after_restart(self):
  self.book();self.enable();self.event('open');self.n.sender=lambda *args:SimpleNamespace(status_code=503);self.n.dispatch();self.assertEqual(self.history()['unreadCount'],1)
  again=Notifications(s.reservations,self.cfg,sender=lambda a,b:self.sent.append(b));again.clock=lambda:time.time()+10;again.dispatch();self.assertEqual(len(self.sent),1);again.dispatch();self.assertEqual(len(self.sent),1)
 def test_09_expired_session_does_not_deliver(self):
  self.book();self.enable();self.event('open')
  with s.reservations.store.connect() as db:db.execute('UPDATE sessions SET expires=0 WHERE token_hash=?',(self.identity()['token_hash'],))
  self.n.dispatch();self.assertEqual(self.sent,[])
 def test_10_retained_unknown_closed_baseline_no_recipient(self):
  self.assertEqual(self.n.observe('A','open'),[]);self.book();self.assertEqual(self.n.observe('A','open',retained=True),[]);self.assertEqual(self.n.observe('Z','open'),[]);self.assertEqual(self.n.observe('A','unlocked'),[])
  with s.reservations.store.connect() as db:db.execute('DELETE FROM locker_notification_state')
  self.assertEqual(self.n.observe('A','closed'),[]);self.assertEqual(self.history()['unreadCount'],0)
 def test_11_notification_failure_preserves_device_processing(self):
  self.book()
  with patch.object(self.n,'observe',side_effect=OSError('synthetic')):self.event('open')
  self.assertEqual(s.state['locker']['door'],'open')
 def test_12_no_provider_or_private_key_leak(self):
  self.assertNotIn(self.cfg.VAPID_PRIVATE_KEY,json.dumps(self.history()))
  for endpoint in ('http://127.0.0.1/x','https://127.0.0.1/x','https://fcm.googleapis.com.evil.invalid/x','https://fcm.googleapis.com@evil.invalid/x'):
   data=subscription();data['endpoint']=endpoint;self.assertEqual(self.call('/push-subscriptions','POST',data).status_code,400)
  data=subscription();data['keys']['auth']='bad';self.assertEqual(self.call('/push-subscriptions','POST',data).status_code,400)
 def test_13_additive_migration_idempotent_without_initialization(self):
  import os
  b=self.book()
  with s.reservations.store.connect() as db:
   for table in ('notification_deliveries','push_subscriptions','notifications','locker_notification_state'):db.execute('DROP TABLE '+table)
  with s.reservations.store.connect() as db:before=s.reservations.store.get(db,b['bookingId'])
  os.environ['SANDLOCK_INITIALIZE_STORAGE']='0'
  try:
   Notifications(s.reservations,self.cfg);Notifications(s.reservations,self.cfg)
   with s.reservations.store.connect() as db:self.assertEqual(s.reservations.store.get(db,b['bookingId']),before)
   self.assertIsNotNone(self.identity())
  finally:os.environ['SANDLOCK_INITIALIZE_STORAGE']='1'
 def test_14_concurrent_duplicate_event(self):
  from concurrent.futures import ThreadPoolExecutor
  self.book()
  with ThreadPoolExecutor(max_workers=2) as pool:out=list(pool.map(lambda _:self.n.observe('A','open'),range(2)))
  self.assertEqual(sum(len(x) for x in out),1)
 def test_15_real_encryption_and_vapid_mock_transport(self):
  import requests
  captured=[]
  def send(session,request,**kwargs):
   captured.append(request);r=requests.Response();r.status_code=201;r._content=b'';return r
  with patch.object(requests.Session,'send',send):
   response=self.n.send(subscription(),{'notificationId':'test-encryption'})
  self.assertEqual(captured[0].headers['Content-Type'],'application/octet-stream');self.assertNotIn('X-WNS-Type',captured[0].headers)
  windows=subscription();windows['endpoint']='https://test.notify.windows.com/test'
  with patch.object(requests.Session,'send',send):self.n.send(windows,{'notificationId':'test-encryption'})
  self.assertEqual(captured[-1].headers['X-WNS-Type'],'wns/raw')
  self.assertEqual(response.status_code,201);self.assertIn('vapid',captured[0].headers['Authorization'].lower());self.assertNotIn(b'test-encryption',captured[0].body)
 def test_16_disable_subscription_and_invalid_configuration(self):
  self.book();_,sub=self.enable();self.event('open');self.assertEqual(self.call('/push-subscriptions/remove','POST',{'id':sub['id']}).status_code,200);self.n.dispatch();self.assertEqual(self.sent,[])
  broken=configuration();broken.VAPID_PRIVATE_KEY='';self.assertRaises(RuntimeError,Notifications,s.reservations,broken)

if __name__=='__main__':unittest.main(verbosity=2)
