#!/usr/bin/env python3
"""Activate prepared website assistants only after checking their scoped tool configuration."""
import copy,json,sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from importlib.util import spec_from_file_location,module_from_spec
spec=spec_from_file_location('prepare',Path(__file__).with_name('prepare.py') if Path(__file__).with_name('prepare.py').exists() else Path(__file__).with_name('prepare-active-order-sites.py'))
p=module_from_spec(spec);spec.loader.exec_module(p)
api=p.api
rows=json.loads(Path(sys.argv[1]).read_text())
boxes={b['id']:b for b in api('GET','/inboxes')}
tools={t['id']:t for t in api('GET','/ai/tools')}
assistants=api('GET','/ai/assistants')
rules=api('GET','/automations/rules?type=new_conversation')
byname={r['name']:r for r in rules}
active=[r for r in rows if boxes.get(r['inbox_id'],{}).get('enabled') and boxes.get(r.get('continuity_inbox_id'),{}).get('enabled')]
def one(row):
 inbox=row['inbox_id'];stem='post_order_'+str(inbox)
 candidates=[]
 for brief in assistants:
  if brief.get('enabled') and brief['name']==row['domain']+' Order Support':candidates.append(brief)
 # Existing branded assistants are verified through their post-order tool membership below.
 if row['store_id']!=1:
  return None
 assert len(candidates)==1, 'Missing or duplicate assistant'
 a=api('GET','/ai/assistants/'+str(candidates[0]['id']))
 ids=a.get('tool_ids') or [t['id'] if isinstance(t,dict) else t for t in a.get('tools',[])]
 named={tools[i]['name']:tools[i] for i in ids}
 for action in ['status','resend']:
  t=named[stem+'_'+action]
  assert t['requires_verification'] and t['enabled']
  assert t['url']=='https://fulfilment.gofinch.com/api/public/support-post-order/%s/%s/%s/%s'%(row['store_id'],row['website_id'],inbox,action)
 for action in ['customer_match','orders','link_order','order_status']:
  t=named['store_'+str(inbox)+'_'+action]
  assert '/libredesk/order-tools/'+str(row['website_id'])+'/' in t['url']
  if action!='customer_match':assert t['requires_verification']
 assert 'dispatch.estimated_dispatch' in a['instructions']
 assert 'store_167_' not in a['instructions']
 name='Verified order support · '+row['domain']
 rule={'name':name,'description':'Assign only this website chat to its isolated order assistant.','type':'new_conversation','events':[],'enabled':True,'rules':[{'group_operator':'AND','groups':[{'logical_op':'AND','rules':[{'field':'inbox','field_type':'conversation','operator':'equals','value':str(inbox)}]}],'actions':[{'type':'assign_team','value':[str(a['fallback_team_id'])]},{'type':'set_sla','value':['2']},{'type':'assign_user','value':[str(a['user_id'])]}]}]}
 old=byname.get(name)
 if old:ident=old['id']
 else:
  inactive=copy.deepcopy(rule);inactive['enabled']=False;inactive['rules'][0]['groups'][0]['rules'][0]['value']='0'
  ident=api('POST','/automations/rules',inactive)['id']
 api('PUT','/automations/rules/'+str(ident),rule)
 saved=api('GET','/automations/rules/'+str(ident))
 assert saved['enabled'] and saved['rules']==rule['rules'], 'Routing readback mismatch'
 print('Activated',inbox,flush=True)
 return {'inbox_id':inbox,'assistant_id':a['id'],'rule_id':ident}
with ThreadPoolExecutor(max_workers=3) as pool:
 result=[r for r in pool.map(one,active) if r]
print(json.dumps({'activated':result}),flush=True)
