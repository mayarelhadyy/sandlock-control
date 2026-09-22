import sys,os,tempfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True
sys.path[:0]=[str(ROOT),str(ROOT/'.test-deps')]
runtime=ROOT/'tests/.runtime';runtime.mkdir(exist_ok=True);data=Path(tempfile.mkdtemp(dir=runtime))
shutil.copy2(ROOT/'data/SandLock_Dashboard_Database.xlsx',data/'report.xlsx')
os.environ.update(SANDLOCK_MQTT_ENABLED='0',SANDLOCK_PUSH_ENABLED='0',SANDLOCK_LOCAL_HTTP='0',SANDLOCK_INITIALIZE_STORAGE='1',SANDLOCK_RESERVATION_WORKER='0',SANDLOCK_TRUST_PROXY='1',SANDLOCK_RESERVATION_DB=str(data/'state.sqlite3'),SANDLOCK_WORKBOOK=str(data/'report.xlsx'),SANDLOCK_RESERVATION_ORIGINS='https://app.frontend.test:8796',SANDLOCK_HTTP_HOST='127.0.0.1',PORT='8795')
# Synthetic Owner account only in the fresh test database.
import secrets,json
from reservation_service import ReservationService
from auth import Auth
credentials={'login':'local-browser-owner','password':secrets.token_urlsafe(24),'name':'Local Test Owner'}
Auth(ReservationService(data/'state.sqlite3').store).register(credentials,role='admin')
(runtime/'browser-owner.json').write_text(json.dumps(credentials))
(runtime/'browser-storage.json').write_text(json.dumps({'db':str(data/'state.sqlite3'),'workbook':str(data/'report.xlsx')}))
import runpy
runpy.run_path(str(ROOT/'server.py'),run_name='__main__')
