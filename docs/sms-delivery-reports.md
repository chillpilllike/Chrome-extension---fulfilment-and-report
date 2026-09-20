# SMS log and delivery reports

The Email log page includes an expandable SMS log, showing messages created or
updated within 30 days. Older records are retained, not deleted. Each new send
attempt has its own provider reference and result. Legacy records keep their
existing aggregate attempt count; missing historical timestamps are not invented.

Initial live approval and manual retries validate the current order, recipient,
website, consent/suppression, test destination and source event again. Maximum
three attempts. Confirmed failures can be retried. Only confirmed delivered
messages can be explicitly resent with duplicate-charge confirmation. Once-only
movement/review/issue acknowledgements cannot be duplicated. Uncertain or pending
sends cannot be blindly resent. Financial-event SMS remains held pending the
verified adapter. Welcome emails and reminders intentionally do not create SMS.

## MSG91 delivery report connection

1. Set a random runtime secret (at least 32 characters) as
   `MSG91_WEBHOOK_SECRET` in Coolify. Keep it out of Git and URLs.
2. MSG91 → SMS → Webhook (New): create an **On Report Received** webhook.
3. POST JSON to
   `https://fulfilment.gofinch.com/api/public/after-order-webhooks/msg91`.
4. Add the header `X-MSG91-Webhook-Secret` with the same secret.
5. Use this body (one report per recipient):

```json
{
  "requestId": "{{requestId}}",
  "telNum": "{{telNum}}",
  "status": "{{status}}",
  "deliveryTime": "{{deliveryTime}}",
  "failureReason": "{{failureReason}}",
  "credit": "{{credit}}",
  "smsLength": "{{smsLength}}"
}
```

Provider statuses: 0 sent, 1 delivered, 2 failed, 9/17/20 blocked,
16/25 rejected. Request ID and full recipient must match. Duplicate reports are
idempotent. Late sent reports cannot overwrite terminal results. Refresh in the
SMS preview reconciles stored reports, including reports arriving before the
send acknowledgment. Until connected, provider acceptance is shown honestly as
accepted, not delivered. Unknown references do not initiate or authorize sending.

Source: https://msg91.com/help/webhook-new/how-to-receive-sms-delivery-reports-via-webhook-new

## Reviewed Resend HTTP 403 recovery

Payment relay emails have a separate retry path. Every previous attempt must be
an explicit 403 rejection with no provider message ID. The fresh Odoo payment
snapshot must still match and require customer action. A preview digest and
explicit override are required for an email held by a previous queue reset.
The retry is audited and receives a new idempotency generation. Subsequent
transport retries use that same payload/key. Other uncertain sends remain held.
