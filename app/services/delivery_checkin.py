"""Carrier evidence and conservative 24-hour check-in eligibility."""
import re
from datetime import datetime, timedelta, timezone


def carrier_moment(value):
    raw = str(value or '').strip()
    try:
        parsed = datetime.fromisoformat(raw.replace('Z','+00:00'))
    except ValueError:
        parsed = None
        for fmt in ('%m/%d/%Y %I:%M:%S %p','%m/%d/%Y %I:%M %p','%m/%d/%Y %H:%M:%S','%m/%d/%Y'):
            try:
                parsed=datetime.strptime(raw,fmt)
                if fmt=='%m/%d/%Y':
                    parsed=parsed.replace(hour=23,minute=59,second=59)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if len(raw)==10 and re.fullmatch(r'\d{4}-\d{2}-\d{2}',raw):
        parsed=parsed.replace(hour=23,minute=59,second=59)
    # Unknown carrier timezone: latest possible UTC instant, never assume UTC.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone(timedelta(hours=-12)))


def delivery_details(events):
    candidates=[]
    for event in events:
        status=str(event.get('status') or event.get('message') or '')
        if not re.search(r'\bdelivered\b',status,re.I) or re.search(r'not|attempt|unable|will be',status,re.I):
            continue
        raw=str(event.get('date') or event.get('timestamp') or '')
        moment=carrier_moment(raw)
        if moment:
            candidates.append((moment,raw,event))
    if not candidates:
        return {'delivery_datetime':'Not provided by carrier','delivery_location':'Not provided by carrier',
                'delivery_postal_code':'Not provided by carrier','delivery_checkin_due_at':''}
    moment,raw,event=max(candidates,key=lambda item:item[0])
    return {'delivery_datetime':raw,
            'delivery_location':str(event.get('location') or 'Not provided by carrier'),
            'delivery_postal_code':str(event.get('postal_code') or event.get('postalCode') or event.get('postcode') or event.get('zip') or 'Not provided by carrier'),
            'delivery_checkin_due_at':(moment+timedelta(hours=24)).astimezone(timezone.utc).isoformat()}


def require_due(details, now=None):
    raw=details.get('delivery_checkin_due_at')
    if not raw:
        raise ValueError('A dated carrier delivery event is required before sending the delivery check-in.')
    if (now or datetime.now(timezone.utc)) < datetime.fromisoformat(raw):
        raise ValueError('Delivery check-in is held until at least 24 hours after delivery. Eligible at '+raw)
