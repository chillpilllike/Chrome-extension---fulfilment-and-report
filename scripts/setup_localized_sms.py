"""Register fixed-brand MSG91 translations, never send SMS.

Defaults to a dry run. --apply creates templates; record returned public IDs in
docs/msg91-localized-templates.json before any rerun. Provider approval remains
mandatory and is checked by the application before use.
"""
import argparse
import json
import subprocess
from pathlib import Path

import requests


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--language',required=True)
    parser.add_argument('--sender',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    from app.services.notification_i18n import catalog,normalize_language
    language=normalize_language(args.language)
    data=catalog(language)
    if not language or not data.get('complete') or not data.get('sms'):
        raise ValueError('A complete localized catalog with SMS copy is required.')
    existing=Path('docs/msg91-localized-templates.json')
    saved=json.loads(existing.read_text()) if existing.exists() else {}
    auth=None
    if args.apply:
        def secret(account):
            return subprocess.check_output(['security','find-generic-password','-s',
                'codex.coolify.185.194.236.161','-a',account,'-w'],text=True).strip()
        response=requests.get(secret('coolify-host')+'/api/v1/applications/'+secret('coolify-app')+'/envs',
            headers={'Authorization':'Bearer '+secret('coolify-api')},timeout=30)
        response.raise_for_status()
        auth=next(x['value'] for x in response.json() if x['key']=='MSG91_AUTH_KEY' and not x.get('is_preview'))
    brand='Nutricity' if args.sender=='nutricity' else args.sender
    for kind,source in data['sms'].items():
        key=args.sender+':'+language+':'+kind
        if key in saved:
            continue
        text=source.format(brand=brand,order='##order##',url='##url##')
        row={'sender':args.sender,'language':language,'kind':kind,'text':text,'sms_type':'UNICODE'}
        if args.apply:
            response=requests.post('https://control.msg91.com/api/v5/sms/addTemplate',headers={'authkey':auth},
                files={k:(None,v) for k,v in {'template':text,'sender_id':args.sender,
                    'template_name':args.sender+'_Care_'+language+'_'+kind,'smsType':'UNICODE'}.items()},timeout=30)
            response.raise_for_status()
            result=response.json()
            if result.get('status')!='success' or not result.get('data',{}).get('template_id'):
                raise RuntimeError('MSG91 did not confirm template creation; inspect its dashboard before retrying.')
            row['template_id']=result['data']['template_id']
        print(json.dumps({key:row},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
