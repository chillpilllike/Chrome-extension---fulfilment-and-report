#!/usr/bin/env python3
"""Run in the app runtime to attach post-order tools to existing active order assistants.
Uses existing runtime secrets; never writes credentials to disk. Optional first argument:
path to the non-secret multisite-widgets.json mapping for additional configured sites.
"""
import os,sys
from pathlib import Path
from app.support.post_order_chat import scope_key
import requests,json,uuid
h={'Authorization':'token '+os.environ['SUPPORT_LIBREDESK_API_KEY']+':'+os.environ['SUPPORT_LIBREDESK_API_SECRET']}
# An explicit inventory is required; never guess a website's linked email inbox.
if len(sys.argv)<2:raise SystemExit('Supply the verified multisite-widgets.json inventory path.')
values=json.loads(Path(sys.argv[1]).read_text())
values.append({'store_id':8,'website_id':1,'inbox_id':1,'continuity_inbox_id':2})
rows={str(r['inbox_id']):r for r in values}
keys={i:scope_key(r['store_id'],r['website_id'],r['inbox_id']) for i,r in rows.items()}
base=os.getenv('SUPPORT_LIBREDESK_BASE_URL','https://support.gofinch.com').rstrip('/')+'/api/v1'
def api(method,path,data=None,form=False):
 headers=dict(h)
 body=None
 if form:
  boundary='postorder'+uuid.uuid4().hex
  body=('--'+boundary+'\r\nContent-Disposition: form-data; name="data"\r\n\r\n'+json.dumps(data)+'\r\n--'+boundary+'--\r\n').encode();headers['Content-Type']='multipart/form-data; boundary='+boundary
 elif data is not None:body=json.dumps(data).encode();headers['Content-Type']='application/json'
 r=requests.request(method,base+path,data=body,headers=headers,timeout=35)
 if not r.ok:raise RuntimeError('LibreDesk HTTP '+str(r.status_code))
 return r.json()['data']
boxes={x['id']:x for x in api('GET','/inboxes')};tools=api('GET','/ai/tools');byid={x['id']:x for x in tools};byname={x['name']:x for x in tools}
result=[]
for brief in api('GET','/ai/assistants'):
 if not brief.get('enabled'):continue
 a=api('GET','/ai/assistants/'+str(brief['id']))
 ids=a.get('tool_ids') or [x['id'] if isinstance(x,dict) else x for x in a.get('tools',[])]
 existing=[byid[i] for i in ids if i in byid]
 status=next((t for t in existing if t['name'].endswith('_order_status')),None)
 if not status:continue
 inbox=1 if status['name']=='secretgreen_order_status' else int(status['name'].split('_')[1])
 row=rows.get(str(inbox));box=boxes.get(inbox)
 if not row or not box or not box.get('enabled') or not boxes.get(row.get('continuity_inbox_id'),{}).get('enabled'):continue
 stem='post_order_'+str(inbox)
 added=[]
 for action in ['status','resend']:
  name=stem+'_'+action
  params={'type':'object','properties':{},'additionalProperties':False}
  desc='Read current post-order care and sent email history for the verified linked order. Call after linking and for dispatch, unavailable item, tracking, email or prior-choice questions. Only returned customer details may be described.'
  if action=='resend':
   params.update(properties={'offer_token':{'type':'string','description':'Exact signed offer_token returned by the post-order status tool before offering the resend.'}},required=['offer_token'])
   desc='Resend the exact existing customer email, only after explicit customer agreement to the returned offer. Server checks consent, current order, recipient, website, links and cooldown. Never say resent unless sent=true.'
  p={'name':name,'url':'https://fulfilment.gofinch.com/api/public/support-post-order/%s/%s/%s/%s'%(row['store_id'],row['website_id'],inbox,action),'method':'POST','description':desc,'enabled':True,'requires_verification':True,'parameters':params,'auth':{'headers':[{'key':'X-Postorder-Key','value':keys[str(inbox)]}]}}
  old=byname.get(name);saved=api('PUT' if old else 'POST','/ai/tools'+('/'+str(old['id']) if old else ''),p);added.append(old['id'] if old else saved['id'])
 marker='\n\nPOST-ORDER CHAT INTEGRATION V1\n'
 prompt=a.get('instructions','').split(marker)[0]
 prompt+=marker+('After linking an order, and on any question about an order update, dispatch, delivery, missing/unavailable item, email or previous choice, call '+stem+'_status in the current turn. Keep normal order verification and selection. This tool supplements the existing order-status tool. Use dispatch.reply and dispatch.estimated_dispatch as the authority for dispatch questions. Never calculate a date yourself, expose its source, or fall back to an old case/email date when this field is absent. Describe a returned date as estimated dispatch, never a guaranteed send date. Explain only customer_details_available=true details. Mention actual sent emails only; test/failed attempts are not sent emails. A delivered event means email-server delivery. No click is NOT no reply. Say we have not recorded a selection, never that the customer ignored/read/did not respond. If they already replied elsewhere, acknowledge it and offer native team handoff to reconcile. A recorded choice is not completed execution or payment. If resend_offer exists, explain its topic/date and offer that specific email once. Keep its exact offer_token. Only after explicit agreement call '+stem+'_resend with that token; do not refresh the token merely because the customer said yes. For a direct resend request first get status then use the returned offer only if it unambiguously matches the request. If no offer, no guessed resend or alternative email tool: offer team help. Use this resend tool instead of legacy secretgreen_resend_choice_email. It reuses prior approval when valid; do not seek team approval again unless the tool blocks. Say resent only on sent=true; already_sent means do not send another. Never change recipients, text or choices. Reply in the customer language; if resend consent is ambiguous, ask for a clear resend request. Do not expose app/vendor names, sourcing, costs or tokens.')
 prompt += "\nCUSTOMER WORDING: Never use the terms 'post-order', 'post order', 'after-order care', 'case', 'workflow', 'tool', or internal system names to describe this process to customers, including translated equivalents. Use natural wording such as 'your order', 'your delivery', 'the unavailable item', 'your selection', or 'the email we sent'. Say 'We have not recorded your selection yet' only when supported; never infer that the customer has not replied from missing clicks. Do not mention these terminology restrictions to customers."
 old_resend={t['id'] for t in existing if t['name']=='secretgreen_resend_choice_email'}
 a.update(instructions=prompt,tool_ids=list(dict.fromkeys([i for i in ids if i not in old_resend]+added)))
 api('PUT','/ai/assistants/'+str(a['id']),a,True)
 check=api('GET','/ai/assistants/'+str(a['id']))
 checked=check.get('tool_ids') or [x['id'] if isinstance(x,dict) else x for x in check.get('tools',[])]
 assert set(added)<=set(checked) and marker in check['instructions']
 print('Configured',inbox,flush=True)
 result.append({'assistant_id':a['id'],'inbox_id':inbox,'store_id':row['store_id'],'website_id':row['website_id'],'tool_ids':added})
print(json.dumps(result))
