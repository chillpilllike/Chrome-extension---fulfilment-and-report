"""Read-only audit: missing ASIN evidence is a review flag, not proof of corruption.

Run with PYTHONPATH=. python scripts/audit_amazon_bindings.py --output report.json
Never repairs orders or queues purchases. Candidate IDs need independent review.
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from app.main import amazon_history_order_refs_from_text, order_line_asin_aliases
from app.db.session import db


def audit():
    with db() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        lines = conn.execute("""SELECT id,store_id,odoo_order_id,odoo_order_name,asin,
            replacement_asin,original_asin,asin_from_reference,asin_from_note,
            amazon_order_id,amazon_account_name,state,tracking_status,odoo_order_date,updated_at
            FROM order_lines WHERE COALESCE(amazon_order_id,'')!=''
            AND state NOT IN ('cancelled','refunded')""").fetchall()
        histories = conn.execute("""SELECT amazon_order_id,recipient,status,asins_json,
            order_date,first_seen_at,last_seen_at FROM amazon_order_history_unmatched""").fetchall()
    by_id = {h['amazon_order_id']: h for h in histories}
    by_ref = defaultdict(list)
    for history in histories:
        for ref in amazon_history_order_refs_from_text(history['recipient']):
            by_ref[ref].append(history)
    flags = []
    for line in lines:
        history = by_id.get(line['amazon_order_id'])
        if not history:
            continue
        asins = set(json.loads(history['asins_json'] or '[]'))
        aliases = set(order_line_asin_aliases(line))
        if not asins or aliases.intersection(asins):
            continue
        candidates = [h for h in by_ref[line['odoo_order_name'].upper()]
                      if 'cancel' not in (h['status'] or '').lower()
                      and (line['replacement_asin'] or line['asin']) in json.loads(h['asins_json'] or '[]')]
        flags.append({**line, 'current_history': history, 'exact_asin_candidates': candidates,
                      'classification': 'candidate_for_review' if candidates else 'missing_evidence_or_substitution'})
    return {'summary': {'flagged_lines': len(flags),
                        'flagged_orders': len({(r['store_id'],r['odoo_order_id']) for r in flags}),
                        'recent_lines': sum((r['odoo_order_date'] or '') >= '2026-08-01' for r in flags),
                        'classification': dict(Counter(r['classification'] for r in flags))},
            'warning': 'Flags include possible substitutions and incomplete captures; do not bulk overwrite from this report.',
            'lines': flags}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = audit()
    Path(args.output).write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps(result['summary'], indent=2))
    print('NC24079:', json.dumps([r for r in result['lines'] if r['odoo_order_name']=='NC24079']))
