"""Privacy-safe destination text shared by refund review and confirmation email."""
def destination_label(bank):
    account = str(bank.get('iban') or bank.get('account_number') or '').replace(' ', '')
    if account:
        return 'Account ending ' + account[-4:]
    value = str(bank.get('account_routing_value1') or '')
    kind = bank.get('account_routing_type1')
    prefix = 'PayID ' if bank.get('local_clearing_system') == 'NPP' else ''
    if kind == 'email_address' and '@' in value:
        name, domain = value.rsplit('@', 1)
        return prefix + name[:1] + '•••@' + domain
    if kind == 'phone_number' and value:
        return prefix + 'phone ending ' + value[-4:]
    if kind in {'australian_business_number', 'organisation_identifier'} and value:
        label = 'ABN' if kind == 'australian_business_number' else 'organisation ID'
        return prefix + label + ' ending ' + value[-4:]
    if bank.get('account_routing_value2') and not account:
        return 'Recipient reference ending ' + str(bank['account_routing_value2'])[-4:]
    return 'Verified recipient account'
