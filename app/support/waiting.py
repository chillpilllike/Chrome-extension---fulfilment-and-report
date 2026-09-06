"""Durable, deduplicated two-minute human-wait acknowledgement via official API."""
import threading,time
from datetime import datetime,timezone
from app.support.live import stamp
ACK='Your chat has been saved as a support ticket. A team member will review it and respond to you shortly.'

def pending_customer(messages):
    public=[m for m in messages if not m.get('private') and m.get('type') in (None,'message','incoming','outgoing')]
    contacts=[m for m in public if m.get('sender_type')=='contact']
    if not contacts:return None
    latest=max(contacts,key=lambda m:stamp(m.get('created_at')))
    for m in public:
        text=m.get('text_content') or m.get('content') or ''
        automated=m.get('sender_id')==5 or bool((m.get('meta') or {}).get('ai_assistant_id')) or ACK in text or 'change request has been forwarded to our team for review' in text or 'cancellation/refund request has been saved on this support ticket' in text
        if m.get('sender_type')=='agent' and not automated and stamp(m.get('created_at'))>=stamp(latest.get('created_at')):return None
    return latest

class WaitMonitor:
    def __init__(self,db,libre):self.db=db;self.libre=libre;self.stop=threading.Event()
    def start(self):
        with self.db() as c:
            c.execute('CREATE TABLE IF NOT EXISTS support_wait_receipts (conversation_uuid TEXT PRIMARY KEY,message_uuid TEXT,due_at DOUBLE PRECISION NOT NULL,state TEXT NOT NULL)')
            c.execute("INSERT INTO support_wait_receipts VALUES('__enabled_since__','',?,'config') ON CONFLICT(conversation_uuid) DO NOTHING",(time.time(),))
        self.thread=threading.Thread(target=self.run,daemon=True,name='support-human-wait');self.thread.start()
    def run(self):
        while not self.stop.wait(10):
            try:self.tick()
            except Exception:pass # No customer claims are made when source/API checks fail.
    def tick(self):
        with self.db() as c:enabled=c.execute("SELECT due_at FROM support_wait_receipts WHERE conversation_uuid='__enabled_since__'").fetchone()['due_at']
        for page in range(1,6):
            listing=self.libre('/conversations/all?page_size=100&page='+str(page)+'&order=desc')
            for brief in listing.get('results',[]):
                if brief.get('inbox_channel')!='livechat' or brief.get('inbox_name')!='Secretgreen Support' or brief.get('assigned_user_id')==5:continue
                uuid=brief['uuid'];conv=self.libre('/conversations/'+uuid)
                if conv.get('inbox_id')!=1 or conv.get('status_category') in {'resolved','closed'} or conv.get('assigned_user_id')==5:continue
                messages=self.libre('/conversations/'+uuid+'/messages?page_size=100')['results'];latest=pending_customer(messages)
                if not latest:
                    with self.db() as c:c.execute("UPDATE support_wait_receipts SET state='cancelled' WHERE conversation_uuid=? AND state='pending'",(uuid,))
                    continue
                with self.db() as c:
                    old=c.execute('SELECT * FROM support_wait_receipts WHERE conversation_uuid=?',(uuid,)).fetchone()
                    if not old and stamp(latest['created_at']).timestamp()<enabled:continue
                    if old and old['state'] in {'sending','sent'}:continue # Once per conversation, no repeated reassurance spam.
                    if not old:
                        c.execute("INSERT INTO support_wait_receipts VALUES(?,?,?,'pending') ON CONFLICT(conversation_uuid) DO NOTHING",(uuid,latest['uuid'],time.time()+120));continue
                    if old['state']=='cancelled':
                        c.execute("UPDATE support_wait_receipts SET message_uuid=?,due_at=?,state='pending' WHERE conversation_uuid=?",(latest['uuid'],time.time()+120,uuid));continue
                    if time.time()<old['due_at']:continue
                    c.execute('SELECT pg_advisory_xact_lock(81910416)')
                    locked=c.execute('SELECT state FROM support_wait_receipts WHERE conversation_uuid=?',(uuid,)).fetchone()
                    if locked['state']!='pending':continue
                    c.execute("UPDATE support_wait_receipts SET state='sending' WHERE conversation_uuid=?",(uuid,))
                # Recheck immediately before queuing; genuine human replies suppress this acknowledgement.
                fresh=self.libre('/conversations/'+uuid)
                latest_messages=self.libre('/conversations/'+uuid+'/messages?page_size=100')['results']
                if fresh.get('assigned_user_id')==5 or fresh.get('status_category') in {'resolved','closed'} or not pending_customer(latest_messages):
                    with self.db() as c:c.execute("UPDATE support_wait_receipts SET state='cancelled' WHERE conversation_uuid=?",(uuid,))
                    continue
                self.libre('/conversations/'+uuid+'/messages',method='POST',data={'private':False,'sender_type':'agent','message':ACK})
                with self.db() as c:c.execute("UPDATE support_wait_receipts SET state='sent' WHERE conversation_uuid=?",(uuid,))
            if page>=listing.get('total_pages',1):break
