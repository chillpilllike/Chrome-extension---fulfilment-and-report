"""Register dedicated welcome templates; print non-secret provider results only.

Run manually by an operator. Does not send SMS or change application settings.
Credentials are loaded at runtime from the existing Coolify Keychain connection.
"""
import json
import re
import subprocess
from pathlib import Path

import psycopg2
import requests


def main():
    def credential(account):
        return subprocess.check_output(['security', 'find-generic-password', '-s',
            'codex.coolify.185.194.236.161', '-a', account, '-w'], text=True).strip()
    host = credential('coolify-host')
    env = requests.get(host + '/api/v1/applications/' + credential('coolify-app') + '/envs',
        headers={'Authorization': 'Bearer ' + credential('coolify-api')}, timeout=30)
    env.raise_for_status()
    auth = next(x['value'] for x in env.json() if x['key'] == 'MSG91_AUTH_KEY' and x.get('is_runtime') and not x.get('is_preview'))
    conn = psycopg2.connect(re.search(r'POSTGRES_URL=(\S+)', Path('Dockerfile').read_text()).group(1))
    conn.set_session(readonly=True)
    with conn.cursor() as cursor:
        cursor.execute("SELECT value FROM app_settings WHERE key='after_order_sms_mappings'")
        mappings = cursor.fetchone()[0]
        mappings = json.loads(mappings) if isinstance(mappings, str) else mappings
    conn.close()
    senders = sorted({site['msg91']['sender'] for site in mappings.values() if site.get('msg91', {}).get('sender')})
    # Read recorded non-secret IDs so an operator can resume a partially completed run.
    path = Path('docs/msg91-welcome-templates.json')
    result = json.loads(path.read_text()) if path.exists() else {}
    for sender in senders:
        if sender in result:
            continue
        brand = 'Nutricity' if sender == 'nutricity' else sender
        text = f'{brand}: Order ##order## confirmed. Thank you! Processing starts soon. View order: ##url## - {brand} Support'
        response = requests.post('https://control.msg91.com/api/v5/sms/addTemplate',
            headers={'authkey': auth}, files={key: (None, value) for key, value in {
                'template': text, 'sender_id': sender, 'template_name': sender + '_Care_new_order_welcome',
                'smsType': 'NORMAL'}.items()}, timeout=30)
        response.raise_for_status()
        data = response.json()
        print(sender, json.dumps(data), flush=True)
        if data.get('status') != 'success' or not data.get('data', {}).get('template_id'):
            raise RuntimeError('Template registration rejected. Inspect provider response.')
        # Operator records returned IDs through apply_patch before resuming.
        result[sender] = {'text': text, 'template_id': data['data']['template_id'], 'sender': sender}
    print('RESULT', json.dumps(result))


if __name__ == '__main__':
    main()
