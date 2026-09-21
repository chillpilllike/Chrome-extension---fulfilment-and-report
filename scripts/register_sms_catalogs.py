"""Register offline translated catalogs in MSG91; never sends a message.

Dry run by default. A durable SQLite journal is written BEFORE each create call:
unknown outcomes are held for dashboard reconciliation, never blindly retried.
Successful public IDs are imported into the app's localization lookup, not its
English mappings. Provider approval is still checked on every send.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import re
import sqlite3
import signal
import subprocess
import threading
import time
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values
import requests

from app.services.notification_i18n import catalog, valid_translation
from app.services.sms_language_scope import allowed_catalogs, TRANSLATED_SENDERS

ROOT = Path(__file__).resolve().parents[1]
ALIASES = {'package_movement':'tracking', 'alternative_payment':'price_difference'}


def plan(senders, website_scope):
    english = catalog('en')['sms']
    rows = []
    for sender in senders:
        brand = 'Nutricity' if sender == 'nutricity' else sender
        for language in sorted(allowed_catalogs(sender,website_scope.get(sender,[]))):
            data = catalog(language)
            if not data.get('complete') or data.get('delivery_blocked'):
                continue
            for kind, source in data['sms'].items():
                # Existing maintained French copy repeats the fixed brand in its signature.
                expected = english[kind] + ' {brand}' * max(0, source.count('{brand}')-english[kind].count('{brand}'))
                if not valid_translation(expected, source):
                    raise ValueError('Invalid translated SMS: '+language+':'+kind)
                text = source.format(brand=brand, order='##order##', url='##url##')
                if text.rstrip().endswith('##url##'):
                    text += ' - '+brand
                if brand not in text or text.rstrip().endswith('##'):
                    raise ValueError('Fixed brand and trailing literal text are required.')
                rows.append({'sender':sender,'language':language,'kind':kind,'text':text,'sms_type':'UNICODE'})
    return rows


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--senders', nargs='+', choices=sorted(TRANSLATED_SENDERS), required=True)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--journal', default='/tmp/care-msg91-registration.sqlite3')
    args=p.parse_args()
    from scripts.audit_sms_website_languages import main as refresh_website_audit
    refresh_website_audit()
    audit=json.loads((ROOT/'docs/sms-website-language-audit.json').read_text())
    if any(not s.get('ok') for s in audit['stores']):
        raise ValueError('Website language audit incomplete; registration is blocked.')
    scope={}
    for store in audit['stores']:
        for site in store['websites']:
            if site.get('sms_enabled'):
                scope.setdefault(site.get('sender'),set()).update(site['languages'])
    rows=plan(args.senders,scope)
    archive_path=ROOT/'docs/msg91-template-archive-manifest.json'
    archived=json.loads(archive_path.read_text()) if archive_path.exists() else {}
    # A newly re-enabled language must be restored and verified in MSG91 first.
    # Never silently re-import a retired provider ID from the durable journal.
    retired={k for k,v in archived.items() if v.get('provider_archive_status')!='restored'}
    blocked=[r for r in rows if ':'.join(r[x] for x in ('sender','language','kind')) in retired]
    if blocked:
        raise ValueError(f'{len(blocked)} retired templates need verified restoration before registration.')
    print(json.dumps({'planned':len(rows),'senders':args.senders,'apply':args.apply}),flush=True)
    if not args.apply:return
    def secret(account):
        return subprocess.check_output(['security','find-generic-password','-s',
            'codex.coolify.185.194.236.161','-a',account,'-w'],text=True).strip()
    env=requests.get(secret('coolify-host')+'/api/v1/applications/'+secret('coolify-app')+'/envs',
        headers={'Authorization':'Bearer '+secret('coolify-api')},timeout=30)
    env.raise_for_status()
    auth=next(x['value'] for x in env.json() if x['key']=='MSG91_AUTH_KEY' and not x.get('is_preview'))
    dsn=re.search(r'POSTGRES_URL=(\S+)',(ROOT/'Dockerfile').read_text()).group(1)
    db=sqlite3.connect(args.journal,check_same_thread=False)
    db.execute('CREATE TABLE IF NOT EXISTS registrations(key TEXT PRIMARY KEY,status TEXT NOT NULL,row_json TEXT NOT NULL)')
    for key,row in json.loads((ROOT/'docs/msg91-localized-templates.json').read_text()).items():
        db.execute('INSERT OR IGNORE INTO registrations VALUES(?,?,?)',(key,'created',json.dumps(row)))
    db.commit()
    lock=threading.Lock(); throttle=threading.Lock(); last=[0.0]; count=[0]
    stopping=threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    def register(row):
        if stopping.is_set():return
        key=':'.join(row[x] for x in ('sender','language','kind'))
        with lock:
            existing=db.execute('SELECT status,row_json FROM registrations WHERE key=?',(key,)).fetchone()
            if existing:
                if existing[0]!='created':return
                saved=json.loads(existing[1])
                if saved['text']!=row['text']:
                    # Existing French wording remains authoritative; do not change approved provider content.
                    row=saved
                else:row={**row,**saved}
            else:
                db.execute('INSERT INTO registrations VALUES(?,?,?)',(key,'creation_unknown',json.dumps(row)))
                db.commit()
        if not existing:
            with throttle:
                time.sleep(max(0,0.25-(time.monotonic()-last[0])))
                last[0]=time.monotonic()
            try:
                response=requests.post('https://control.msg91.com/api/v5/sms/addTemplate',headers={'authkey':auth},
                    files={k:(None,v) for k,v in {'template':row['text'],'sender_id':row['sender'],
                    'template_name':row['sender']+'_Care_'+row['language'].replace('@','_')+'_'+row['kind'],
                    'smsType':'UNICODE'}.items()},timeout=30)
                response.raise_for_status(); result=response.json()
                ident=result.get('data',{}).get('template_id')
                if result.get('status')!='success' or not ident:raise ValueError('Create not confirmed')
                row['template_id']=ident
            except Exception:
                print(json.dumps({'held_for_reconciliation':key}),flush=True)
                return
            with lock:
                db.execute('UPDATE registrations SET status=?,row_json=? WHERE key=?',('created',json.dumps(row),key));db.commit()
        with lock:
            count[0]+=1
            if count[0]%50==0:print(json.dumps({'registered':count[0],'planned':len(rows)}),flush=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(register,rows))
    expected={':'.join(row[x] for x in ('sender','language','kind')) for row in rows}
    exported={key:json.loads(value) for key,value in db.execute("SELECT key,row_json FROM registrations WHERE status='created'") if key in expected}
    # Registering one sender must not discard the other sender's active inventory.
    saved=json.loads((ROOT/'docs/msg91-localized-templates.json').read_text())
    exported={**{k:v for k,v in saved.items() if v.get('sender') not in args.senders},**exported}
    (ROOT/'docs/msg91-localized-templates.json').write_text(json.dumps(exported,ensure_ascii=False,indent=2)+'\n')
    mappings=[]
    for row in exported.values():
        for kind in (row['kind'],ALIASES.get(row['kind'])):
            if kind:mappings.append(('msg91',row['sender'],row['language'],kind,json.dumps(row,ensure_ascii=False)))
    with psycopg2.connect(dsn,connect_timeout=10) as conn:
        with conn.cursor() as cur:
            execute_values(cur,'''INSERT INTO after_order_sms_localizations(provider,sender,language,template_kind,mapping_json)
                VALUES %s ON CONFLICT(provider,sender,language,template_kind)
                DO UPDATE SET mapping_json=EXCLUDED.mapping_json''', mappings, page_size=200)
    print(json.dumps({'mapped':len(mappings),'journal_status':dict(db.execute('SELECT status,count(*) FROM registrations GROUP BY status'))}),flush=True)


if __name__=='__main__':main()
