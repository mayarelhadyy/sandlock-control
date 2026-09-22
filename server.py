from flask import Flask, jsonify, request, send_from_directory, g, redirect, abort, send_file
from pathlib import Path
from datetime import datetime, timezone
import json, ssl, threading, time, uuid, io
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from security import public_reservation,safe_text,protect_workbook,identifier
from device_gateway import DeviceGateway
from openpyxl import load_workbook
import paho.mqtt.client as mqtt
import config
from auth import Auth
from reservation_service import ReservationService, ReservationError, excel_record
from reservation_projection import ReservationProjection, workbook_lock, atomic_workbook_save
import application_events
import payment_service as finance

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / config.DATABASE_FILE
app = Flask(__name__, static_folder="static", static_url_path="/static")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config['MAX_CONTENT_LENGTH']=65536
lock = threading.RLock()
reservations = ReservationService(BASE / config.RESERVATION_DATABASE)
projection = ReservationProjection(reservations, DB_PATH, lock)
auth = Auth(reservations.store)

state = {
    "brokerConnected": False,
    "lastBrokerEvent": None,
    "locker": {"id":"A","status":"offline","door":"unknown","battery":None,"online":False,"lastSeen":None,"currentBooking":""},
    "lastAlert": None,
}

def observed_locker():
    value=dict(state['locker'])
    try:fresh=(datetime.now(timezone.utc)-datetime.fromisoformat(value['lastSeen'])).total_seconds()<=60
    except (ValueError,TypeError):fresh=False
    if not fresh or not state['brokerConnected'] or not value.get('online'):value.update(online=False,door='unknown',status='unknown')
    try:door_fresh=(datetime.now(timezone.utc)-datetime.fromisoformat(state.get('doorObservedAt',''))).total_seconds()<=60
    except (ValueError,TypeError):door_fresh=False
    if not door_fresh:value['door']='unknown'
    return value

def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

def open_wb(**kwargs):
    return load_workbook(DB_PATH, **kwargs)

def append_row(sheet_name, row):
    with workbook_lock(DB_PATH, lock):
        wb = open_wb(); ws = wb[sheet_name]; ws.append([safe_text(v) for v in row]); atomic_workbook_save(wb,DB_PATH)

def update_locker(**updates):
    with workbook_lock(DB_PATH, lock):
        wb = open_wb(); ws = wb["Lockers"]
        row = None
        for r in range(2, ws.max_row+1):
            if str(ws.cell(r,1).value) == "A": row = r; break
        if row is None:
            row = ws.max_row+1; ws.cell(row,1).value="A"; ws.cell(row,2).value="Beach Zone 1"
        mapping = {"status":3,"door":4,"battery":5,"online":6,"lastSeen":7,"hourlyRate":8,"currentBooking":9}
        for k,v in updates.items():
            if k in mapping: ws.cell(row,mapping[k]).value = safe_text(v)
        atomic_workbook_save(wb,DB_PATH)
    state["locker"].update(updates)

def upsert_user(data):
    mobile = str(data.get("mobile") or "")
    if not mobile: return
    with workbook_lock(DB_PATH, lock):
        wb = open_wb(); ws=wb["Users"]; row=None
        for r in range(2, ws.max_row+1):
            if str(ws.cell(r,3).value or "") == mobile: row=r; break
        ts=now_iso()
        if row is None:
            row=ws.max_row+1
            ws.cell(row,1).value = safe_text(data.get("userId") or f"USR-{mobile[-6:]}")
            ws.cell(row,5).value = ts
        ws.cell(row,2).value = safe_text(data.get("name") or ws.cell(row,2).value)
        ws.cell(row,3).value = safe_text(mobile)
        ws.cell(row,4).value = safe_text(data.get("paymentLast4") or ws.cell(row,4).value)
        ws.cell(row,6).value = ts
        ws.cell(row,7).value = safe_text(data.get("status") or "active")
        atomic_workbook_save(wb,DB_PATH)

def upsert_reservation(data):
    # Legacy creation snapshots use the same validated authority. Existing records
    # are returned unchanged; unversioned snapshots cannot change lifecycle state.
    raise ReservationError('Reservation writes require an authenticated HTTP session',403)



def add_access(data):
    append_row("Access Logs", [data.get("timestamp") or now_iso(), data.get("locker","A"), data.get("bookingId",""), data.get("ownerMobile",""), data.get("method",""), data.get("action",""), data.get("result",""), data.get("door",""), data.get("source","app")])

def log_device(topic, payload):
    suffix=topic.removeprefix(config.BASE_TOPIC+'/')
    category=suffix if suffix in ('status','door','battery','alert','ack','reservation/set','reservation/clear','cmd/unlock','cmd/cancel','time/set') else 'application-event'
    append_row('Device Events',[now_iso(),'A',config.BASE_TOPIC+'/'+category,category,'received','[REDACTED]'])


def parse_payload(msg):
    text=msg.payload.decode("utf-8",errors="replace")
    if text=="": return ""
    try: return json.loads(text)
    except: return text

def on_connect(client, userdata, flags, reason_code, properties=None):
    state["brokerConnected"] = reason_code == 0; state["lastBrokerEvent"] = now_iso()
    if not state["brokerConnected"]:return
    client.subscribe(f"{config.BASE_TOPIC}/#", qos=config.QOS)
    # Broker connection is not evidence of device liveness.

def on_disconnect(client, userdata, flags, reason_code, properties=None):
    state["brokerConnected"] = False; state["lastBrokerEvent"] = now_iso(); update_locker(online=False)

def handle_device_message(client, userdata, msg):
    topic=msg.topic
    if not topic.startswith(config.BASE_TOPIC+'/') or len(msg.payload)>65536:return
    if topic.startswith(f"{config.BASE_TOPIC}/app/reservation/"):
        app.logger.warning('Unauthenticated reservation MQTT event ignored')
        return
    payload = parse_payload(msg); state["lastBrokerEvent"] = now_iso()
    if getattr(msg,"retain",False) and topic.rsplit("/",1)[-1] in ("status","door","battery"):
        return  # Retained telemetry has no trustworthy observation time.
    log_device(topic,payload)
    suffix = topic.split(f"{config.BASE_TOPIC}/",1)[-1]
    if suffix == "ack":
        gateway.receive_ack(payload,getattr(msg,"retain",False))
    elif suffix == "status":
        val = payload.get("status",payload.get("state",payload.get("value","online"))) if isinstance(payload,dict) else payload
        update_locker(status=str(val).lower() if str(val).lower() in ('online','offline','available','reserved','occupied','active') else 'unknown', online=str(val).lower()!='offline',lastSeen=now_iso())
    elif suffix == "door":
        state['doorObservedAt']=now_iso()
        val = payload.get("door",payload.get("state",payload.get("value","unknown"))) if isinstance(payload,dict) else payload
        update_locker(door=str(val).lower() if str(val).lower() in ('open','closed','locked','unlocked') else 'unknown', online=True,lastSeen=now_iso())
    elif suffix == "battery":
        val = payload.get("battery",payload.get("value",payload.get("percent"))) if isinstance(payload,dict) else payload
        try: val=float(val)
        except: val=None
        if val is not None and not 0<=val<=100:val=None
        update_locker(battery=val, online=True,lastSeen=now_iso())
    elif suffix == "alert":
        typ='Device Alert';sev='warning';message='Device alert received'
        append_row('Alerts',[now_iso(),'A',typ,sev,message,False]);state['lastAlert']={'type':typ,'severity':sev,'message':message,'timestamp':now_iso()}
    elif suffix == "app/user/upsert" and isinstance(payload,dict): upsert_user(payload)
    elif suffix.startswith("app/reservation/") and isinstance(payload,dict):
        app.logger.warning('Unauthenticated reservation MQTT event ignored')
    elif suffix == "app/access" and isinstance(payload,dict):
        add_access(dict(payload,result='unverified-reported-event',door='unknown'))
    elif suffix == "app/payment": app.logger.info("Untrusted financial MQTT event ignored")
    elif suffix == "app/feedback" and isinstance(payload,dict): append_row("Feedback",[payload.get("timestamp") or now_iso(),payload.get("bookingId",""),payload.get("ownerMobile",""),payload.get("rating",0),payload.get("comment","")])


def on_message(client,userdata,msg):
    try:handle_device_message(client,userdata,msg)
    except Exception as exc:app.logger.warning('Device message rejected: %s',type(exc).__name__)

def find_reservation(booking_id):
    try: return excel_record(reservations.get(booking_id))
    except ReservationError as exc:
        if exc.code == 404: return None
        raise


def project_applications():
    try:return projection.applications()
    except Exception as exc:
        app.logger.warning('Application reporting pending: %s',type(exc).__name__)
        return False

def add_admin_audit(action, booking_id="", locker="A", result="ok", details="", admin="Owner Dashboard"):
    if hasattr(g,'identity') and g.identity:admin=g.identity['user_id']
    application_events.record(reservations.store,admin,'audit',dict(action=action,bookingId=booking_id,locker=locker,result=result,details=details),uuid.uuid4().hex)
    return project_applications()


def safe_publish(topic, payload):
    if not config.MQTT_ENABLED:return False
    try:
        info=mqttc.publish(topic,json.dumps(payload),qos=config.QOS)
        info.wait_for_publish(timeout=3)
        return info.rc==mqtt.MQTT_ERR_SUCCESS and info.is_published()
    except Exception:
        return False

mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sandlock-dashboard-{uuid.uuid4().hex[:8]}")
mqttc.username_pw_set(config.BROKER_USERNAME, config.BROKER_PASSWORD)
mqttc.tls_set(tls_version=ssl.PROTOCOL_TLS_CLIENT)
mqttc.on_connect=on_connect; mqttc.on_disconnect=on_disconnect; mqttc.on_message=on_message

def gateway_publish(topic,payload,retain=False):
    try:
        info=mqttc.publish(topic,payload if isinstance(payload,str) else json.dumps(payload),qos=config.QOS,retain=retain)
        info.wait_for_publish(timeout=3)
        return info.rc==mqtt.MQTT_ERR_SUCCESS and info.is_published()
    except Exception:return False

gateway=DeviceGateway(gateway_publish,lambda:config.MQTT_ENABLED,config.BASE_TOPIC)
gateway.bind(reservations)

def mqtt_loop():
    while True:
        try:
            mqttc.connect(config.BROKER_HOST, config.BROKER_PORT, keepalive=45)
            mqttc.loop_forever(retry_first_connection=True)
        except Exception:
            state["brokerConnected"]=False; time.sleep(3)
if config.MQTT_ENABLED:
    threading.Thread(target=mqtt_loop, daemon=True).start()

def rows_as_dict(sheet_name, limit=100):
    with workbook_lock(DB_PATH, lock):
        wb=open_wb(read_only=True,data_only=True); ws=wb[sheet_name]; headers=[c.value for c in ws[1]]; out=[]
        for row_number,row in enumerate(ws.iter_rows(min_row=2, values_only=True),2):
            if not any(v not in (None,"") for v in row): continue
            item={str(headers[i]):row[i] for i in range(min(len(headers),len(row)))}
            if sheet_name=='Alerts':item['_ack']={'row':row_number,'identity':[str(v or '') for v in row[:5]]}
            out.append(item)
        wb.close()
        if sheet_name=='Device Events':
            out=[{**r,'Topic':'[REDACTED]','Value':'[REDACTED]','Raw Payload':'[REDACTED]'} for r in out]
        return list(reversed(out if limit is None else out[-limit:]))

def auth_kind():
    return 'admin' if request.path.startswith('/auth/admin/') else 'user'

def cookie_name(kind):return 'sandlock_'+kind+'_session'

def json_body():
    body=request.get_json(silent=True)
    if not isinstance(body,dict):raise ReservationError('Expected JSON object')
    return body

@app.before_request
def protect_request():
    path=request.path
    g.identity=None
    protected=path.startswith(('/api/','/download/')) or path in ('/','/static/index.html')
    auth_path=path.startswith('/auth/')
    if not protected and not auth_path:return
    kind=auth_kind() if auth_path else ('user' if path.startswith('/api/v1/') and path!='/api/v1/legacy-reservations/import' else 'admin')
    g.kind=kind
    token=request.cookies.get(cookie_name(kind),'')
    g.identity=auth.resolve(token,kind)
    public=auth_path and path.rsplit('/',1)[-1] in ('login','register')
    if not public and not g.identity:
        other=auth.resolve(request.cookies.get(cookie_name('user' if kind=='admin' else 'admin'),''),'user' if kind=='admin' else 'admin')
        if path in ('/','/static/index.html') and not other:return redirect('/static/login.html')
        raise ReservationError('Forbidden' if other else 'Authentication required',403 if other else 401)
    if request.method not in ('GET','HEAD','OPTIONS'):
        origin=request.headers.get('Origin')
        if origin!=request.host_url.rstrip('/') or request.headers.get('Sec-Fetch-Site')=='cross-site':
            raise ReservationError('Untrusted request origin',403)
        if not public:
            import secrets
            if not secrets.compare_digest(request.headers.get('X-CSRF-Token',''),g.identity['csrf']):raise ReservationError('Invalid CSRF token',403)
    if g.identity and request.method in ('GET','HEAD') and auth_path:auth.touch(g.identity)

@app.after_request
def private_headers(response):
    if request.path.startswith(('/auth/','/api/','/download/')) or request.path in ('/','/static/index.html'):
        response.headers['Cache-Control']='no-store'
    if getattr(g,'identity',None):response.headers['X-SandLock-User']=g.identity['user_id']
    # Refresh Admin inactivity only after successfully authorized operations.
    if getattr(g,'identity',None) and response.status_code<400:auth.touch(g.identity)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'; worker-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    return response

@app.post('/auth/user/register')
def register_user():
    return jsonify(user=auth.register(json_body())),201

@app.post('/auth/<kind>/login')
def login_account(kind):
    if kind not in ('user','admin'):abort(404)
    token,csrf,user=auth.login(json_body(),kind)
    auth.revoke(request.cookies.get(cookie_name(kind),''))
    response=jsonify(user=user,csrf=csrf)
    response.set_cookie(cookie_name(kind),token,httponly=True,secure=config.AUTH_COOKIE_SECURE,samesite='Lax',max_age=28800 if kind=='admin' else 604800,path='/')
    return response

@app.get('/auth/<kind>/session')
def account_session(kind):
    if kind not in ('user','admin'):abort(404)
    return jsonify(user=auth.public(g.identity),csrf=g.identity['csrf'])

@app.post('/auth/<kind>/logout')
def logout_account(kind):
    if kind not in ('user','admin'):abort(404)
    auth.revoke(request.cookies.get(cookie_name(kind),''))
    response=jsonify(ok=True);response.delete_cookie(cookie_name(kind),path='/')
    return response

@app.post('/auth/user/profile')
def update_profile():
    body=json_body();name=body.get('name');mobile=body.get('mobile')
    if not isinstance(name,str) or not 2<=len(name.strip())<=120 or not isinstance(mobile,str) or len(mobile)>30:raise ReservationError('Invalid profile')
    def save(db):
        db.execute('UPDATE accounts SET name=?,mobile=? WHERE user_id=?',(name.strip(),mobile,g.identity['user_id']))
        application_events.accept(db,g.identity['user_id'],'profile',{'name':name.strip(),'mobile':mobile},uuid.uuid4().hex)
    auth.store.transaction(save)
    project_applications()
    return jsonify(user=auth.public({**g.identity,'name':name.strip(),'mobile':mobile}))

@app.route("/")
def home(): return send_from_directory(BASE/"static","index.html")
@app.get("/api/overview")
def api_overview():
    state["locker"].update(observed_locker())
    financial_rows=reservations.list();reservation_rows=[excel_record(r) for r in financial_rows]; users=rows_as_dict("Users",None); alerts=rows_as_dict("Alerts",None)
    active=[r for r in reservation_rows if str(r.get("Status","")).lower() not in ("completed","cancelled","")]
    financial=finance.summary(financial_rows)
    view_state={**state,"locker":{**state["locker"]}}
    slot=next(x for x in reservations.availability() if x['lockerId']=='A')
    view_state['locker'].update(status=slot['status'],currentBooking=slot['bookingId'] or '')
    return jsonify({"state":view_state,"kpi":{"users":len(users),"activeReservations":len(active),"alerts":len([a for a in alerts if not a.get("Acknowledged")]),**financial},"activeReservations":active[:8],"alerts":alerts[:8]})
@app.get("/api/<name>")
def api_sheet(name):
    mapping={"users":"Users","reservations":"Reservations","access":"Access Logs","events":"Device Events","alerts":"Alerts","payments":"Payments","feedback":"Feedback","lockers":"Lockers","audit":"Admin Audit"}
    if name not in mapping: return jsonify({"error":"not found"}),404
    if name == "payments":
        current=finance.reporting_rows(reservations.store)
        historical=[{**r,'Status':'legacy-review (not collected)'} for r in rows_as_dict('Payments',None) if not r.get('Financial Event')]
        return jsonify(current+historical)
    if name == "reservations": return jsonify([excel_record(r) for r in reservations.list()])
    if name == "lockers":
        rows=rows_as_dict("Lockers",300)
        slots={x['lockerId']:x for x in reservations.availability()}
        for r in rows:
            if str(r.get('Locker')) in slots:
                slot=slots[str(r['Locker'])];r.update(Status=slot['status']);r['Current Booking']=slot['bookingId'] or ''
        return jsonify(rows)
    return jsonify(rows_as_dict(mapping[name],300 if name=="payments" else None))
@app.get("/api/reservations/<booking_id>")
def reservation_details(booking_id):
    r=find_reservation(booking_id)
    if not r: return jsonify({"error":"reservation not found"}),404
    return jsonify(r)

@app.post("/api/reservations/<booking_id>/reveal-pin")
def reveal_pin(booking_id):
    r=reservations.get(booking_id)
    add_admin_audit("Reveal Reservation PIN", booking_id, r["lockerId"], "authorized", "PIN disclosure authorized; delivery unconfirmed")
    return jsonify({"ok":True,"pin":r["pin"]})

@app.post("/api/reservations/<booking_id>/cancel")
def cancel_reservation(booking_id):
    body=request.get_json(silent=True) or {}
    if not isinstance(body,dict):raise ReservationError("Expected JSON object")
    r,changed=reservations.transition(booking_id,"cancel",body.get('expectedRevision'),source='admin',admin_actor=g.identity['user_id'])
    # Cancellation records and their existing Excel action logs share the
    # retryable projection; a workbook failure cannot lose the committed action.
    return reservation_response(r,changed=changed)

@app.post("/api/cmd/unlock")
def cmd_unlock():
    body=json_body()
    if set(body)-{'bookingId','locker'}:raise ReservationError('Unsupported unlock fields')
    booking_id=body.get('bookingId','')
    if booking_id and not identifier(booking_id):raise ReservationError('Invalid bookingId')
    r=find_reservation(booking_id) if booking_id else None
    if booking_id and not r:raise ReservationError('Reservation not found',404)
    locker=body.get('locker') or (r or {}).get('Locker') or 'A'
    if locker!='A' or (r and r.get('Locker')!=locker):raise ReservationError('No matching physical device configured',409)
    payload={'locker':locker,'source':'admin-dashboard','timestamp':now_iso(),'ownerOverride':True}
    if booking_id:payload['bookingId']=booking_id
    payload['requestId']=uuid.uuid4().hex
    result=reservations.store.transaction(lambda db:gateway.command(reservations,g.identity,booking_id,payload))
    # The durable Group 4 command journal already records this accepted attempt.
    try:projection.device_audit(gateway.journal)
    except Exception:app.logger.warning("Command audit projection pending")
    return jsonify(result)


@app.get('/api/device/commands/<request_id>')
@app.get('/api/v1/device/commands/<request_id>')
def command_status(request_id):
    if not identifier(request_id):raise ReservationError('Invalid request identifier')
    gateway.bind(reservations)
    value=gateway.journal.get(request_id,g.identity['user_id'])
    if not value:raise ReservationError('Command not found',404)
    return jsonify(**value,physicalConfirmed=False)

@app.post("/api/alerts/ack")
def ack_alert():
    body=json_body();idx=body.get('row',0)
    if type(idx)!=int or idx<0:raise ReservationError('Invalid alert row')
    with workbook_lock(DB_PATH, lock):
        wb=open_wb()
        try:
            ws=wb['Alerts']
            if not 2<=idx<=ws.max_row:raise ReservationError('Alert not found',404)
            identity=[str(ws.cell(idx,i).value or '') for i in range(1,6)]
            expected=body.get('expectedIdentity')
            if not isinstance(expected,list) or len(expected)!=5 or any(not isinstance(v,str) for v in expected):
                raise ReservationError('Alert reference is required; refresh the alerts list')
            if identity!=expected:raise ReservationError('Alert changed; refresh the alerts list',409)
            if ws.cell(idx,6).value is True:return jsonify(ok=True,alreadyAcknowledged=True,excelSynchronized=True)
            # Record the selected identity while holding the same lock used by projection.
            application_events.record(reservations.store,g.identity['user_id'],'alert-ack',dict(row=idx,identity=identity))
        finally:wb.close()
    return jsonify(ok=True,excelSynchronized=project_applications())
@app.get("/download/database.xlsx")
def download_db():
    with workbook_lock(DB_PATH,lock):
        wb=open_wb();protect_workbook(wb,export=True);data=io.BytesIO();wb.save(data);wb.close()
    data.seek(0)
    return send_file(data,mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',as_attachment=True,download_name='SandLock_Dashboard_Database.xlsx')

@app.errorhandler(ReservationError)
def reservation_error(exc):
    return jsonify(error=str(exc)),exc.code

@app.after_request
def reservation_headers(response):
    if request.path.startswith('/api/v1/'):
        response.headers['Cache-Control']='no-store'
    return response

def reservation_notify(r,source='admin'):
    return reservations.store.transaction(lambda db:_reservation_notify(r,source,db))

def _reservation_notify(r,source,db):
    if r['lockerId']!='A':return True
    if not config.MQTT_ENABLED:return False
    current=reservations.store.rows(db)
    if any(x['lockerId']=='A' and x['bookingId']!=r['bookingId'] and x['status'] in ('Confirmed','Active','Overdue') for x in current):return True
    try:
        payload={"bookingId":r['bookingId'],"locker":r['lockerId'],"status":r['status'],"cancelledAt":r.get('cancelledAt'),"source":"admin-dashboard" if source=='admin' else 'backend'}
        topics=[f"{config.BASE_TOPIC}/admin/reservation/cancel"]
        # Preserve the pre-existing Admin device command without changing its
        # payload contract or claiming that broker receipt is a physical result.
        if source=='admin':topics.append(f"{config.BASE_TOPIC}/cmd/cancel")
        published=True
        for topic in topics:
            info=mqttc.publish(topic,json.dumps(payload),qos=config.QOS)
            info.wait_for_publish(timeout=1)
            published=published and info.rc==mqtt.MQTT_ERR_SUCCESS and info.is_published()
        return published
    except Exception:return False

def reservation_response(r, status=200, changed=False):
    projected=projection.flush()
    projected=projection.financial() and projected
    value=public_reservation(r)
    if getattr(g,'kind',None)=='admin' and 'actionActor' in r:value['actionActor']=r['actionActor']
    return jsonify(ok=True,reservation=value,created=status==201,changed=changed,excelSynchronized=projected),status

@app.route('/api/v1/reservations',methods=['GET','POST'])
def reservation_collection():
    if request.method=='GET':
        rows=reservations.list(g.identity['user_id']);projection.flush()
        return jsonify(reservations=[public_reservation(r) for r in rows],financialHistory=finance.summary([r for r in rows if r['status'] in ('Completed','Cancelled')]))
    body=request.get_json(silent=True)
    if not isinstance(body,dict):raise ReservationError('Expected JSON object')
    body.update(userId=g.identity['user_id'],ownerName=g.identity['name'],ownerMobile=g.identity['mobile'])
    r,created=reservations.create(body)
    return reservation_response(r,201 if created else 200)

@app.post('/api/v1/financial/quote')
def financial_quote():
    from reservation_service import LOCKERS,parse_time
    body=json_body();locker=body.get('lockerId')
    if not isinstance(locker,str) or locker not in LOCKERS:raise ReservationError('Invalid lockerId')
    start,end=parse_time(body.get('startTime')),parse_time(body.get('endTime'))
    if end<=start or (end-start).total_seconds()>86400:raise ReservationError('Invalid duration')
    return jsonify(total=finance.amount_for(start,end,LOCKERS[locker]),hourlyRate=LOCKERS[locker],actualCollectedRevenue=None)

@app.get('/api/v1/reservations/<booking_id>')
def canonical_reservation(booking_id):return reservation_response(reservations.get(booking_id,actor=g.identity['user_id']))

@app.get('/api/v1/reservations/<booking_id>/pin')
def user_pin(booking_id):
    r=reservations.get(booking_id,actor=g.identity['user_id'])
    if r['status'] not in ('Confirmed','Active','Overdue'):raise ReservationError('PIN unavailable for terminal reservation',409)
    return jsonify(bookingId=booking_id,pin=r['pin'],revision=r['revision'])

@app.post('/api/v1/device/<booking_id>/<action>')
def device_action(booking_id,action):
    return jsonify(gateway.action(reservations,g.identity,booking_id,action,json_body()))

@app.get('/api/v1/device/state')
def device_state():
    rows=reservations.list(g.identity['user_id'])
    own=any(r['lockerId']=='A' and r['status'] in ('Confirmed','Active','Overdue') for r in rows)
    fields=('status','door','battery','online','lastSeen')
    return jsonify(brokerConnected=state['brokerConnected'],locker={k:observed_locker().get(k) for k in fields} if own else {},acks=gateway.acks(g.identity))

@app.post('/api/v1/events/<kind>')
def device_event(kind):
    value=gateway.event(reservations,g.identity,kind,json_body())
    if value.get('accepted'):value['excelSynchronized']=projection.financial() if kind=='payment' else project_applications()
    return jsonify(value)

@app.errorhandler(Exception)
def safe_error(exc):
    if isinstance(exc,HTTPException):return jsonify(error=exc.name),exc.code
    app.logger.warning('Request failed: %s',type(exc).__name__)
    return jsonify(error='Service temporarily unavailable'),503

@app.post('/api/v1/reservations/<booking_id>/<action>')
def user_reservation_action(booking_id,action):
    body=request.get_json(silent=True) or {}
    if not isinstance(body,dict):raise ReservationError('Expected JSON object')
    r,changed=reservations.transition(booking_id,action,body.get('expectedRevision'),source='user',actor=g.identity['user_id'])
    return reservation_response(r,changed=changed)

@app.post('/api/v1/legacy-reservations/import')
def import_legacy_reservation():
    body=request.get_json(silent=True) or {}
    if not isinstance(body,dict):raise ReservationError('Expected JSON object')
    r,created=reservations.create(body.get('reservation'),legacy=True,timezone_name=body.get('timezone'),source='legacy-import')
    return reservation_response(r,201 if created else 200)

@app.get('/api/v1/lockers')
def canonical_lockers():return jsonify(lockers=[{k:v for k,v in r.items() if k!='bookingId'} for r in reservations.availability()])

@app.route('/user/')
def user_home():return send_from_directory(config.USER_APP_PATH,'index.html')

@app.route('/user/<path:name>')
def user_asset(name):
    if Path(name).suffix.lower() not in ('.html','.js','.css','.png','.jpg','.jpeg','.svg','.ico','.webp','.webmanifest','.woff','.woff2') or '..' in Path(name).parts:abort(404)
    return send_from_directory(config.USER_APP_PATH,name)

def recovery_tick():
    tasks=(('reservations',reservations.list),('reporting',projection.flush),('applications',projection.applications),('financial',projection.financial),('device-audit',lambda:projection.device_audit(gateway.journal)),('notifications',lambda:projection.notify(reservation_notify)),('device-intents',lambda:gateway.reconcile(reservations)))
    result={}
    for name,task in tasks:
        try:result[name]=task() is not False
        except Exception as exc:
            result[name]=False
            app.logger.warning('%s recovery pending: %s',name,type(exc).__name__)
    return result

def reservation_loop():
    while True:
        recovery_tick()
        time.sleep(2)

if config.RESERVATION_WORKER:
    threading.Thread(target=reservation_loop,daemon=True).start()

if __name__ == "__main__":
    app.run(host=config.HTTP_HOST,port=config.HTTP_PORT,debug=False)

