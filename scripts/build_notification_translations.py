"""Offline static-catalog builder; never reads customer data or sends messages.

Uses the saved LibreDesk draft-only translation endpoint. Credentials remain in
macOS Keychain. Run with --apply to generate versioned catalogs; default lists
targets. Safe to resume: only missing/invalid catalogs are generated.
Development dependencies: requests, beautifulsoup4.
"""
import argparse
import ast
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess
import threading
import time

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'app/services/notification_locales'
FIELDS = re.compile(r'\{[a-z_]+\}|%s')

# These variants must not collapse to a language-only catalog.
EXACT = {'zh_CN': 'Chinese (Simplified)', 'zh_TW': 'Chinese (Traditional)',
         'zh_HK': 'Chinese (Traditional Hong Kong)', 'pt_BR': 'Portuguese (Brazil)',
         'pt_PT': 'Portuguese (Portugal)', 'pt_AO': 'Portuguese (Angola)',
         'de_CH': 'German (Switzerland)', 'ko_KP': 'Korean (North Korea)',
         'sr@Cyrl': 'Serbian (Cyrillic)', 'sr@latin': 'Serbian (Latin)'}


def targets(manifest):
    out = {}
    for code, row in manifest.items():
        if code.startswith(('en_', 'fr_')):
            continue
        key = code if code in EXACT else code.split('_')[0]
        name = EXACT.get(code) or row['name'].split('/')[0].strip()
        if key == 'es': name = 'Spanish'
        if key == 'nl': name = 'Dutch'
        if key == 'de': name = 'German'
        if key == 'ko': name = 'Korean'
        out[key] = name
    return out


def source_sections(addon):
    messages = json.loads((DIRECTORY / 'en.json').read_text())['messages']
    tree = ast.parse((ROOT / 'app/services/care_sms.py').read_text())
    render = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == 'render')
    summaries = ast.literal_eval(render.body[0].value)
    summaries.pop('price_difference')
    summaries['package_movement'] = 'Your parcel has a tracking update.'
    sms = {k: '{brand}: Order {order}. ' + v + ' {url}' for k,v in summaries.items()}
    addon_tree = ast.parse((addon / 'after_order_portal/models/notification_copy.py').read_text())
    french = ast.literal_eval(next(x.value for x in addon_tree.body if isinstance(x,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='FRENCH' for t in x.targets)))
    return {'messages': messages, 'sms': sms, 'payment': {k:k for k in french}}, french


def validate(source, value):
    if not isinstance(value,str) or not value.strip() or '<' in value or '>' in value or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', value):
        raise ValueError('Empty or unsafe translation')
    if Counter(FIELDS.findall(source)) != Counter(FIELDS.findall(value)):
        raise ValueError('Changed interpolation placeholders')
    source_numbers=Counter(re.findall(r'\d+', source))
    translated_numbers=Counter(re.findall(r'\d+', value))
    # Japanese/Vietnamese can naturally use a digit for these English words.
    # Do not weaken preservation of actual numeric deadlines or amounts.
    for word,digit in (('four','4'),('one','1')):
        if re.search(r'\b'+word+r'\b',source,re.I) and digit not in source_numbers:
            translated_numbers[digit]-=min(translated_numbers[digit],len(re.findall(r'\b'+word+r'\b',source,re.I)))
            if not translated_numbers[digit]:translated_numbers.pop(digit,None)
    if source_numbers != translated_numbers:
        raise ValueError('Changed numeric policy or amount')
    if '#CAREPH' in value or '__LD_KEEP_' in value:
        raise ValueError('Leaked protected token')


class Client:
    def __init__(self):
        def secret(account):
            return subprocess.check_output(['security','find-generic-password','-s',
                'codex.libredesk.chat.production','-a',account,'-w'],text=True).strip()
        self.url = secret('base-url') + '/api/v1/ai/draft-translation'
        self.headers = {'Authorization':'token '+secret('api-key')+':'+secret('api-secret')}
        self.lock = threading.Lock()
        self.next_request = 0

    def translate(self, name, texts):
        protected = []
        mappings = []
        for text in texts:
            values = {}
            def replace(match):
                token = '#CAREPH' + str(len(values)) + 'X'
                values[token] = match.group()
                return token
            # Avoid an upstream draft-renderer ampersand encoding defect.
            # A standalone English conjunction is semantically identical;
            # the catalog key itself remains unchanged.
            protected.append(FIELDS.sub(replace,text.replace(' & ', ' and ')))
            mappings.append(values)
        payload = {'target_language':name, 'html':''.join('<p>'+html.escape(t)+'</p>' for t in protected)}
        for attempt in range(4):
            with self.lock:
                delay = max(0,self.next_request-time.monotonic())
                self.next_request = max(self.next_request,time.monotonic()) + 2.6
            if delay: time.sleep(delay)
            try:
                response = requests.post(self.url,headers=self.headers,json=payload,timeout=75)
                if response.status_code in (429,502,503,504):
                    time.sleep(5*(attempt+1)); continue
                response.raise_for_status()
                paragraphs = BeautifulSoup(response.json()['data']['html'],'html.parser').find_all('p')
                if len(paragraphs) != len(texts): raise ValueError('Changed segment count')
                result = []
                for original, p, mapping in zip(texts,paragraphs,mappings):
                    translated = p.get_text()
                    for token, value in mapping.items():
                        if translated.count(token) != 1: raise ValueError('Missing protected token')
                        translated = translated.replace(token,value)
                    validate(original,translated)
                    result.append(translated)
                return result
            except (requests.RequestException,ValueError,KeyError):
                if attempt == 3: break
                time.sleep(3*(attempt+1))
        # Isolate difficult segments; never accept a partial catalog.
        if len(texts)>1:
            middle=len(texts)//2
            return self.translate(name,texts[:middle])+self.translate(name,texts[middle:])
        raise ValueError('Translation failed validation for '+name+': '+texts[0][:60])


def build(client, code, name, source, fingerprint):
    path = DIRECTORY / (code+'.json')
    previous={}
    if path.exists():
        previous = json.loads(path.read_text())
        if previous.get('source_sha256') == fingerprint and previous.get('complete'):
            for section,entries in source.items():
                for key,value in entries.items(): validate(value,previous[section][key])
            return code, 'already complete'
    flattened = [(section,key,value) for section,entries in source.items() for key,value in entries.items()]
    result = {section:{} for section in source}
    cache=DIRECTORY.parent.parent.parent/'.translation-cache'/(code+'.json')
    if cache.exists():
        saved=json.loads(cache.read_text())
        if saved.get('source_sha256')==fingerprint:result=saved['sections']
    # Small batches avoid model truncation and preserve script-heavy languages.
    chunk=[]; length=0
    def consume(rows):
        translations=client.translate(name,[row[2] for row in rows])
        for (section,key,_),value in zip(rows,translations): result[section][key]=value
        cache.parent.mkdir(exist_ok=True)
        cache.write_text(json.dumps({'source_sha256':fingerprint,'sections':result},ensure_ascii=False))
    for row in flattened:
        if row[1] in result[row[0]]:
            validate(row[2],result[row[0]][row[1]])
            continue
        if chunk and (length+len(row[2])>(700 if code=='dv' else 3600) or len(chunk)>=(10 if code=='dv' else 60)):
            consume(chunk);chunk=[];length=0
        chunk.append(row);length+=len(row[2])
    if chunk: consume(chunk)
    output={'version':code+'-20260921-v2','complete':True,
            'translation_method':'machine_generated','human_reviewed':False,
            'source_sha256':fingerprint,
            **{k:previous[k] for k in ('delivery_blocked','review_reason') if k in previous},**result}
    path.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return code, 'generated'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--addon',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--only',nargs='*')
    parser.add_argument('--repair',action='store_true',help='Regenerate invalid individual strings, preserving review holds')
    args=parser.parse_args()
    manifest=json.loads((DIRECTORY/'manifest.json').read_text())
    selected=targets(manifest)
    if args.only: selected={k:v for k,v in selected.items() if k in args.only}
    print('Catalog targets:',len(selected),flush=True)
    if not args.apply:
        print(json.dumps(selected,ensure_ascii=False,indent=2));return
    source,french=source_sections(args.addon)
    fingerprint=hashlib.sha256(json.dumps(source,sort_keys=True).encode()).hexdigest()
    client=Client()
    if args.repair:
        for code,name in selected.items():
            path=DIRECTORY/(code+'.json')
            if not path.exists():continue
            data=json.loads(path.read_text());changed=False
            for section,values in source.items():
                for key,value in list(data.get(section,{}).items()):
                    try:validate(values[key],value)
                    except ValueError:
                        data[section][key]=client.translate(name,[values[key]])[0]
                        changed=True
            if changed:
                path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                print(code,'repaired',flush=True)
        return
    failures=[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        tasks={pool.submit(build,client,code,name,source,fingerprint):code for code,name in selected.items()}
        for future in as_completed(tasks):
            try: print(*future.result(),flush=True)
            except Exception as exc:
                failures.append(tasks[future]);print(tasks[future],'FAILED',str(exc)[:160],flush=True)
    print('Failed catalogs:',failures,flush=True)
    if failures: raise SystemExit(1)


if __name__=='__main__': main()
