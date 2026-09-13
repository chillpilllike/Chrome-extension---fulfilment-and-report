#!/usr/bin/env python3
"""Read-only coverage and unauthenticated boundary audit; never sends customer messages."""
import json,sys,importlib.util
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import requests
sys.path.insert(0,'/app')
from app.support.post_order_chat import scope_key
spec=importlib.util.spec_from_file_location('prepare',Path(__file__).with_name('prepare.py') if Path(__file__).with_name('prepare.py').exists() else Path(__file__).with_name('prepare-active-order-sites.py'))
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p);api=p.api
rows=json.loads(Path(sys.argv[1]).read_text())
rows.append({'store_id':8,'website_id':1,'inbox_id':1,'continuity_inbox_id':2})
rows=list({r['inbox_id']:r for r in rows}.values())
boxes={b['id']:b for b in api('GET','/inboxes')};tools={t['id']:t for t in api('GET','/ai/tools')}
with ThreadPoolExecutor(max_workers=3) as pool:assistants=list(pool.map(lambda b:api('GET','/ai/assistants/'+str(b['id'])),api('GET','/ai/assistants')))
rules=api('GET','/automations/rules?type=new_conversation')
def one(row):
 i=row['inbox_id'];name='post_order_'+str(i)+'_status'
 matches=[a for a in assistants if a.get('enabled') and any(tools.get(t,{}).get('name')==name for t in a.get('tool_ids',[]))]
 assert len(matches)==1, 'Assistant coverage '+str(i)
 a=matches[0];assert 'dispatch.estimated_dispatch' in a['instructions']
 matched=[]
 for rule in rules:
  if not rule.get('enabled'):continue
  for block in rule.get('rules',[]):
   condition=any(c.get('field')=='inbox' and c.get('operator')=='equals' and str(c.get('value'))==str(i) for g in block.get('groups',[]) for c in g.get('rules',[]))
   assigned=any(x.get('type')=='assign_user' and str(a['user_id']) in list(map(str,x.get('value',[]))) for x in block.get('actions',[]))
   if condition and assigned:matched.append(rule['id'])
 assert matched,'Missing scoped route '+str(i)
 t=next(tools[t] for t in a['tool_ids'] if tools[t]['name']==name)
 h={'X-Postorder-Key':scope_key(row['store_id'],row['website_id'],i),'X-Libredesk-Inbox-Id':str(i),'X-Libredesk-Contact-Verified':'false'}
 r=requests.post(t['url'],headers=h,json={},timeout=30)
 assert r.status_code==403, 'Unverified boundary '+str(i)+' '+str(r.status_code)
 return {'inbox_id':i,'assistant_id':a['id'],'routing_rule_ids':matched,'unverified_blocked':True}
active=[r for r in rows if boxes.get(r['inbox_id'],{}).get('enabled') and boxes.get(r.get('continuity_inbox_id'),{}).get('enabled')]
with ThreadPoolExecutor(max_workers=3) as pool:out=list(pool.map(one,active))
print(json.dumps({'verified_active_websites':len(out),'websites':out}),flush=True)
