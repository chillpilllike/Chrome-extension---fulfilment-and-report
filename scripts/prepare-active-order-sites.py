#!/usr/bin/env python3
"""Prepare missing native website order assistants; routing is enabled separately after verification.
Run in the fulfilment runtime with an explicit verified inventory JSON as the argument.
"""
import copy,json,os,secrets,sys
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests
sys.path.insert(0,'/app')
from app.main import OdooClient,get_store

def api(method,path,data=None,form=False):
 h={'Authorization':'token '+os.environ['SUPPORT_LIBREDESK_API_KEY']+':'+os.environ['SUPPORT_LIBREDESK_API_SECRET']}
 args={'files':{'data':(None,json.dumps(data))}} if form else {'json':data}
 r=requests.request(method,'https://support.gofinch.com/api/v1'+path,headers=h,timeout=35,**args)
 if not r.ok:raise RuntimeError('LibreDesk HTTP '+str(r.status_code))
 return r.json()['data']

def prepare(inventory):
 boxes={x['id']:x for x in api('GET','/inboxes')}
 tools={x['id']:x for x in api('GET','/ai/tools')}
 assistants=api('GET','/ai/assistants')
 current={x['name']:x for x in assistants}
 template=api('GET','/ai/assistants/4')
 ids=template.get('tool_ids') or [x['id'] if isinstance(x,dict) else x for x in template.get('tools',[])]
 base_tools={tools[i]['name'].removeprefix('store_167_'):tools[i] for i in ids if i in tools and tools[i]['name'].startswith('store_167_')}
 actions=['customer_match','orders','link_order','order_status']
 assert all(a in base_tools for a in actions)
 byname={x['name']:x for x in tools.values()}
 targets=[r for r in inventory if r['store_id']==1 and boxes.get(r['inbox_id'],{}).get('enabled') and boxes.get(r.get('continuity_inbox_id'),{}).get('enabled')]
 def one(row):
  inbox=row['inbox_id'];website=row['website_id'];store=get_store(row['store_id']);odoo=OdooClient(store)
  site=odoo.search_read('website',[('id','=',website)],['name','libredesk_enabled','libredesk_inbox_id'],limit=1)
  if not site or not site[0].get('libredesk_enabled') or not site[0].get('libredesk_inbox_id'):raise RuntimeError('Website widget is not enabled')
  catalog=odoo.search_read('libredesk.website.inbox',[('id','=',site[0]['libredesk_inbox_id'][0])],['remote_id','uuid'],limit=1)
  if not catalog or catalog[0]['remote_id']!=inbox or catalog[0]['uuid']!=boxes[inbox]['uuid']:raise RuntimeError('Website catalog identity mismatch')
  name='libredesk_website.order_tools.'+str(website)
  config=json.loads(odoo.execute('ir.config_parameter','get_param',[name,'{}']) or '{}')
  if config and config.get('inbox_id')!=inbox:raise RuntimeError('Existing order-tool inbox conflicts')
  if not config:
   config={'inbox_id':inbox,'secret':secrets.token_urlsafe(40)}
   odoo.execute('ir.config_parameter','set_param',[name,json.dumps(config)])
  endpoint=store.odoo_url.rstrip('/')+'/libredesk/order-tools/'+str(website)
  h={'X-Libredesk-Order-Key':config['secret'],'X-Libredesk-Inbox-Id':str(inbox),'X-Libredesk-Conversation-UUID':'12345678-1234-1234-1234-123456789012','X-Libredesk-Contact-Email':'scope-test-20260913@example.invalid','X-Libredesk-Contact-Verified':'false'}
  for action in ['orders','link-order','order-status']:
   r=requests.post(endpoint+'/'+action,headers=h,json={},timeout=25)
   if r.status_code!=403:raise RuntimeError('Verification boundary failed '+str(r.status_code))
  r=requests.post(endpoint+'/customer-match',headers=h,json={},timeout=25)
  if r.status_code!=200 or r.json().get('has_orders') is not False:raise RuntimeError('Unmatched-email boundary failed')
  added=[]
  for action in actions:
   payload=copy.deepcopy(base_tools[action]);toolname='store_'+str(inbox)+'_'+action
   for field in ['id','created_at','updated_at']:payload.pop(field,None)
   payload.update(name=toolname,url=endpoint+'/'+action.replace('_','-'),auth={'headers':[{'key':'X-Libredesk-Order-Key','value':config['secret']}]})
   old=byname.get(toolname);new=api('PUT' if old else 'POST','/ai/tools'+('/'+str(old['id']) if old else ''),payload);added.append(old['id'] if old else new['id'])
  assistant_name=row['domain']+' Order Support';old=current.get(assistant_name)
  if old:return {'inbox_id':inbox,'assistant_id':old['id'],'existing':True}
  payload=copy.deepcopy(template)
  for field in ['id','user_id','created_at','updated_at','tools']:payload.pop(field,None)
  prompt=template['instructions'].split('\n\nPOST-ORDER CHAT INTEGRATION V1')[0]
  prompt=prompt.replace('store_167_','store_'+str(inbox)+'_').replace('nutrihub.ca',row['domain']).replace('Nutrihub',site[0]['name']).replace('NutriHub',site[0]['name'])
  payload.update(name=assistant_name,instructions=prompt,tool_ids=added,enabled=True,languages=[])
  new=api('POST','/ai/assistants',payload,True)
  return {'inbox_id':inbox,'assistant_id':new['id'],'assistant_user_id':new['user_id'],'domain':row['domain'],'scope_tests_passed':True}
 results=[];errors=[]
 with ThreadPoolExecutor(max_workers=3) as pool:
  futures={pool.submit(one,row):row for row in targets}
  for f in as_completed(futures):
   row=futures[f]
   try:results.append(f.result());print('Prepared',row['inbox_id'],flush=True)
   except Exception as e:errors.append({'inbox_id':row['inbox_id'],'error':str(e) if isinstance(e,RuntimeError) else type(e).__name__});print('Needs review',row['inbox_id'],errors[-1]['error'],flush=True)
 print(json.dumps({'prepared':results,'errors':errors}),flush=True)
 return results,errors
if __name__=='__main__':
 from pathlib import Path
 _,errors=prepare(json.loads(Path(sys.argv[1]).read_text()))
 if errors:raise SystemExit(1)
