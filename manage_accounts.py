"""Local operator utility. No default credentials or automatic legacy claims."""
import argparse,getpass
from pathlib import Path
import config
from reservation_store import ReservationStore
from auth import Auth
p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
a=sub.add_parser('create-admin');a.add_argument('login');a.add_argument('--name',required=True)
m=sub.add_parser('map-owner');m.add_argument('legacy_id');m.add_argument('user_id');m.add_argument('--admin-login',required=True)
args=p.parse_args();auth=Auth(ReservationStore(Path(__file__).parent/config.RESERVATION_DATABASE))
if args.command=='create-admin':
    password=getpass.getpass('New Admin password (12–128 characters): ')
    if password!=getpass.getpass('Repeat password: '):raise SystemExit('Passwords differ')
    print(auth.register(dict(login=args.login,password=password,name=args.name,mobile=''),role='admin')['userId'])
else:
    token,csrf,admin=auth.login(dict(login=args.admin_login,password=getpass.getpass('Admin password: ')),'admin')
    auth.revoke(token)
    if input('Confirm reviewed ownership assignment (type ASSIGN): ')!='ASSIGN':raise SystemExit('Not assigned')
    auth.map_owner(args.legacy_id,args.user_id,admin['userId']);print('Reviewed ownership mapping saved; reservation records preserved.')
