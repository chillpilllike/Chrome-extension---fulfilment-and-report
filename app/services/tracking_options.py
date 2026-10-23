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
