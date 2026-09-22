import sys, os, tempfile, shutil, unittest, json, secrets
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path[:0]=[str(ROOT),str(ROOT/'.test-deps')]
RUNTIME=ROOT/'tests/.runtime';RUNTIME.mkdir(exist_ok=True)
folder=Path(tempfile.mkdtemp(dir=RUNTIME));assert folder.resolve().is_relative_to(RUNTIME.resolve())
shutil.copy2(ROOT/'data/SandLock_Dashboard_Database.xlsx',folder/'report.xlsx')
os.environ.update(SANDLOCK_MQTT_ENABLED='0',SANDLOCK_PUSH_ENABLED='0',SANDLOCK_LOCAL_HTTP='0',SANDLOCK_INITIALIZE_STORAGE='1',SANDLOCK_RESERVATION_WORKER='0',SANDLOCK_TRUST_PROXY='0',SANDLOCK_RESERVATION_DB=str(folder/'state.sqlite3'),SANDLOCK_WORKBOOK=str(folder/'report.xlsx'),SANDLOCK_RESERVATION_ORIGINS='https://sandlock-app.vercel.app')
import server as s
from datetime import datetime,timezone,timedelta
from reservation_service import iso
ORIGIN='https://sandlock-app.vercel.app';BASE='https://sandlock-control-production.up.railway.app'
class Integration(unittest.TestCase):
 def setUp(self):
  self.c=s.app.test_client();self.login='u'+secrets.token_hex(6);self.password=secrets.token_urlsafe(16)
 def request(self,path,method='GET',body=None,csrf=None,origin=ORIGIN,client=None):
  return (client or self.c).open(path,method=method,json=body,base_url=BASE,headers={'Origin':origin,**({'X-CSRF-Token':csrf} if csrf else {})})
 def login_user(self):
  self.assertEqual(self.request('/auth/user/register','POST',{'login':self.login,'password':self.password,'name':'Test User'}).status_code,201)
  r=self.request('/auth/user/login','POST',{'login':self.login,'password':self.password});self.assertEqual(r.status_code,200);return r
 def test_preflight(self):
  r=self.c.options('/auth/user/login',base_url=BASE,headers={'Origin':ORIGIN,'Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'Content-Type, X-CSRF-Token'})
  self.assertEqual(r.status_code,200);self.assertEqual(r.headers['Access-Control-Allow-Origin'],ORIGIN);self.assertEqual(r.headers['Access-Control-Allow-Credentials'],'true');self.assertIn('Origin',r.vary)
 def test_preflight_untrusted(self):
  r=self.c.options('/api/v1/reservations',base_url=BASE,headers={'Origin':'https://evil.invalid','Access-Control-Request-Method':'POST'});self.assertEqual(r.status_code,403);self.assertNotIn('Access-Control-Allow-Origin',r.headers)
 def test_preflight_invalid_header(self):
  r=self.c.options('/api/v1/reservations',base_url=BASE,headers={'Origin':ORIGIN,'Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'X-Forged'});self.assertEqual(r.status_code,403)
 def test_error_cors(self):
  r=self.request('/api/v1/reservations');self.assertEqual(r.status_code,401);self.assertEqual(r.headers['Access-Control-Allow-Origin'],ORIGIN);self.assertEqual(r.headers['Cache-Control'],'no-store')
 def test_partitioned_cookie_and_session(self):
  r=self.login_user();cookie=r.headers['Set-Cookie']
  for part in ('__Host-sandlock_user_session_v2=','Secure','HttpOnly','SameSite=None','Partitioned','Path=/'):self.assertIn(part,cookie)
  self.assertNotIn('Domain=',cookie);self.assertNotIn('token',r.json)
  session=self.request('/auth/user/session');self.assertEqual(session.status_code,200);self.assertEqual(session.json['user']['userId'],r.json['user']['userId']);self.assertEqual(session.headers['Access-Control-Expose-Headers'],'X-SandLock-User')
 def test_api_credentials_and_csrf(self):
  r=self.login_user();csrf=r.json['csrf'];self.assertEqual(self.request('/api/v1/lockers').status_code,200)
  before=s.reservations.list();self.assertEqual(self.request('/api/v1/reservations','POST',{}).status_code,403);self.assertEqual(before,s.reservations.list())
  self.assertEqual(self.request('/auth/user/profile','POST',{'name':'Changed User','mobile':''},csrf).status_code,200)
 def test_untrusted_origin(self):
  r=self.login_user();self.assertEqual(self.request('/auth/user/profile','POST',{'name':'Changed User','mobile':''},r.json['csrf'],origin='https://evil.invalid').status_code,403)
 def test_logout_revokes(self):
  r=self.login_user();cookie=self.c.get_cookie(s.cookie_name('user'),domain='sandlock-control-production.up.railway.app').value
  out=self.request('/auth/user/logout','POST',{},r.json['csrf']);self.assertEqual(out.status_code,200);self.assertIn('Partitioned',out.headers['Set-Cookie']);self.assertIn('Max-Age=0',out.headers['Set-Cookie']);self.assertIsNone(s.auth.resolve(cookie,'user'));self.assertEqual(self.request('/auth/user/session').status_code,401)
 def test_invalid_expired(self):
  self.login_user();token=self.c.get_cookie(s.cookie_name('user'),domain='sandlock-control-production.up.railway.app').value
  with s.auth.store.connect() as db:db.execute('UPDATE sessions SET expires=0 WHERE token_hash=?',(s.auth.resolve(token,'user')['token_hash'],))
  self.assertEqual(self.request('/api/v1/reservations').status_code,401)
 def test_admin_separation(self):
  r=self.login_user();self.assertIn(self.request('/api/overview').status_code,(401,403));self.assertEqual(self.request('/auth/admin/login','POST',{'login':self.login,'password':self.password}).status_code,403)
 def test_owner_login(self):
  s.auth.register({'login':self.login,'password':self.password,'name':'Test Owner'},role='admin')
  r=self.request('/auth/admin/login','POST',{'login':self.login,'password':self.password},origin=BASE);self.assertEqual(r.status_code,200);self.assertIn('SameSite=Lax',r.headers['Set-Cookie']);self.assertNotIn('Partitioned',r.headers['Set-Cookie']);self.assertEqual(self.request('/api/overview',origin=BASE).status_code,200)
 def test_reservation_lifecycle(self):
  csrf=self.login_user().json['csrf'];now=datetime.now(timezone.utc);bid='T-'+secrets.token_hex(8)
  payload={'bookingId':bid,'lockerId':'B','startTime':iso(now),'endTime':iso(now+timedelta(minutes=30)),'pin':'1234'}
  r=self.request('/api/v1/reservations','POST',payload,csrf);self.assertEqual(r.status_code,201,r.json);r=r.json['reservation'];self.assertEqual(self.request('/api/v1/reservations','POST',payload,csrf).status_code,200)
  other=s.app.test_client();other_login='u'+secrets.token_hex(8);self.request('/auth/user/register','POST',{'login':other_login,'password':self.password,'name':'Other User'},client=other);self.request('/auth/user/login','POST',{'login':other_login,'password':self.password},client=other)
  self.assertEqual(self.request('/api/v1/reservations/'+bid,client=other).status_code,404)
  self.assertEqual(self.request('/api/v1/reservations/'+bid+'/complete','POST',{'expectedRevision':r['revision']},csrf).json['reservation']['status'],'Completed')
 def test_cancel(self):
  csrf=self.login_user().json['csrf'];now=datetime.now(timezone.utc);bid='T-'+secrets.token_hex(8)
  r=self.request('/api/v1/reservations','POST',{'bookingId':bid,'lockerId':'B','startTime':iso(now+timedelta(minutes=20)),'endTime':iso(now+timedelta(minutes=50)),'pin':'1234'},csrf);self.assertEqual(r.status_code,201,r.json)
  done=self.request('/api/v1/reservations/'+bid+'/cancel','POST',{'expectedRevision':r.json['reservation']['revision']},csrf);self.assertEqual(done.status_code,200);self.assertEqual(done.json['reservation']['finalTotal'],0)
 def test_concurrent_booking_and_owner_cancel(self):
  from concurrent.futures import ThreadPoolExecutor
  first_csrf=self.login_user().json['csrf'];other=s.app.test_client();login='u'+secrets.token_hex(8)
  self.request('/auth/user/register','POST',{'login':login,'password':self.password,'name':'Other User'},client=other)
  second_csrf=self.request('/auth/user/login','POST',{'login':login,'password':self.password},client=other).json['csrf']
  now=datetime.now(timezone.utc)+timedelta(hours=1)
  def create(args):
   client,csrf=args
   return self.request('/api/v1/reservations','POST',{'bookingId':'R-'+secrets.token_hex(8),'lockerId':'B','startTime':iso(now),'endTime':iso(now+timedelta(minutes=30)),'pin':'1234'},csrf,client=client)
  with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(create,[(self.c,first_csrf),(other,second_csrf)]))
  self.assertEqual(sorted(r.status_code for r in responses),[201,409])
  booking=next(r.json['reservation'] for r in responses if r.status_code==201)
  owner=s.app.test_client();admin_login='a'+secrets.token_hex(8)
  s.auth.register({'login':admin_login,'password':self.password,'name':'Test Owner'},role='admin')
  csrf=self.request('/auth/admin/login','POST',{'login':admin_login,'password':self.password},origin=BASE,client=owner).json['csrf']
  result=self.request('/api/reservations/'+booking['bookingId']+'/cancel','POST',{'expectedRevision':booking['revision']},csrf,origin=BASE,client=owner)
  self.assertEqual(result.status_code,200,result.json)
  user_client=self.c if responses[0].status_code==201 else other
  self.assertEqual(self.request('/api/v1/reservations/'+booking['bookingId'],client=user_client).json['reservation']['status'],'Cancelled')
if __name__=='__main__':unittest.main(verbosity=2)
