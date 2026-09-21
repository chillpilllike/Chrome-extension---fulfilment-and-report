"""Validate complete language coverage and sync payment-only catalogs to Odoo.

Generated JSON is static copy, not customer data. Does not deploy or send.
"""
import argparse
import json
from pathlib import Path
from build_notification_translations import DIRECTORY, source_sections, targets, validate


def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--addon',type=Path,required=True)
    args=parser.parse_args()
    manifest=json.loads((DIRECTORY/'manifest.json').read_text())
    source,french=source_sections(args.addon)
    selected=targets(manifest)
    # Validate everything before changing the registry or addon.
    all_catalogs={}
    for code in selected:
        data=json.loads((DIRECTORY/(code+'.json')).read_text())
        held=data.get('delivery_blocked') and data.get('review_reason')
        if not data.get('complete') and not held: raise ValueError('Incomplete: '+code)
        for section,values in source.items():
            if not held and set(data[section])!=set(values):raise ValueError('Incomplete section: '+code+'/'+section)
            for key,value in data[section].items():validate(values[key],value)
        all_catalogs[code]=data
    for code in ('en','fr'):
        data=json.loads((DIRECTORY/(code+'.json')).read_text())
        data['payment']=source['payment'] if code=='en' else french
        if code=='en':data['sms']=source['sms']
        all_catalogs[code]=data
    for code,data in all_catalogs.items():
        save(DIRECTORY/(code+'.json'),data)
        payment={k:v for k,v in data.items() if k not in {'messages','sms'}}
        save(args.addon/'after_order_portal/notification_locales'/(code+'.json'),payment)
    for code,row in manifest.items():
        key=code if code in all_catalogs else code.split('_')[0]
        row['catalog']=key
        row['status']='needs_native_review' if all_catalogs[key].get('delivery_blocked') else 'machine_translated' if all_catalogs[key].get('translation_method')=='machine_generated' else 'translated'
    save(DIRECTORY/'manifest.json',manifest)
    save(args.addon/'after_order_portal/notification_locales/manifest.json',manifest)
    print('Synced',len(manifest),'Odoo locales using',len(all_catalogs),'catalogs.')


if __name__=='__main__':main()
