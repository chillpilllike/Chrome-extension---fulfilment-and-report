"""Read-only operational queues. Legacy 'lost' flags are not carrier evidence."""
from datetime import datetime, timezone
import re


QUEUES = {
    "unscanned": ("Not checked yet", "Tracking team", "Run a carrier check before deciding whether the shipment has moved."),
    "lookup_error": ("Tracking not found", "Fulfilment team", "Verify the code and carrier, check handover proof, then recheck tracking."),
    "awaiting_first_scan": ("Awaiting carrier scan", "Fulfilment team", "Check dispatch and carrier handover. Electronic records do not prove carrier possession."),
    "stalled": ("Movement stalled", "Carrier support", "Open a carrier investigation and review the order in After-order care."),
    "carrier_exception": ("Delivery exception", "Customer support", "Review the carrier message for customs, address, delivery attempt or return action."),
    "confirmed_lost": ("Carrier-reported loss", "Customer support", "Verify the carrier evidence and review refund or replacement approval in After-order care."),
    "in_transit": ("In transit", "Tracking team", "Monitor the next carrier event. No loss action is indicated."),
    "delivered": ("Delivered", "Customer support", "No delivery action unless the customer disputes receipt."),
    "needs_review": ("Status needs review", "Tracking team", "Open the carrier record. This message does not establish physical movement or delivery."),
}


def parse_stamp(value):
    text = str(value or "").strip()
    for fmt in (None, "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p"):
        try:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00")) if fmt is None else datetime.strptime(text, fmt)
            return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass
    return None


def annotate_workflow(row, stale_days=10, now=None):
    now = now or datetime.now(timezone.utc)
    threshold = max(1, min(90, int(stale_days or 10)))
    status = str(row.get("status") or "").strip().lower()
    stamp = parse_stamp(row.get("last_update_at"))
    age = max(0, (now - stamp).days) if stamp and stamp <= now else None
    imported = parse_stamp(row.get("created_at"))
    import_age = max(0, (now - imported).days) if imported and imported <= now else None
    exception = re.search(r"\b(not delivered|undeliverable|delivery failed|failed delivery|attempted delivery|delivery attempted|return(?:ed)? to sender|return to warehouse|damaged|confiscated|invalid kyc|customs hold|held|delayed by customs|unsafe to leave)\b", status)
    loss = re.search(r"\b(?:parcel|package|shipment|item) (?:is |was |has been |reported |declared )?lost\b|^lost$|\blost in transit\b", status)
    delivered = re.search(r"\b(delivered|recipient collected|collected by recipient)\b", status) and not re.search(r"\b(not|pending|expected|will be|to be|out for)\b", status)
    electronic = any(term in status for term in ("data received", "electronic information", "manifest", "label created", "pre-advised", "pre advised", "shipment information", "shipment announced", "shipment in transit to the epost global processing center"))
    physical = any(term in status for term in ("in transit", "in-transit", "arrived", "arrival", "departed", "departure", "received", "processing", "processed", "out for delivery", "picked up", "accepted", "customs clearance", "into customs", "handing over")) and not re.search(r"\b(expected|awaiting|pending|not yet)\b", status)
    if "error locating tracking number" in status or "tracking number not found" in status:
        queue = "lookup_error"
    elif exception:
        queue = "carrier_exception"
    elif loss:
        queue = "confirmed_lost"
    elif delivered:
        queue = "delivered"
    elif electronic:
        queue = "awaiting_first_scan"
    elif not status:
        queue = "awaiting_first_scan" if row.get("last_checked_at") else "unscanned"
    elif physical:
        queue = "stalled" if age is not None and age >= threshold else "in_transit"
    else:
        queue = "needs_review"
    label, owner, action = QUEUES[queue]
    row.update(workflow_queue=queue, workflow_label=label, suggested_owner=owner,
               next_action=action, days_since_update=age, days_since_import=import_age,
               suspected_lost=queue == "stalled", stale_days_threshold=threshold)
    return row


def persisted_status(status, last_update_at):
    queue = annotate_workflow({"status": status, "last_update_at": last_update_at})["workflow_queue"]
    return {"confirmed_lost": "lost", "stalled": "suspected_lost", "unscanned": "awaiting_first_scan", "in_transit": "pending", "needs_review": "pending"}.get(queue, queue)


def matches_queue(row, queue):
    if queue == "archived":
        return bool(row.get("archived_at"))
    if row.get("archived_at"):
        return False
    actual = row.get("workflow_queue")
    if queue in ("all", ""):
        return True
    if queue in ("active", "not_delivered"):
        return actual != "delivered"
    if queue == "attention":
        return actual not in ("in_transit", "delivered")
    if queue in ("suspected_lost", "stale"):
        return actual == "stalled"
    if queue == "lost":
        return actual == "confirmed_lost"
    if queue == "pending":
        return actual in ("unscanned", "awaiting_first_scan", "in_transit", "needs_review")
    if queue in ("refund_claimed", "refund_received"):
        return row.get("refund_status") == queue.removeprefix("refund_")
    return actual == queue
