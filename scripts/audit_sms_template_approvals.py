"""Read-only MSG91 approval audit of every configured English fallback."""
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

import psycopg2
import requests


def main():
    root=Path(__file__).resolve().parents[1]
    def secret(account):
        return subprocess.check_output(['security','find-generic-password','-s',
            'codex.coolify.185.194.236.161','-a',account,'-w'],text=True).strip()
    r=requests.get(secret('coolify-host')+'/api/v1/applications/'+secret('coolify-app')+'/envs',
        headers={'Authorization':'Bearer '+secret('coolify-api')},timeout=30)
    r.raise_for_status()
    auth=next(x['value'] for x in r.json() if x['key']=='MSG91_AUTH_KEY' and not x.get('is_preview'))
    conn=psycopg2.connect(re.search(r'POSTGRES_URL=(\S+)',(root/'Dockerfile').read_text()).group(1))
    conn.set_session(readonly=True)
    cur=conn.cursor();cur.execute("SELECT value FROM app_settings WHERE key='after_order_sms_mappings'")
    config=cur.fetchone()[0];config=json.loads(config) if isinstance(config,str) else config
    entries={}
    for site in config.values():
        mapping=site.get('msg91',{})
        for kind,template in mapping.get('templates',{}).items():
            entries[(mapping['sender'],kind)]=dict(template,sender=mapping['sender'],kind=kind)
    def check(row):
        out={k:row.get(k) for k in ('sender','kind','template_id')}
        try:
            r=requests.post('https://control.msg91.com/api/v5/sms/getTemplateVersions',
                headers={'authkey':auth},json={'template_id':row.get('template_id')},timeout=20)
            r.raise_for_status()
            active=[x for x in r.json().get('data',[]) if str(x.get('active_status'))=='1']
            out['approved']=len(active)==1 and str(active[0].get('status'))=='1'
            out['exact_match']=len(active)==1 and active[0].get('sender_id')==row['sender'] and active[0].get('template_data')==row.get('text')
            out['ready']=out['approved'] and out['exact_match']
        except Exception:
            out.update(ready=False,error='Approval check unavailable')
        return out
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows=list(pool.map(check,entries.values()))
    result={'checked_at':datetime.now(timezone.utc).isoformat(),'english_mappings':rows,
            'ready':sum(x['ready'] for x in rows),'total':len(rows)}
    (root/'docs/msg91-english-approval-audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'ready':result['ready'],'total':result['total'],
        'blocked':[x for x in rows if not x['ready']]}),flush=True)


if __name__=='__main__':main()
