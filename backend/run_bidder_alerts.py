"""Signed worker; never publishes user identities or chat IDs."""
import hashlib,hmac,json,os,time
import requests

def main():
    secret=os.environ['TELEGRAM_BOT_TOKEN'];url=os.getenv('ACCOUNT_API','https://mp-tenders-api.onrender.com')+'/api/internal/bidder-alerts'
    for _ in range(110):
        stamp=str(int(time.time()));raw=b'{}'
        signature=hmac.new(secret.encode(),b'mp-bidder-alerts\n'+stamp.encode()+b'\n'+raw,hashlib.sha256).hexdigest()
        response=requests.post(url,data=raw,headers={'Content-Type':'application/json','X-Alert-Timestamp':stamp,'X-Alert-Signature':signature},timeout=90)
        response.raise_for_status();data=response.json()
        if not data.get('pending'):
            print(json.dumps({key:data.get(key,0) for key in ('bound','sent','uncertain')}));return
        time.sleep(5)
    raise RuntimeError('Private alert job is still pending; next scheduled job will resume saved state.')
if __name__=='__main__':main()
