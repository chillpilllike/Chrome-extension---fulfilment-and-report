"""Read website-enabled (not merely installed) Odoo languages; no sends/writes."""
import json
import re
import socket
import xmlrpc.client
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import psycopg2
from psycopg2.extras import RealDictCursor


def main():
    socket.setdefaulttimeout(30)
    root=Path(__file__).resolve().parents[1]
    db=psycopg2.connect(re.search(r'POSTGRES_URL=(\S+)',(root/'Dockerfile').read_text()).group(1))
    db.set_session(readonly=True)
    cur=db.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT * FROM stores WHERE active=1');stores=cur.fetchall()
    cur.execute("SELECT value FROM app_settings WHERE key='after_order_sms_mappings'")
    mappings=cur.fetchone()['value'];mappings=json.loads(mappings) if isinstance(mappings,str) else mappings
    def audit(store):
        result={'store_id':store['id'],'store_name':store['name'],'websites':[]}
        try:
            url=store['odoo_url'].rstrip('/')
            uid=xmlrpc.client.ServerProxy(url+'/xmlrpc/2/common',allow_none=True).authenticate(store['odoo_db'],store['odoo_user'],store['odoo_password'],{})
            rpc=xmlrpc.client.ServerProxy(url+'/xmlrpc/2/object',allow_none=True)
            def call(model,method,args,kw):return rpc.execute_kw(store['odoo_db'],uid,store['odoo_password'],model,method,args,kw)
            langs=call('res.lang','search_read',[[['active','=',True]]],{'fields':['code']})
            languages={x['id']:x['code'] for x in langs}
            sites=call('website','search_read',[[]],{'fields':['name','domain','language_ids','default_lang_id']})
            for site in sites:
                key=f"{store['id']}:{site['id']}";mapping=mappings.get(key,{})
                result['websites'].append({'key':key,'name':site['name'],'domain':site['domain'],
                    'sender':mapping.get('msg91',{}).get('sender'),
                    'sms_enabled':bool(mapping.get('transactional_sms_enabled')),
                    'languages':sorted(languages[x] for x in site['language_ids'] if x in languages)})
            result['ok']=True
        except Exception as e:result.update(ok=False,error=type(e).__name__)
        print(json.dumps({'store':result['store_name'],'ok':result['ok'],'websites':len(result['websites'])}),flush=True)
        return result
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(audit,stores))
    output={'checked_at':datetime.now(timezone.utc).isoformat(),'stores':results}
    (root/'docs/sms-website-language-audit.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    for result in results:
        groups={}
        for site in result['websites']:
            if site['sender']:groups.setdefault(site['sender'],set()).update(site['languages'])
        print(json.dumps({'store':result['store_name'],'sender_languages':{k:sorted(v) for k,v in groups.items()}}),flush=True)


if __name__=='__main__':main()
