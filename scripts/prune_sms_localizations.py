"""Prune app mappings only; provider archival is tracked separately, never assumed."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import psycopg2
from psycopg2.extras import execute_values
from scripts.register_sms_catalogs import plan, ALIASES


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    audit=json.loads((root/'docs/sms-website-language-audit.json').read_text())
    if any(not s.get('ok') for s in audit['stores']) or (datetime.now(timezone.utc)-datetime.fromisoformat(audit['checked_at'])).total_seconds()>3600:
        raise ValueError('A complete website audit from the last hour is required.')
    scope={}
    for store in audit['stores']:
        for site in store['websites']:
            if site.get('sms_enabled'):scope.setdefault(site.get('sender'),set()).update(site['languages'])
    wanted={':'.join(r[x] for x in ('sender','language','kind')) for r in plan(['nutricity','GofinchKart'],scope)}
    registry=json.loads((root/'docs/msg91-localized-templates.json').read_text())
    removed={k:v for k,v in registry.items() if k not in wanted};kept={k:v for k,v in registry.items() if k in wanted}
    print(json.dumps({'keep_templates':len(kept),'remove_templates':len(removed),'apply':args.apply}),flush=True)
    if not args.apply:return
    archive=root/'docs/msg91-template-archive-manifest.json'
    manifest=json.loads(archive.read_text()) if archive.exists() else {}
    for k,row in removed.items():manifest.setdefault(k,{**row,'provider_archive_status':'pending'})
    archive.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    with psycopg2.connect(re.search(r'POSTGRES_URL=(\S+)',(root/'Dockerfile').read_text()).group(1)) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT sender,language,template_kind,mapping_json FROM after_order_sms_localizations FOR UPDATE')
            rows=cur.fetchall();targets=[]
            for sender,language,kind,value in rows:
                basekind=next((k for k,v in ALIASES.items() if v==kind),kind)
                key=':'.join((sender,language,basekind))
                if key not in removed:continue
                if json.loads(value).get('template_id')!=removed[key]['template_id']:
                    raise ValueError('Mapping changed since inventory; cleanup stopped.')
                targets.append((sender,language,kind))
            deleted=0
            if targets:
                result=execute_values(cur,"""DELETE FROM after_order_sms_localizations AS target
                    USING (VALUES %s) AS old(sender,language,kind)
                    WHERE target.provider='msg91' AND target.sender=old.sender
                      AND target.language=old.language AND target.template_kind=old.kind
                    RETURNING target.sender""",targets,page_size=500,fetch=True)
                deleted=len(result)
    (root/'docs/msg91-localized-templates.json').write_text(json.dumps(kept,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'removed_app_mappings':deleted,'provider_templates_pending_archive':len(manifest)}),flush=True)


if __name__=='__main__':main()
