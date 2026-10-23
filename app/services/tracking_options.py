"""Tracking sync options: empty legacy settings inherit their intended defaults."""
def boolean_option(value, *, default):
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'true', '1', 'yes', 'on'}:
            return True
        if normalized in {'false', '0', 'no', 'off'}:
            return False
        raise ValueError('Invalid tracking boolean option.')
    if isinstance(value, (bool, int)) and value in (0, 1):
        return bool(value)
    raise ValueError('Invalid tracking boolean option.')


def shopify_updated_window(start, end):
    """Quote complete UTC dates so Shopify cannot tokenize their time as search terms."""
    from datetime import timezone
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError('Shopify search boundaries must include a timezone.')
    if end < start:
        raise ValueError('Shopify search window ends before it starts.')
    lower = start.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    upper = end.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    return f"updated_at:>='{lower}' updated_at:<='{upper}'"
