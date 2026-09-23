"""Shared, table-based customer email layout; no application or network access."""
from html import escape
from decimal import Decimal
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
from .notification_i18n import for_case


FONT = "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"


def safe_url(value):
    value = str(value or "").strip()
    return value if urlsplit(value).scheme in {"https", "http"} else ""


def storefront_product_url(value, domain):
    """Internal Odoo record URLs must never become customer product links."""
    parts = urlsplit(str(value or '').strip())
    host = str(domain or '').lower().removeprefix('www.')
    if (parts.scheme != 'https' or not host or parts.username or parts.password
            or (parts.hostname or '').lower().removeprefix('www.') != host
            or not parts.path.startswith('/shop/') or parts.fragment):
        return ''
    return value


def button(label, url, *, primary=True, destructive=False):
    url = safe_url(url)
    if not url:
        return ""
    color = "#ffffff" if primary else "#a33232" if destructive else "#25352f"
    background = "#153e35" if primary else "#ffffff"
    border = "#153e35" if primary else "#e7d9d9" if destructive else "#d9e2de"
    return f'''<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin:0 0 10px">
      <tr><td align="center" bgcolor="{background}" style="border:1px solid {border};border-radius:0;mso-padding-alt:15px 20px">
      <a href="{escape(url, quote=True)}" style="display:block;padding:15px 20px;border-radius:0;color:{color};font-family:{FONT};font-size:14px;line-height:20px;font-weight:600;text-align:center;text-decoration:none;mso-padding-alt:0;text-underline-color:{background}">{escape(label)}</a>
      </td></tr></table>'''


def render_after_order_email(case, action_url, *, actions, labels, template_kind="", unsubscribe_url="", review_url=""):
    t = for_case(case)
    context = case.get("context") or {}
    order = str(case.get("odoo_order_name") or "Your order")
    website = str(context.get("website_name") or case.get("store_name") or "Customer care")
    kind = template_kind or case.get("case_type") or "tracking"
    if kind == "tracking" and context.get("risk_state") == "suspected_lost":
        kind = "package_lost"
    content = {
        "refund_request_received": ("WE’RE HERE TO HELP", "Our team will review your request and help with the next steps.", "Our team will review your request and help with the next steps.", "Keep your order number handy if you contact our team.", "No further action needed", "Keep your order number handy if you contact our team."),
        "shopify_dispatch": ("DISPATCH UPDATE", "Follow your delivery", "Follow your delivery", "There’s a new update on your package. You can find the latest details below.", "Follow your delivery", "See the full tracking history and the latest carrier updates."),
        "manual_refund_completed": ("REFUND CONFIRMED", "Your refund has been processed.", "Your refund has been processed", "Our team has confirmed that your refund has been processed. The refund details are below.", "When will the credit appear?", "Please allow 24–48 hours for the credit to appear. Your bank or payment provider may take longer. If you need help, reply to this email."),
        "refund_confirmed": ("REFUND CONFIRMED", "Your refund has been sent.", "Your refund has been processed", "We’ve successfully sent a refund for your order. You’ll find the amount and destination below.", "When will the credit appear?", "The credit may appear in your account within 24–72 business hours. Timing depends on your bank. If it has not appeared after this time, reply to this email and our team will help."),
        "relay_request": ("PAYMENT REQUEST", "Your invoice is ready.", "Complete payment", "Complete your order securely using the payment button below. Your invoice will be charged in USD at the amount shown.", "Complete your payment", "Your payment link belongs to this order. If you return later, use this same link."),
        "relay_received": ("ORDER CONFIRMED", "Thank you for your payment.", "Payment received", "Relay has reported your payment initiation and your order is confirmed. Bank settlement is still processing.", "We’re here to help", "Keep your order number handy if you contact our team."),
        "new_order_welcome": ("THANK YOU", "Thank you for your order!", "Thank you for your order", "We’ve received your order and will begin processing it soon. We’re here to help whenever you need us.", "Here to help", "Keep your order number handy when contacting us so we can help you more quickly."),
        "warehouse_dispatch_delay": ("DISPATCH UPDATE", "An update on your dispatch.", "Your dispatch is taking longer than expected", "Your order’s dispatch has hit a hurdle, and our team has been notified. We expect to dispatch it within the next 24–48 hours.", "No action needed", "You don’t need to take any action. Our team is working to get your order on its way."),
        "item_unavailable": ("YOUR ORDER", "Let’s find your next best option.", "An item needs your choice", "Our team has prepared alternatives for an unavailable item in your order. Review the affected item below and choose how you’d like to continue.", "Choose what works for you", "You can change an alternative for 24 hours from your first selection. After that, a higher-priced choice requires payment of the difference; a cheaper choice is reviewed for a difference refund. If you remove an item, any amount charged for it will be refunded after our team reviews and confirms your request."),
        "expected_dispatch": ("DISPATCH UPDATE", "A quick update on your order.", "Your expected dispatch date", "Your order has a later expected dispatch date. Please let us know if you’d like to proceed or cancel.", "How would you like to continue?", "If we don’t hear from you, we’ll continue processing your order."),
        "delivery_confirmation": ("DELIVERY CHECK-IN", "Has your order arrived?", "Did your order arrive?", "The carrier has marked your package as delivered. Please take a moment to tell us whether you received it.", "Please confirm your delivery", "If it hasn’t arrived, let us know so our team can look into it."),
        "package_lost": ("WE’RE HERE TO HELP", "Let’s get this sorted.", "Your package needs attention", "Your package hasn’t had confirmed movement for a while. We’re sorry for the uncertainty. You can request a replacement or a refund below.", "What would you prefer?", "Our team will review your request and help with the next steps."),
        "trustpilot_review": ("YOUR FEEDBACK", "Your feedback means a lot.", "How was your experience?", "If you have a moment, we’d love to hear about your experience with your order.", "Share your experience", "Share an honest review — your feedback helps us improve."),
        "delivery_issue_received": ("WE’RE HERE TO HELP", "We’re looking into your delivery.", "We received your delivery report", "Thank you for letting us know your order hasn’t arrived. Our team will investigate the delivery and contact you shortly.", "No further action needed", "If your order arrives in the meantime, you can update your answer on your order page."),
        "tracking": ("ON ITS WAY", "A little closer to your door.", "Your package has moved", "There’s a new update on your package. You can find the latest details below.", "Follow your delivery", "See the full tracking history and the latest carrier updates."),
    }
    eyebrow, heading, subject_text, intro, action_heading, note = map(t, content.get(kind, content["tracking"]))
    if kind == 'refund_request_received':
        # Reuse the maintained refund-acknowledgement translation in every catalog,
        # rather than sending untranslated new financial prose to other locales.
        from app.services.notification_i18n import sms_translation
        intro, _ = sms_translation(t.language,'refund_request_received',website,order,'')
        intro = intro.strip()
    no_alternatives = kind == 'item_unavailable' and bool(context.get('no_alternative_line_ids')) and 'offer_alternatives' not in actions
    if no_alternatives:
        heading = t('An item in your order is unavailable.')
        intro = t('Our team checked sourcing options but could not find suitable alternatives for the unavailable item or items below. Please choose how you would like to continue.')
        note = t('Our team will review your choice before making changes or processing any refund.')
    if kind == "item_unavailable" and context.get('three_day_policy_enabled'):
        # Filtered actions, not row count: quantities/shipping/duplicate ASINs
        # do not establish that another fulfilable product will remain.
        removable = bool({"exclude_item_and_proceed", "cancel_affected_item"}.intersection(actions))
        affected = t("these unavailable items" if len(case.get("affected_items") or []) > 1 else "this unavailable item")
        outcome = (
            t("If you make no choice within 3 days, we will process the rest of your order without {affected}. Any amount paid for the removed items will go to our team for refund review and approval.", affected=affected)
            if removable else
            t("If you make no choice within 3 days, your order will be sent to our team for cancellation and refund review. Cancellation and any refund require team approval; neither happens automatically.")
        )
        note = (
            t("Please choose an option within 3 days of this notification.") + " " + outcome + " " + t(
            "Once you select an alternative, you have a separate 24-hour window from your first selection to change it. "
            "The 3-day no-response rule does not cancel a choice you have already made. "
            "After the 24-hour window, a higher-priced choice requires payment of the difference; "
            "a cheaper choice is reviewed for a difference refund. "
            "If you choose to remove an item, any amount paid for it is subject to our team's refund review and approval.")
        )
    if no_alternatives and context.get('three_day_policy_enabled'):
        note = t('Please choose an option within 3 days of this notification.') + ' ' + outcome
    subject = f"{order} — {subject_text}"
    preheader = f"{subject} — {intro}"
    logo_url = safe_url(context.get("website_logo_url"))
    logo = f'<img src="{escape(logo_url, quote=True)}" alt="{escape(website, quote=True)}" width="144" style="display:block;width:144px;max-width:100%;height:auto;border:0;color:#193b32;font-family:{FONT};font-size:18px;font-weight:600">' if logo_url else f'<span style="font-family:{FONT};font-size:20px;line-height:28px;font-weight:700;letter-spacing:-.6px;color:#193b32">{escape(website)}</span>'

    item_rows = []
    plain_items = []
    for item in case.get("affected_items") or []:
        name = str(item.get("product_name") or t("Order item"))
        if item.get('no_alternatives'):
            name += ' — ' + t('No suitable alternatives available')
        quantity = str(item.get("quantity") or 1)
        if quantity.endswith(".0"):
            quantity = quantity[:-2]
        thumbnail = safe_url(item.get("thumbnail_url"))
        product_url = storefront_product_url(item.get("odoo_product_url"),case.get('sender_domain'))
        image = f'<img src="{escape(thumbnail, quote=True)}" alt="{escape(name, quote=True)}" width="64" height="64" style="display:block;width:64px;height:64px;object-fit:contain;border:0;border-radius:0;background:#ffffff;font-size:10px;color:#66756e">' if thumbnail else '<span style="font-size:24px;color:#a4b1aa">&#9633;</span>'
        link = f'<a href="{escape(product_url, quote=True)}" style="font-family:{FONT};font-size:12px;line-height:20px;font-weight:500;color:#28594a;text-decoration:underline">{escape(t("View item"))}</a>' if product_url else ""
        item_rows.append(f'''<tr><td style="padding:0 0 10px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#eeeeee" style="background:#eeeeee;border-radius:0">
          <tr><td width="64" valign="middle" align="center" style="width:64px;padding:16px">{image}</td>
          <td valign="middle" style="padding:16px 16px 16px 0;font-family:{FONT};font-size:14px;line-height:21px;color:#24372e">
          <strong style="font-weight:600">{escape(name)}</strong><br><span style="font-size:12px;line-height:24px;color:#64756a">{escape(t('Quantity'))} {escape(quantity)}</span>{'<br>' + link if link else ''}
          </td></tr></table></td></tr>''')
        plain_items.append(f"{name} — {t('Quantity')} {quantity}")
        recommendations = item.get('recommendations') or []
        if kind == 'item_unavailable' and recommendations:
            item_rows.append('<tr><td style="padding:14px 0 10px;font-size:16px;font-weight:600">'+escape(t('Best alternatives for this item'))+'</td></tr>')
            for alternative in recommendations:
                title = escape(str(alternative.get('name') or t('Alternative product')))
                photo = safe_url(alternative.get('thumbnail_url'))
                details = safe_url(alternative.get('details_url'))
                selection = safe_url(alternative.get('select_url'))
                item_rows.append(f'''<tr><td style="padding:0 0 16px"><table role="presentation" width="100%" cellpadding="12" cellspacing="0" bgcolor="#eeeeee"><tr>
                  <td width="80"><a href="{escape(details, quote=True)}" target="_blank" rel="noopener noreferrer"><img src="{escape(photo, quote=True)}" alt="{title}" width="80" height="80" style="display:block;object-fit:contain;border:0;background:#ffffff"></a></td>
                  <td style="font-size:14px;line-height:22px"><strong>{title}</strong><br><a href="{escape(details, quote=True)}" target="_blank" rel="noopener noreferrer" style="color:#28594a;text-decoration:underline">{escape(t('View full product details'))}</a></td></tr>
                  <tr><td colspan="2">{button(t('Review and confirm this alternative'), selection)}</td></tr></table></td></tr>''')
                plain_items.append(f"{t('Recommended')}: {alternative.get('name')}\n{t('Product details')}: {details}\n{t('Review and confirm')}: {selection}")
    items_html = '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:28px">' + "".join(item_rows) + "</table>" if item_rows else ""

    panel = ""
    detail_lines = []
    if kind == 'manual_refund_completed':
        refund = context['refund']
        fields = [('Refund amount',str(refund['amount'])+' '+str(refund['currency'])),
                  ('Refund reference',refund['reference']),('Processed on',refund['completed_at'])]
        panel = '<table role="presentation" width="100%" cellpadding="12" cellspacing="0" bgcolor="#eeeeee" style="margin-top:28px">'
        for label,value in fields:
            label = t(label)
            panel += '<tr><td>'+escape(label)+'</td><td style="font-weight:600">'+escape(str(value))+'</td></tr>'
            detail_lines.append(label+': '+str(value))
        panel += '</table>'
    if kind == 'refund_confirmed':
        refund = context['refund']
        fields = [('Refund amount', format(Decimal(str(refund['amount'])).normalize(), 'f') + ' ' + str(refund['currency'])),
                  ('Credited to', refund.get('holder') or t('Your verified recipient')),
                  ('Destination account', refund.get('account') or t('Verified recipient account')),
                  ('Bank', refund.get('bank') or ''),
                  ('Refund reference', refund.get('reference') or '')]
        panel = '<table role="presentation" width="100%" cellpadding="12" cellspacing="0" bgcolor="#eeeeee" style="margin-top:28px">'
        for label, value in fields:
            label = t(label)
            if value:
                panel += '<tr><td style="font-size:13px;color:#637167">' + escape(label) + '</td><td style="font-size:14px;font-weight:600;word-break:break-word">' + escape(str(value)) + '</td></tr>'
                detail_lines.append(label + ': ' + str(value))
        panel += '</table>'
        detail_lines.append(t('Only the last four account characters are shown for your privacy.'))
    if kind == 'new_order_welcome':
        domain = str(case.get('sender_domain') or '').strip().lower()
        parsed_domain = urlsplit('https://' + domain)
        if not domain or parsed_domain.hostname != domain or parsed_domain.path or parsed_domain.username or parsed_domain.port:
            raise ValueError('A verified storefront domain is required for the welcome email.')
        base = 'https://' + domain
        sections = [
            ('✉', 'Need help with your order?', 'Contact us by email, through our website contact form, or via chat on our website.'),
            ('◷', 'Friday and weekend enquiries', 'If you email us on Friday or over the weekend, our team will return on Monday during business hours to review and reply. If Monday is a public holiday, please allow until the next business day.'),
            ('▣', 'After your package is dispatched', 'We work with our courier partners to resolve delivery questions. Their investigations can take 48 hours or longer. Thank you for your patience—we’ll follow up and keep you informed.'),
            ('✓', 'Support after your purchase', 'We’re here to help with order queries. Returns, refunds and replacements are available in accordance with our store policies.'),
        ]
        panel = '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:28px">'
        for icon, title, body in sections:
            title, body = t(title), t(body)
            panel += f'<tr><td valign="top" width="30" style="padding:0 8px 24px 0;color:#28594a;font-size:21px" aria-hidden="true">{icon}</td><td style="padding:0 0 24px;font-size:14px;line-height:24px"><strong>{escape(title)}</strong><p style="margin:8px 0 0;color:#637167">{escape(body)}</p></td></tr>'
            detail_lines.append(title + '\n' + body)
        panel += '</table>'
        contact_url = base + '/contactus'
        panel += f'<p style="font-size:14px;line-height:28px;margin:0 0 24px"><a href="mailto:support@{escape(domain, quote=True)}" style="color:#28594a;text-decoration:underline">{escape(t("Email our team"))}</a><br><a href="{escape(contact_url, quote=True)}" style="color:#28594a;text-decoration:underline">{escape(t("Contact form"))}</a><br><a href="{escape(base, quote=True)}" style="color:#28594a;text-decoration:underline">{escape(t("Visit our website for chat"))}</a></p>'
        detail_lines += [t('Email') + ': support@' + domain, t('Contact form') + ': ' + contact_url, t('Website chat') + ': ' + base]
    if kind == "expected_dispatch":
        date = str(context.get("expected_dispatch_date") or t("We’ll keep you updated"))
        panel_label, panel_value, panel_detail = t("ESTIMATED DISPATCH"), date, t("An estimate, not a guaranteed delivery date.")
    elif kind == "delivery_confirmation":
        panel_label = t("CARRIER DELIVERY DETAILS")
        panel_value = t("Delivered") + ": " + str(context.get("delivery_datetime") or t("Not provided by carrier"))
        panel_detail = t("Location") + ": " + str(context.get("delivery_location") or t("Not provided by carrier")) + "\n" + t("Destination postal code") + ": " + str(context.get("delivery_postal_code") or t("Not provided by carrier"))
    elif kind == "shopify_dispatch":
        panel_label = t("DISPATCH UPDATE")
        panel_value = ', '.join(x['number'] for x in context.get('dispatch_parcels', []))
        panel_detail = t("Date and time") + ": " + str(context.get('dispatch_created_at') or '')
    elif kind == "tracking":
        panel_label = t("LATEST CARRIER UPDATE")
        panel_value = t("Status") + ": " + str(context.get("latest_status") or t("Not provided by carrier"))
        panel_detail = t("Date and time") + ": " + str(context.get("last_update_at") or t("Not provided by carrier")) + "\n" + t("Location") + ": " + str(context.get("latest_location") or t("Not provided by carrier"))
    else:
        panel_label = panel_value = panel_detail = ""
    if panel_label:
        panel_detail_html = "<br>".join(escape(line) for line in panel_detail.split("\n"))
        panel = f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#eeeeee" style="margin-top:28px;background:#eeeeee;border:1px solid #dedede;border-radius:0"><tr><td style="padding:22px 24px;font-family:{FONT}">
        <p style="margin:0 0 9px;font-size:10px;line-height:16px;letter-spacing:1.3px;font-weight:600;color:#506c5e">{panel_label}</p>
        <p style="margin:0;font-size:18px;line-height:26px;font-weight:600;color:#214f3d">{escape(panel_value)}</p>
        {f'<p style="margin:7px 0 0;font-size:13px;line-height:21px;color:#596f60">{panel_detail_html}</p>' if panel_detail else ''}
        </td></tr></table>'''
        detail_lines = [panel_value, panel_detail]

    if kind in {"relay_request", "relay_received"}:
        payment = context["relay_payment"]
        currency = escape(str(payment["original_currency"]))
        money_rows = []
        plain_items = []
        for item in payment["items"]:
            name = str(item["name"])
            quantity = str(item["quantity"]).removesuffix(".0")
            amount = str(item["total"])
            money_rows.append(f'<tr><td style="padding:16px 12px 16px 0;border-bottom:1px solid #dedede;font-size:13px;line-height:21px;word-break:break-word">{escape(name)}<br><span style="color:#637167">{escape(t("Quantity"))} {escape(quantity)}</span></td><td width="100" align="right" valign="top" style="padding:16px 0;border-bottom:1px solid #dedede;font-size:13px;line-height:21px;white-space:nowrap">{currency} {escape(amount)}</td></tr>')
            plain_items.append(f'{name} — {t("Quantity")} {quantity} — {payment["original_currency"]} {amount}')
        items_html = f'<table width="100%" cellpadding="0" cellspacing="0" style="margin-top:24px;table-layout:fixed"><tr><th align="left" style="font-size:11px;color:#637167;padding-bottom:8px">{escape(t("ORDER DETAILS"))}</th><th width="100" align="right" style="font-size:11px;color:#637167;padding-bottom:8px">{escape(t("AMOUNT"))}</th></tr>'+''.join(money_rows)+'</table>'
        for label, value in [("Subtotal",payment["subtotal"]),("Tax",payment["tax"]),("Order total",payment["original_total"])]:
            items_html += f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding-top:12px;font-size:13px">{escape(t(label))}</td><td align="right" style="padding-top:12px;font-size:13px;font-weight:600">{currency} {escape(str(value))}</td></tr></table>'
        usd = format(payment["amount_cents"]/100,'.2f')
        panel = f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#eeeeee" style="margin-top:28px;border:1px solid #dedede"><tr><td style="padding:24px"><p style="margin:0 0 8px;font-size:10px;letter-spacing:1.3px;color:#506c5e">{escape(t("USD PAYMENT" if kind == "relay_received" else "AMOUNT TO PAY"))}</p><p style="margin:0;font-size:30px;line-height:38px;font-weight:600;color:#193b2d">USD {usd}</p><p style="margin:8px 0 0;font-size:13px;color:#637167">{escape(t("Due"))} {escape(str(payment["due_date"]))}</p></td></tr></table>'
        detail_lines = [f'{t("Order total")}: {payment["original_currency"]} {payment["original_total"]}',f'{t("Subtotal")}: {payment["subtotal"]} · {t("Tax")}: {payment["tax"]}',f'{t("USD PAYMENT")}: {usd}',f'{t("Due")}: {payment["due_date"]}']

    buttons = []
    plain_actions = []
    if kind in {"relay_request", "relay_received"}:
        if kind == "relay_request":
            label = t('Pay USD') + ' ' + format(context['relay_payment']['amount_cents']/100,'.2f')
            buttons.append(button(label, action_url))
            plain_actions.append(label + ': ' + safe_url(action_url))
        base = safe_url(context.get('website_url'))
        if base:
            contact = base.rstrip('/') + '/contactus'
            buttons.append(button(t('Contact our team'), contact, primary=False))
            plain_actions.append(t('Contact our team') + ': ' + contact)
    elif kind == "trustpilot_review":
        buttons.append(button(t("Share an honest review"), review_url))
        plain_actions.append(f"{t('Share an honest review')}: {safe_url(review_url)}")
    elif kind == 'delivery_issue_received':
        buttons.append(button(t('View your order'), action_url))
        plain_actions.append(f'{t("View your order")}: {safe_url(action_url)}')
    elif kind == "shopify_dispatch":
        for parcel in context.get('dispatch_parcels', []):
            from app.services.shopify_dispatch import carrier_url
            url = carrier_url(parcel['url'])
            label = t('Track all details') + ' — ' + parcel['number']
            buttons.append(button(label, url))
            plain_actions.append(label + ': ' + url)
        tracking_url = safe_url(context.get('tracking_url'))
        buttons.append(button(t('View your order'), tracking_url, primary=False))
        plain_actions.append(t('View your order') + ': ' + tracking_url)
    elif kind == "tracking":
        tracking_url = safe_url(context.get("tracking_url"))
        buttons.append(button(t("Track all details"), tracking_url))
        plain_actions.append(f"{t('Track all details')}: {tracking_url}")
    else:
        for index, action in enumerate(actions):
            if action == 'remove_line':
                continue  # Requires selecting a specific line on the order page.
            if not safe_url(action_url):
                continue
            parts = urlsplit(action_url)
            query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != "choice"] + [("choice", action)]
            url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
            label = t(labels.get(action, action.replace("_", " ").capitalize()))
            destructive = action in {"refund", "cancel_order", "cancel_affected_item"}
            buttons.append(button(label, url, primary=index == 0 and not destructive, destructive=destructive))
            plain_actions.append(f"{label}: {url}")
    if kind == 'delivery_confirmation' and safe_url(context.get('tracking_url')):
        buttons.append(button(t('Track all details'), context['tracking_url'], primary=False))
        plain_actions.append(t('Track all details') + ': ' + context['tracking_url'])
    action_html = f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:30px"><tr><td style="border-top:1px solid #edf0ec;padding-top:26px;font-family:{FONT}">
      <h2 style="margin:0 0 8px;font-size:15px;line-height:23px;font-weight:600;color:#26392f">{action_heading}</h2>
      <p style="margin:0 0 20px;font-size:13px;line-height:22px;color:#6a766e">{note}</p>
      {''.join(buttons)}</td></tr></table>'''
    unsubscribe = safe_url(unsubscribe_url)
    footer = t("This message relates to order {order} placed on {website}.", order=order, website=website)
    html_body = f'''<!doctype html>
<html lang="{t.html_language}" dir="{t.direction}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="x-apple-disable-message-reformatting">
<title>{escape(subject)}</title>
<!--[if !mso]><!--><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&amp;display=swap" rel="stylesheet"><!--<![endif]-->
<style>body,table,td,a{{font-family:{FONT}}}table{{border-collapse:separate}}a{{text-decoration:none}}@media only screen and (max-width:620px){{.outer{{padding:20px 12px!important}}.content{{padding:28px 24px 30px!important}}.heading{{font-size:28px!important;line-height:35px!important}}.brand{{padding:0 8px 22px!important}}}}</style>
<!--[if mso]><style>body,table,td,a,p,h1,h2{{font-family:Arial,sans-serif!important}}</style><![endif]-->
</head><body style="margin:0;padding:0;width:100%;background:#ffffff;color:#26392f;font-family:{FONT};-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%">
<div style="display:none!important;visibility:hidden;opacity:0;color:transparent;height:0;width:0;max-height:0;max-width:0;overflow:hidden;mso-hide:all">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff"><tr><td class="outer" align="center" style="padding:44px 16px">
<!--[if mso]><table role="presentation" width="600" align="center"><tr><td><![endif]-->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;margin:0 auto">
<tr><td class="brand" style="padding:0 4px 28px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td valign="middle">{logo}</td><td align="right" valign="middle" style="font-family:{FONT};font-size:11px;line-height:18px;color:#738076">{escape(t("ORDER"))}<br><strong style="font-size:13px;font-weight:600;color:#34493c">{escape(order)}</strong></td></tr></table></td></tr>
<tr><td bgcolor="#f5f5f5" style="background:#f5f5f5;border:1px solid #e0e0e0;border-radius:0;overflow:hidden">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td class="content" style="padding:40px 40px 36px;font-family:{FONT}">
<p style="margin:0 0 16px;color:#557364;font-size:10px;line-height:16px;font-weight:600;letter-spacing:1.8px">{eyebrow}</p>
<h1 class="heading" style="margin:0 0 18px;font-family:{FONT};font-size:34px;line-height:41px;letter-spacing:-1.2px;font-weight:600;color:#193b2d">{heading}</h1>
<p style="margin:0;font-family:{FONT};font-size:15px;line-height:26px;color:#637167">{intro}</p>
{panel}{items_html}{action_html}
<p style="margin:24px 0 0;font-family:{FONT};font-size:12px;line-height:20px;color:#7a857d">{escape(t("With care,"))}<br><strong style="font-weight:500;color:#42584a">{escape(t("The {website} team", website=website))}</strong></p>
</td></tr></table></td></tr>
<tr><td align="center" style="padding:24px 20px 0;font-family:{FONT};font-size:11px;line-height:19px;color:#7a847c">{escape(footer)}
{f'<p style="margin:12px 0 0"><a href="{escape(unsubscribe, quote=True)}" style="font-family:{FONT};font-size:11px;line-height:19px;color:#68796d;text-decoration:underline">{escape(t("Unsubscribe from movement and review emails"))}</a></p>' if unsubscribe else ''}
</td></tr></table><!--[if mso]></td></tr></table><![endif]-->
</td></tr></table></body></html>'''
    plain = "\n\n".join(filter(None, [subject, intro, "\n".join(detail_lines), "\n".join(plain_items), note, "\n".join(plain_actions), footer, f"{t('Unsubscribe from movement and review emails')}: {unsubscribe}" if unsubscribe else ""]))
    return subject, html_body, plain
