"""Read-only login email matching. Never persist or log login links."""
import base64
import json
import re
import time
from html import unescape
from urllib.parse import urlsplit, parse_qs
from .relay_policy import authenticate_forwarder


def login_subject(message):
    return 'securely log in to relay' in str(message.get('subject', '')).casefold()


def login_link(message, settings, journey, email, now=None):
    now = time.time() if now is None else now
    authenticate_forwarder(message, settings)
    from email.utils import parseaddr
    if parseaddr(message.get('from', ''))[1].lower() != email.lower():
        raise ValueError('Login forwarder differs from login account')
    if not login_subject(message):
        return None
    raw = unescape(str(message.get('text') or '') + '\n' + str(message.get('html') or ''))
    if len(raw) > 2000000:
        raise ValueError('Login message too large')
    links = set()
    for candidate in re.findall(r'https://[^\s<>"\']+', raw):
        candidate = candidate.rstrip('.,)>')
        for _ in range(3):
            u = urlsplit(candidate)
            if u.hostname and u.hostname.endswith('.safelinks.protection.outlook.com'):
                candidate = parse_qs(u.query).get('url', [''])[0]
                continue
            break
        u = urlsplit(candidate)
        if u.scheme != 'https' or u.netloc != 'app.relayfi.com' or u.path != '/magic-link/confirm' or u.fragment:
            continue
        q = parse_qs(u.query)
        if q.get('journeyId') != [journey] or len(q.get('token', [])) != 1:
            continue
        # Email authentication + exact server-issued journey bind the request.
        # Relay verifies the JWT signature; we only reject stale/wrong-purpose links here.
        try:
            token=q['token'][0];parts=token.split('.')
            if len(parts)!=3:continue
            claims=json.loads(base64.urlsafe_b64decode(parts[1]+'='*(-len(parts[1])%4)))
            if not isinstance(claims,dict):continue
            issued=float(claims['iat']);expires=float(claims['exp'])
            if claims.get('flow')!='confirm' or not now-300 <= issued <= now+30 or not now < expires <= issued+900:
                continue
        except (KeyError,ValueError,TypeError):
            continue
        links.add(candidate)
    if len(links)>1:
        raise ValueError('Multiple login links match this attempt')
    return next(iter(links), None)


def recent_login_summaries(listing, now=None):
    from datetime import datetime
    now = time.time() if now is None else now
    candidates=[]
    for row in listing.get('data', []):
        if not login_subject(row):continue
        try:
            stamp=datetime.fromisoformat(row['created_at'].replace('Z','+00:00')).timestamp()
        except (KeyError, TypeError, ValueError):continue
        if now-600 <= stamp <= now+30:candidates.append((stamp,row))
    return [row for stamp,row in sorted(candidates,key=lambda v:v[0],reverse=True)[:5]]
