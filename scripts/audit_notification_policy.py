"""Back-translation screening of static policy copy (not a human review)."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import re
from build_notification_translations import Client, DIRECTORY

KEY='If you make no choice within 3 days, your order will be sent to our team for cancellation and refund review. Cancellation and any refund require team approval; neither happens automatically.'


def main():
    rows=[]
    for path in sorted(DIRECTORY.glob('*.json')):
        if path.stem in ('manifest','en','fr'):continue
        data=json.loads(path.read_text())
        rows.append((path.stem,data['messages'][KEY]))
    client=Client()
    def batch(group):
        values=client.translate('English',[value for _,value in group])
        result=[]
        for (code,original),value in zip(group,values):
            checks={'deadline':bool(re.search(r'3\s*(?:calendar\s*)?days?',value,re.I)),
                    'refund':bool(re.search(r'refund|reimburse',value,re.I)),
                    'cancellation':bool(re.search(r'cancel',value,re.I)),
                    'approval':bool(re.search(r'approv|authoriz',value,re.I)),
                    'automatic':bool(re.search(r'automatic',value,re.I))}
            result.append({'catalog':code,'source_key':KEY,'back_translation':value,'checks':checks,
                           'manual_review_required':not all(checks.values())})
        return result
    report=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        tasks=[pool.submit(batch,rows[i:i+8]) for i in range(0,len(rows),8)]
        for task in as_completed(tasks):
            for row in task.result():
                report.append(row)
                if row['manual_review_required']:print(row['catalog'],row['back_translation'],flush=True)
    report.sort(key=lambda r:r['catalog'])
    path=Path('docs/notification-policy-backtranslation.json')
    path.write_text(json.dumps({'method':'automated_screening_not_human_review','results':report},ensure_ascii=False,indent=2)+'\n')
    print('Screened',len(report),'catalogs.',flush=True)


if __name__=='__main__':main()
