"""Safe staff-facing explanations. Never echo upstream messages or submitted values."""
import re


class RefundProviderError(ValueError):
    """An already translated provider error, safe to retain in refund history."""


FIELDS = {
    'account_name': 'Account holder name', 'account_number': 'Bank account number',
    'iban': 'IBAN', 'swift_code': 'SWIFT / BIC', 'bank_country_code': 'Bank country',
    'account_currency': 'Recipient account currency', 'country_code': 'Address country',
    'postcode': 'Postal code', 'street_address': 'Street address', 'city': 'City',
    'state': 'State / province', 'personal_email': 'Email address',
    'personal_phone_number': 'Phone number', 'date_of_birth': 'Date of birth',
    'entity_type': 'Account holder type', 'transfer_method': 'Transfer method',
    'local_clearing_system': 'Local transfer method', 'transfer_amount': 'Refund amount',
    'source_currency': 'Pay from currency', 'transfer_currency': 'Refund currency',
    'account_routing_type1': 'Recipient identifier type', 'account_routing_type2': 'Recipient identifier type',
    'account_routing_value1': 'Bank routing or recipient identifier', 'account_routing_value2': 'Bank routing details',
    'security_question': 'Security question', 'security_question_answer': 'Security answer',
}
NAME_MISMATCH = 'The account holder name does not match the bank account. Ask the customer for the exact name registered with their bank.'
INSUFFICIENT = 'There are not enough funds in the selected currency. Choose another funding currency or ask finance to add funds.'
GENERAL = {
    'schema_definition_not_found': 'This combination of recipient country, currency and transfer method is not available. Choose another supported method.',
    '90301': NAME_MISMATCH, 'beneficiary_name_mismatch': NAME_MISMATCH,
    'account_name_mismatch': NAME_MISMATCH, 'not_matched': NAME_MISMATCH,
    '90101': 'The bank could not accept the account name or number. Confirm both with the customer.',
    '90201': 'The bank or branch code is incorrect. Confirm the routing details with the customer.',
    '90202': 'The SWIFT / BIC is incorrect. Confirm it with the customer’s bank.',
    '90203': 'The intermediary bank details could not be accepted. Ask finance to check them.',
    '90204': 'The bank details could not be accepted. Confirm them with the customer.',
    '90302': 'The receiving account does not support this refund currency. Confirm its supported currencies with the customer.',
    '90401': 'The recipient details could not be accepted. Confirm the customer’s name, address and bank details.',
    '90402': 'The recipient details contain an unsupported character. Check the spelling and punctuation.',
    '90501': 'The payment provider has blocked this transfer under its policy. Ask finance to contact Airwallex.',
    '90502': 'Airwallex has stopped this transfer for review. Ask finance to contact Airwallex.',
    '90604': INSUFFICIENT, 'balance_insufficient': INSUFFICIENT, 'insufficient_funds': INSUFFICIENT,
    '90701': 'The receiving bank account is closed. Ask the customer for another account.',
    '90702': 'The receiving bank account is inactive. Ask the customer to contact their bank.',
    '90703': 'The receiving bank account has restrictions. Ask the customer to contact their bank.',
    '90801': 'The customer requested that this payment be returned. Confirm the next step with them.',
    '90802': 'The receiving bank returned the payment. Ask the customer to check with their bank.',
    '91101': 'The bank did not accept the payment purpose. Ask finance to review it.',
    '91401': 'The payment provider encountered a problem. Check the transfer status before retrying.',
    '91402': 'The bank did not respond in time. Check the transfer status before retrying.',
    'duplicate_request_id': 'This refund request has already been submitted. Check its status in refund history; do not send it again.',
    'request_id_duplicate': 'This refund request has already been submitted. Check its status in refund history; do not send it again.',
    'currency_pair_invalid': 'This currency conversion is not supported. Choose another funding currency.',
    'source_currency_unsupported': 'This funding currency is not supported for the transfer. Choose another currency.',
    'transfer_currency_unsupported': 'This refund currency is not supported by the selected transfer method. Choose another supported method.',
    'transfer_method_unsupported': 'The selected transfer method is not supported for these bank details. Choose another method.',
    'beneficiary_type_unsupported': 'This recipient account type is not supported for the selected transfer.',
    'quote_expired': 'The exchange-rate quote has expired. Review the refund again for an updated estimate.',
    'service_unavailable': 'Airwallex is temporarily unavailable. Check the transfer status before retrying.',
    'workflow_invalid': 'The Airwallex approval setup needs attention. Ask the account administrator to review it.',
}
VALIDATION = {
    '001': 'This is required. Please fill it in.',
    '008': 'Use digits only.', '012': 'Remove spaces at the beginning and end.',
    '013': 'Remove spaces.', '014': 'Remove emojis.', '015': 'Enter an amount greater than zero.',
    '028': 'Enter a street address instead of a PO box.', '029': 'Enter a valid email address.',
    '030': 'The postal code does not match the selected address country. Check both fields.',
    '031': 'The receiving account currency must match the refund currency.',
    '032': 'The SWIFT / BIC does not match the selected bank country. Check both fields.',
    '033': 'Check the SWIFT / BIC with the customer’s bank.',
    '035': 'Check the bank code with the customer.',
    '036': 'The IBAN does not match the selected bank country. Select the country shown by the IBAN’s first two letters; keep the customer’s residential country unchanged.',
    '037': 'The amount exceeds the bank’s transfer limit. Reduce it or choose another supported method.',
    '038': 'The amount is below the bank’s transfer minimum. Choose another supported method; do not exceed the order’s refund limit.',
    '039': 'Confirm the bank details with the customer.',
    '040': 'Choose a supported transfer method for this account.',
    '042': 'The amount has too many decimal places for this currency.',
    '043': 'No transfer method is available for this bank country and refund currency.',
    '076': 'The account holder name must match the customer’s registered Chinese name.',
    '078': 'The recipient name, identity details, account or phone number does not match the bank’s records. Confirm them with the customer.',
    '082': 'The branch code does not match the bank. Check both fields.',
    '085': 'The account number does not match the selected bank.',
    '088': 'The transit number does not match the selected financial institution.',
    '090': 'Enter a value, not just spaces.',
    '091': 'Enter an IBAN or the required local bank account details.',
    '092': 'Enter the phone number with its international country code.',
}


def field_label(source):
    return FIELDS.get(str(source or '').split('.')[-1], 'Recipient details')


def readable_error(payload, status=None):
    payload = payload if isinstance(payload, dict) else {}
    code = str(payload.get('code') or '').lower()
    if code in GENERAL:
        return GENERAL[code]
    details = payload.get('details')
    errors = details.get('errors', []) if isinstance(details, dict) else []
    if isinstance(errors, list) and errors:
        messages = []
        for error in errors[:10]:
            if not isinstance(error, dict):
                continue
            field = field_label(error.get('source'))
            rule = str(error.get('code') or '').lower()
            explanation = GENERAL.get(rule) or VALIDATION.get(rule)
            params = error.get('params') if isinstance(error.get('params'), dict) else {}
            # Only bounded integer format requirements are safe to echo, never field values.
            def length(key):
                value = params.get(key)
                return value if type(value) is int and 0 < value <= 10000 else None
            if rule == '018' and length('length_min') and length('length_max'):
                explanation = f"Use between {length('length_min')} and {length('length_max')} characters."
            elif rule == '019' and length('length'):
                explanation = f"Use exactly {length('length')} characters."
            elif rule in {'018','019','020','021','022'}:
                explanation = 'Check the number of characters entered.'
            elif rule in {'002','003','004','005','006','007','009'}:
                explanation = 'Check the letters, digits and punctuation accepted for this field.'
            message = f'{field}: {explanation or "Check this field with the customer and correct its format."}'
            if message not in messages:
                messages.append(message)
        if messages:
            return '\n'.join(messages)
    if 'above' in code and 'limit' in code:
        return 'This amount exceeds an Airwallex transfer limit. Reduce it or ask finance to review the limit.'
    if 'below' in code and 'limit' in code:
        return 'This amount is below an Airwallex transfer minimum. Choose another supported method without exceeding the order’s refund limit.'
    if status in {401,403}:
        return 'The Airwallex connection needs administrator attention. Ask your administrator to check access.'
    if status == 429:
        return 'Airwallex is receiving too many requests. Wait a moment, then check the transfer status.'
    if status and status >= 500:
        return GENERAL['service_unavailable']
    if payload.get('source'):
        return f'{field_label(payload["source"])}: Airwallex could not accept this detail. Check it with the customer.'
    return 'Airwallex could not complete this request. Ask finance to check the reason in Airwallex before retrying.'


def transfer_failure(result):
    failure = result.get('failure')
    reason = result.get('failure_reason')
    if isinstance(failure, dict) and failure:
        return readable_error(failure)
    if isinstance(reason, dict) and reason:
        return readable_error(reason)
    if isinstance(reason, str) and reason:
        code = re.match(r'^\s*(\d{5}|[A-Z_]+)(?:\s*:|$)', reason, re.I)
        if code:
            return readable_error({'code':code.group(1)})
    if result.get('status') == 'FAILED':
        return 'The transfer failed. Ask finance to check the reason in Airwallex before sending a replacement.'
    return ''
