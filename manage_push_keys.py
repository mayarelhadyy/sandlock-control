"""Generate VAPID configuration locally without printing the private key."""
import argparse,base64,os,re
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat

def generate(contact,output):
    if not re.fullmatch(r'mailto:[^\s@]+@[^\s@]+',contact):raise ValueError('Use mailto: followed by your monitored email address')
    key=ec.generate_private_key(ec.SECP256R1())
    encode=lambda data:base64.urlsafe_b64encode(data).decode().rstrip('=')
    private=encode(key.private_numbers().private_value.to_bytes(32,'big'))
    public=encode(key.public_key().public_bytes(Encoding.X962,PublicFormat.UncompressedPoint))
    text=f'SANDLOCK_PUSH_ENABLED=1\nSANDLOCK_VAPID_PUBLIC_KEY={public}\nSANDLOCK_VAPID_PRIVATE_KEY={private}\nSANDLOCK_VAPID_SUBJECT={contact}\n'
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as stream:stream.write(text)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--contact',required=True);parser.add_argument('--output',default='.env.vapid');args=parser.parse_args()
    generate(args.contact,args.output)
    print('Private VAPID settings written to the requested local file. Keep it private; never upload it to source control or Vercel.')
