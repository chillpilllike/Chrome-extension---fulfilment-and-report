"""Shared, table-based customer email layout; no application or network access."""
from html import escape
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl


FONT = "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"


def safe_url(value):
    value = str(value or "").strip()
    return value if urlsplit(value).scheme in {"https", "http"} else ""


def button(label, url, *, primary=True, destructive=False):
    url = safe_url(url)
    if not url:
        return ""
    color = "#ffffff" if primary else "#a33232" if destructive else "#25352f"
    background = "#153e35" if primary else "#ffffff"
    border = "#153e35" if primary else "#e7d9d9" if destructive else "#d9e2de"
    return f'''<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin:0 0 10px">
      <tr><td align="center" bgcolor="{background}" style="border:1px solid {border};border-radius:10px;mso-padding-alt:15px 20px">
      <a href="{escape(url, quote=True)}" style="display:block;padding:15px 20px;border-radius:10px;color:{color};font-family:{FONT};font-size:14px;line-height:20px;font-weight:600;text-align:center;text-decoration:none;mso-padding-alt:0;text-underline-color:{background}">{escape(label)}</a>
      </td></tr></table>'''


def render_after_order_email(case, action_url, *, actions, labels, template_kind="", unsubscribe_url="", review_url=""):
    context = case.get("context") or {}
    order = str(case.get("odoo_order_name") or "Your order")
    website = str(context.get("website_name") or case.get("store_name") or "Customer care")
    kind = template_kind or case.get("case_type") or "tracking"
    if kind == "tracking" and context.get("risk_state") == "suspected_lost":
        kind = "package_lost"
    content = {
        "item_unavailable": ("YOUR ORDER", "Let’s find your next best option.", "An item needs your choice", "Our team has prepared alternatives for an unavailable item in your order. Review the affected item below and choose how you’d like to continue.", "Choose what works for you", "You can change an alternative for 24 hours from your first selection. After that, a higher-priced choice requires payment of the difference; a cheaper choice is reviewed for a difference refund. If you remove an item, any amount charged for it will be refunded after our team reviews and confirms your request."),
        "expected_dispatch": ("DISPATCH UPDATE", "A quick update on your order.", "Your expected dispatch date", "Your order has a later expected dispatch date. Please let us know if you’d like to proceed or cancel.", "How would you like to continue?", "If we don’t hear from you, we’ll continue processing your order."),
        "delivery_confirmation": ("DELIVERY CHECK-IN", "Has your order arrived?", "Did your order arrive?", "The carrier has marked your package as delivered. Please take a moment to tell us whether you received it.", "Please confirm your delivery", "If it hasn’t arrived, let us know so our team can look into it."),
        "package_lost": ("WE’RE HERE TO HELP", "Let’s get this sorted.", "Your package needs attention", "Your package hasn’t had confirmed movement for a while. We’re sorry for the uncertainty. You can request a replacement or a refund below.", "What would you prefer?", "Our team will review your request and help with the next steps."),
        "trustpilot_review": ("THANK YOU", "Your feedback means a lot.", "Thank you for confirming delivery", "We’re glad your order arrived. If you have a moment, we’d love to hear about your experience.", "How was your experience?", "Share an honest review — your feedback helps us improve."),
        "tracking": ("ON ITS WAY", "A little closer to your door.", "Your package has moved", "There’s a new update on your package. You can find the latest details below.", "Follow your delivery", "See the full tracking history and the latest carrier updates."),
    }
    eyebrow, heading, subject_text, intro, action_heading, note = content.get(kind, content["tracking"])
    subject = f"{order} — {subject_text}"
    preheader = f"{subject_text} for {order}. {intro}"
    logo_url = safe_url(context.get("website_logo_url"))
    logo = f'<img src="{escape(logo_url, quote=True)}" alt="{escape(website, quote=True)}" width="144" style="display:block;width:144px;max-width:100%;height:auto;border:0;color:#193b32;font-family:{FONT};font-size:18px;font-weight:600">' if logo_url else f'<span style="font-family:{FONT};font-size:20px;line-height:28px;font-weight:700;letter-spacing:-.6px;color:#193b32">{escape(website)}</span>'

    item_rows = []
    plain_items = []
    for item in case.get("affected_items") or []:
        name = str(item.get("product_name") or "Order item")
        quantity = str(item.get("quantity") or 1)
        if quantity.endswith(".0"):
            quantity = quantity[:-2]
        thumbnail = safe_url(item.get("thumbnail_url"))
        product_url = safe_url(item.get("odoo_product_url"))
        image = f'<img src="{escape(thumbnail, quote=True)}" alt="{escape(name, quote=True)}" width="64" height="64" style="display:block;width:64px;height:64px;object-fit:contain;border:0;border-radius:8px;background:#ffffff;font-size:10px;color:#66756e">' if thumbnail else '<span style="font-size:24px;color:#a4b1aa">&#9633;</span>'
        link = f'<a href="{escape(product_url, quote=True)}" style="font-family:{FONT};font-size:12px;line-height:20px;font-weight:500;color:#28594a;text-decoration:underline">View item</a>' if product_url else ""
        item_rows.append(f'''<tr><td style="padding:0 0 10px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f6f8f6;border-radius:12px">
          <tr><td width="64" valign="middle" align="center" style="width:64px;padding:16px">{image}</td>
          <td valign="middle" style="padding:16px 16px 16px 0;font-family:{FONT};font-size:14px;line-height:21px;color:#24372e">
          <strong style="font-weight:600">{escape(name)}</strong><br><span style="font-size:12px;line-height:24px;color:#64756a">Quantity {escape(quantity)}</span>{'<br>' + link if link else ''}
          </td></tr></table></td></tr>''')
        plain_items.append(f"{name} — Quantity {quantity}")
    items_html = '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:28px">' + "".join(item_rows) + "</table>" if item_rows else ""

    panel = ""
    detail_lines = []
    if kind == "expected_dispatch":
        date = str(context.get("expected_dispatch_date") or "We’ll keep you updated")
        panel_label, panel_value, panel_detail = "ESTIMATED DISPATCH", date, "An estimate, not a guaranteed delivery date."
    elif kind == "tracking":
        panel_label = "LATEST CARRIER UPDATE"
        panel_value = str(context.get("latest_status") or "Shipment update")
        panel_detail = str(context.get("latest_location") or "")
    else:
        panel_label = panel_value = panel_detail = ""
    if panel_label:
        panel = f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:28px;background:#edf4ef;border:1px solid #dfe9e2;border-radius:12px"><tr><td style="padding:22px 24px;font-family:{FONT}">
        <p style="margin:0 0 9px;font-size:10px;line-height:16px;letter-spacing:1.3px;font-weight:600;color:#506c5e">{panel_label}</p>
        <p style="margin:0;font-size:18px;line-height:26px;font-weight:600;color:#214f3d">{escape(panel_value)}</p>
        {f'<p style="margin:7px 0 0;font-size:13px;line-height:21px;color:#596f60">{escape(panel_detail)}</p>' if panel_detail else ''}
        </td></tr></table>'''
        detail_lines = [panel_value, panel_detail]

    buttons = []
    plain_actions = []
    if kind == "trustpilot_review":
        buttons.append(button("Share an honest review", review_url))
        plain_actions.append(f"Share an honest review: {safe_url(review_url)}")
    elif kind == "tracking":
        tracking_url = safe_url(context.get("tracking_url"))
        buttons.append(button("Track all details", tracking_url))
        plain_actions.append(f"Track all details: {tracking_url}")
    else:
        for index, action in enumerate(actions):
            if not safe_url(action_url):
                continue
            parts = urlsplit(action_url)
            query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != "choice"] + [("choice", action)]
            url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
            label = labels.get(action, action.replace("_", " ").capitalize())
            destructive = action in {"refund", "cancel_order", "cancel_affected_item"}
            buttons.append(button(label, url, primary=index == 0 and not destructive, destructive=destructive))
            plain_actions.append(f"{label}: {url}")
    action_html = f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:30px"><tr><td style="border-top:1px solid #edf0ec;padding-top:26px;font-family:{FONT}">
      <h2 style="margin:0 0 8px;font-size:15px;line-height:23px;font-weight:600;color:#26392f">{action_heading}</h2>
      <p style="margin:0 0 20px;font-size:13px;line-height:22px;color:#6a766e">{note}</p>
      {''.join(buttons)}</td></tr></table>'''
    unsubscribe = safe_url(unsubscribe_url)
    footer = f"This message relates to order {order} placed on {website}."
    html_body = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="x-apple-disable-message-reformatting">
<title>{escape(subject)}</title>
<!--[if !mso]><!--><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&amp;display=swap" rel="stylesheet"><!--<![endif]-->
<style>body,table,td,a{{font-family:{FONT}}}table{{border-collapse:separate}}a{{text-decoration:none}}@media only screen and (max-width:620px){{.outer{{padding:20px 12px!important}}.content{{padding:28px 24px 30px!important}}.heading{{font-size:28px!important;line-height:35px!important}}.brand{{padding:0 8px 22px!important}}}}</style>
<!--[if mso]><style>body,table,td,a,p,h1,h2{{font-family:Arial,sans-serif!important}}</style><![endif]-->
</head><body style="margin:0;padding:0;width:100%;background:#f3f5f2;color:#26392f;font-family:{FONT};-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%">
<div style="display:none!important;visibility:hidden;opacity:0;color:transparent;height:0;width:0;max-height:0;max-width:0;overflow:hidden;mso-hide:all">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f3f5f2"><tr><td class="outer" align="center" style="padding:44px 16px">
<!--[if mso]><table role="presentation" width="600" align="center"><tr><td><![endif]-->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;margin:0 auto">
<tr><td class="brand" style="padding:0 4px 28px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td valign="middle">{logo}</td><td align="right" valign="middle" style="font-family:{FONT};font-size:11px;line-height:18px;color:#738076">ORDER<br><strong style="font-size:13px;font-weight:600;color:#34493c">{escape(order)}</strong></td></tr></table></td></tr>
<tr><td bgcolor="#ffffff" style="background:#ffffff;border:1px solid #e1e7df;border-radius:18px;overflow:hidden">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td class="content" style="padding:40px 40px 36px;font-family:{FONT}">
<p style="margin:0 0 16px;color:#557364;font-size:10px;line-height:16px;font-weight:600;letter-spacing:1.8px">{eyebrow}</p>
<h1 class="heading" style="margin:0 0 18px;font-family:{FONT};font-size:34px;line-height:41px;letter-spacing:-1.2px;font-weight:600;color:#193b2d">{heading}</h1>
<p style="margin:0;font-family:{FONT};font-size:15px;line-height:26px;color:#637167">{intro}</p>
{panel}{items_html}{action_html}
<p style="margin:24px 0 0;font-family:{FONT};font-size:12px;line-height:20px;color:#7a857d">With care,<br><strong style="font-weight:500;color:#42584a">The {escape(website)} team</strong></p>
</td></tr></table></td></tr>
<tr><td align="center" style="padding:24px 20px 0;font-family:{FONT};font-size:11px;line-height:19px;color:#7a847c">{escape(footer)}
{f'<p style="margin:12px 0 0"><a href="{escape(unsubscribe, quote=True)}" style="font-family:{FONT};font-size:11px;line-height:19px;color:#68796d;text-decoration:underline">Unsubscribe from movement and review emails</a></p>' if unsubscribe else ''}
</td></tr></table><!--[if mso]></td></tr></table><![endif]-->
</td></tr></table></body></html>'''
    plain = "\n\n".join(filter(None, [subject, intro, "\n".join(detail_lines), "\n".join(plain_items), note, "\n".join(plain_actions), footer, f"Unsubscribe from movement and review emails: {unsubscribe}" if unsubscribe else ""]))
    return subject, html_body, plain
