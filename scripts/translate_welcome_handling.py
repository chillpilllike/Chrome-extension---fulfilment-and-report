"""Emit an apply_patch for static handling-copy translations; sends no customer data."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from build_notification_translations import Client, targets, DIRECTORY

COPY = ['Handling time and dispatch', 'Please allow 2–3 days for handling before dispatch. Orders placed on Thursday or Friday are normally dispatched on Monday, as we do not dispatch on weekends. If Monday is a public holiday, dispatch resumes on the next business day. These are estimates; we’ll let you know if your order needs more time.']

if __name__ == '__main__':
    names = targets(json.loads((DIRECTORY/'manifest.json').read_text()))
    names.update(en='English',fr='French')
    client = Client()
    paths = [p for p in sorted(DIRECTORY.glob('*.json')) if p.stem!='manifest']
    def convert(path):
        data=json.loads(path.read_text())
        if all(key in data.get('messages',{}) for key in COPY):
            return ''
        if any(key in data.get('messages',{}) for key in COPY):
            raise ValueError('Partial handling translation requires manual review: '+path.name)
        translated=COPY if path.stem=='en' else client.translate(names[path.stem],COPY)
        lines=['*** Update File: '+str(path),'@@','   "messages": {']
        lines += ['+    '+json.dumps(key,ensure_ascii=False)+': '+json.dumps(value,ensure_ascii=False)+',' for key,value in zip(COPY,translated)]
        return '\n'.join(lines)
    with ThreadPoolExecutor(max_workers=5) as pool:
        patches=list(pool.map(convert,paths))
    print('*** Begin Patch\n'+'\n'.join(p for p in patches if p)+'\n*** End Patch')
