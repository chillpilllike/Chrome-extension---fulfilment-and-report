"""Staff attestations of refunds already made elsewhere. Never calls a payment API."""

import json
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from app.services.alternative_workflow import Runtime
from app.services.after_order_email import render_after_order_email

KIND = "manual_refund_completed"
SCHEMA = """CREATE TABLE IF NOT EXISTS after_order_manual_refunds (
 id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL REFERENCES after_order_cases(id),
 store_id INTEGER NOT NULL, order_id INTEGER NOT NULL, scope TEXT NOT NULL,
 amount TEXT NOT NULL,currency TEXT NOT NULL,reference TEXT NOT NULL,completed_at TEXT NOT NULL,
 recorded_by TEXT NOT NULL,notes TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(case_id,scope), UNIQUE(store_id,reference)
);"""


def amount(value):
    try:
        result = Decimal(str(value))
        if (
            not result.is_finite()
            or result <= 0
            or result != result.quantize(Decimal(".01"))
        ):
            raise ValueError("Enter a positive amount with at most two decimal places.")
        return result
    except (InvalidOperation, TypeError):
        raise ValueError("Enter a valid refund amount.")


class Completion(BaseModel):
    scope: str = Field(min_length=1, max_length=100)
    decision_version: int
    amount: str
    currency: str = Field(min_length=3, max_length=3)
    reference: str = Field(min_length=3, max_length=150)
    completed_at: str
    recorded_by: str = Field(min_length=2, max_length=100)
    notes: str = Field(default="", max_length=2000)
    refunded_outside_app: bool
    other_refunds_checked: bool


class ManualRefunds:
    def __init__(self, namespace):
        self.r = Runtime(namespace)

    def enabled(self):
        return self.r.get_service_settings().get("after_order_refund_mode") == "manual"

    def scopes(self, conn, case):
        options = []
        if case.get("status") == "resolved":
            return options
        fingerprint = self.r.request_fingerprint(
            {
                **case,
                "context": case.get("context")
                or json.loads(case.get("context_json") or "{}"),
                "affected_items": case.get("affected_items")
                or json.loads(case.get("affected_items_json") or "[]"),
            }
        )
        if case.get("status") != "resolved" and case.get("current_decision") in {
            "refund",
            "cancel_order",
            "exclude_item_and_proceed",
            "cancel_affected_item",
        }:
            prior = conn.execute(
                "SELECT amount,currency FROM after_order_refund_requests WHERE case_id=?",
                (case["id"],),
            ).fetchone()
            options.append(
                {
                    "key": "order",
                    "label": "Customer refund request",
                    "amount": (
                        str(prior["amount"]) if prior and prior["amount"] > 0 else ""
                    ),
                    "currency": prior["currency"] if prior else "",
                }
            )
        for row in conn.execute(
            "SELECT * FROM after_order_line_selections WHERE case_id=? AND test_mode=0 AND status NOT IN ('withdrawn','completed')",
            (case["id"],),
        ).fetchall():
            if (
                case.get("current_decision") != "offer_alternatives"
                or row["issue_fingerprint"] != fingerprint
            ):
                continue
            product = json.loads(row["product_json"])
            try:
                difference = Decimal(str(product.get("difference")))
                ready = datetime.fromisoformat(
                    row["deadline_at"].replace("Z", "+00:00")
                ) <= datetime.now(timezone.utc)
                if (
                    difference.is_finite()
                    and difference < 0
                    and ready
                    and row["refund_status"]
                    not in {"completed", "refunded", "verified"}
                ):
                    options.append(
                        {
                            "key": f'replacement:{row["line_id"]}:{row["version"]}',
                            "label": f'Replacement price difference · line {row["line_id"]}',
                            "amount": str(-difference),
                            "currency": product["currency"],
                        }
                    )
            except (ValueError, InvalidOperation, TypeError):
                continue
        for row in conn.execute(
            "SELECT * FROM after_order_line_removals WHERE case_id=? AND test_mode=0 AND status IN ('finance_review','removed_refund_pending')",
            (case["id"],),
        ).fetchall():
            if (
                case.get("current_decision") != "offer_alternatives"
                or row["issue_fingerprint"] != fingerprint
            ):
                continue
            options.append(
                {
                    "key": f'removal:{row["line_id"]}:{row["version"]}',
                    "label": f'Removed item refund · line {row["line_id"]}',
                    "amount": "",
                    "currency": "",
                }
            )
        completed = {
            x["scope"]
            for x in conn.execute(
                "SELECT scope FROM after_order_manual_refunds WHERE case_id=?",
                (case["id"],),
            ).fetchall()
        }
        return [x for x in options if x["key"] not in completed]

    def validate_message(self, message):
        r = self.r
        payload = json.loads(message.get("payload_json") or "{}")
        with r.db() as conn:
            record = conn.execute(
                "SELECT * FROM after_order_manual_refunds WHERE id=? AND case_id=?",
                (int(payload.get("_care_manual_refund_id") or 0), message["case_id"]),
            ).fetchone()
        if (
            not record
            or message.get("idempotency_key") != f'manual-refund:{record["id"]}'
        ):
            raise ValueError(
                "Refund confirmation requires an immutable manual completion record."
            )
        if (
            payload.get("_care_manual_refund_amount") != record["amount"]
            or payload.get("_care_manual_refund_currency") != record["currency"]
        ):
            raise ValueError("Refund email no longer matches its completion record.")

    def complete(self, case_id, body, request):
        r = self.r
        if not self.enabled() or r.after_order_email_test_mode():
            raise HTTPException(
                409,
                "Manual completion requires manual-refund live mode. No refund or email was recorded.",
            )
        if not body.refunded_outside_app or not body.other_refunds_checked:
            raise HTTPException(
                400,
                "Confirm the refund was completed outside this app and prior refunds were checked.",
            )
        value = amount(body.amount)
        if len(body.reference.strip()) < 3 or len(body.recorded_by.strip()) < 2:
            raise HTTPException(
                400, "Provide a refund reference and staff name, not whitespace."
            )
        date = datetime.fromisoformat(body.completed_at.replace("Z", "+00:00"))
        if date.tzinfo is None or date > datetime.now(timezone.utc):
            raise HTTPException(
                400,
                "Provide the actual refund date and time, with timezone, not a future date.",
            )
        case = r.after_order_case_by_id(case_id)
        if not case:
            raise HTTPException(404, "Case not found.")
        r.require_after_order_case_in_scope(case)
        case = r.hydrate_after_order_recipient_and_domain(
            case, strict=True, allow_cancelled=True
        )
        order = r.OdooClient(r.get_store(case["store_id"])).read(
            "sale.order",
            [case["odoo_order_id"]],
            ["currency_id", "amount_total", "website_id"],
        )[0]
        if (
            r.many2one_id(order["website_id"]) != case["website_id"]
            or order["currency_id"][1] != body.currency.upper()
        ):
            raise HTTPException(
                409,
                "Order website or currency differs. Reconcile before recording the refund.",
            )
        # Manual attestation is not payment-provider verification. Never move money.
        if value > Decimal(str(order["amount_total"])):
            raise HTTPException(
                409,
                "Refund exceeds the current order total. Reconcile the paid amount in Odoo first.",
            )
        now = r.utc_now()
        with r.db() as conn:
            fresh = dict(
                conn.execute(
                    "SELECT * FROM after_order_cases WHERE id=? FOR UPDATE", (case_id,)
                ).fetchone()
            )
            existing = conn.execute(
                "SELECT id FROM after_order_manual_refunds WHERE case_id=? AND scope=?",
                (case_id, body.scope),
            ).fetchone()
            if existing:
                return {
                    "ok": True,
                    "recorded": True,
                    "message": "Already recorded. Check Email log; no duplicate refund email was sent.",
                }
            if fresh["decision_version"] != body.decision_version:
                raise HTTPException(
                    409,
                    "Customer decision changed. Refresh before recording completion.",
                )
            option = next(
                (x for x in self.scopes(conn, fresh) if x["key"] == body.scope), None
            )
            if not option:
                raise HTTPException(
                    409, "This refund is no longer awaiting manual completion."
                )
            if option["amount"] and value != Decimal(option["amount"]):
                raise HTTPException(
                    409,
                    "Amount differs from the current requested refund. Reconcile before completing.",
                )
            if option["currency"] and option["currency"] != body.currency.upper():
                raise HTTPException(
                    409, "Refund currency differs from the requested price difference."
                )
            if conn.execute(
                "SELECT 1 FROM after_order_manual_refunds WHERE store_id=? AND reference=?",
                (case["store_id"], body.reference.strip()),
            ).fetchone():
                raise HTTPException(
                    409, "This refund reference has already been recorded."
                )
            # Serialize all manual refunds for the same order, including different cases.
            conn.execute(
                "SELECT pg_advisory_xact_lock(?,?)",
                (int(case["store_id"]), int(case["odoo_order_id"])),
            )
            prior = conn.execute(
                "SELECT COALESCE(SUM(CAST(amount AS NUMERIC)),0) AS amount FROM after_order_manual_refunds WHERE store_id=? AND order_id=? AND currency=?",
                (case["store_id"], case["odoo_order_id"], body.currency.upper()),
            ).fetchone()["amount"]
            if value + Decimal(str(prior)) > Decimal(str(order["amount_total"])):
                raise HTTPException(
                    409, "Recorded manual refunds would exceed the current order total."
                )
            ident = conn.execute(
                """INSERT INTO after_order_manual_refunds
                (case_id,store_id,order_id,scope,amount,currency,reference,completed_at,recorded_by,notes,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?) RETURNING id""",
                (
                    case_id,
                    case["store_id"],
                    case["odoo_order_id"],
                    body.scope,
                    str(value),
                    body.currency.upper(),
                    body.reference.strip(),
                    date.isoformat(),
                    body.recorded_by.strip(),
                    body.notes,
                    now,
                ),
            ).fetchone()["id"]
            details = {
                "amount": str(value),
                "currency": body.currency.upper(),
                "reference": body.reference.strip(),
                "completed_at": date.isoformat(),
                "manual_refund_id": ident,
                "scope": body.scope,
            }
            email_case = {
                **case,
                "context": {**case.get("context", {}), "refund": details},
            }
            subject, html, plain = render_after_order_email(
                email_case, "", actions=[], labels={}, template_kind=KIND
            )
            sender, domain = r.after_order_sender(case)
            payload = {
                "from": sender,
                "to": [case["customer_email"]],
                "reply_to": f"support@{domain}",
                "subject": subject,
                "html": html,
                "text": plain,
                "_care_manual_refund_id": ident,
                "_care_manual_refund_amount": str(value),
                "_care_manual_refund_currency": body.currency.upper(),
            }
            msg = conn.execute(
                """INSERT INTO after_order_messages
                (case_id,provider,recipient,sender,subject,html_preview,status,idempotency_key,payload_json,created_at,updated_at,test_mode,request_fingerprint,template_kind,related_items_json,attempt_count)
                VALUES(?,'resend',?,?,?,?,'awaiting_approval',?,?,?,?,0,?,?,?,0) RETURNING id""",
                (
                    case_id,
                    case["customer_email"],
                    sender,
                    subject,
                    html,
                    f"manual-refund:{ident}",
                    json.dumps(payload),
                    now,
                    now,
                    r.request_fingerprint(case),
                    KIND,
                    json.dumps(case["affected_items"]),
                ),
            ).fetchone()["id"]
            if body.scope == "order":
                status = (
                    "needs_attention"
                    if fresh["current_decision"]
                    in {"exclude_item_and_proceed", "cancel_affected_item"}
                    else "resolved"
                )
                conn.execute(
                    "UPDATE after_order_cases SET status=?,confirmed_at=COALESCE(confirmed_at,?),confirmed_by=?,updated_at=? WHERE id=?",
                    (status, now, body.recorded_by, now, case_id),
                )
                conn.execute(
                    "UPDATE after_order_refund_requests SET status='completed',last_error=NULL,updated_at=? WHERE case_id=?",
                    (now, case_id),
                )
                conn.execute(
                    "UPDATE after_order_action_links SET invalidated_at=?,updated_at=? WHERE case_id=? AND invalidated_at IS NULL",
                    (now, now, case_id),
                )
                conn.execute(
                    "UPDATE after_order_execution_jobs SET status='cancelled',updated_at=? WHERE case_id=? AND status='pending'",
                    (now, case_id),
                )
            else:
                kind, line, version = body.scope.split(":")
                if kind == "replacement":
                    conn.execute(
                        "UPDATE after_order_line_selections SET refund_status='completed',status='needs_review',last_error='Refund completed manually; replacement fulfilment still requires team review.',updated_at=? WHERE case_id=? AND line_id=? AND version=? AND test_mode=0",
                        (now, case_id, int(line), int(version)),
                    )
                else:
                    conn.execute(
                        "UPDATE after_order_line_removals SET status='refunded_manual_review',updated_at=? WHERE case_id=? AND line_id=? AND version=? AND test_mode=0",
                        (now, case_id, int(line), int(version)),
                    )
                conn.execute(
                    "UPDATE after_order_cases SET status='needs_attention',updated_at=? WHERE id=?",
                    (now, case_id),
                )
            r.record_after_order_event(
                conn,
                case_id,
                "manual_refund_completed",
                actor_type="team",
                actor_label=body.recorded_by,
                details={**details, "email_id": msg, "notes": body.notes},
            )
        try:
            r.enqueue_odoo_chatter_note(
                case["store_id"],
                case["odoo_order_id"],
                "manual_refund_completed",
                f"manual-refund-{ident}",
                r.html.escape(
                    f"Team recorded an externally completed refund: {value} {body.currency.upper()}; reference {body.reference}; completed {date.isoformat()}; recorded by {body.recorded_by}. No payment was executed by the after-order app."
                ),
            )
        except Exception as exc:
            with r.db() as conn:
                r.record_after_order_event(
                    conn,
                    case_id,
                    "manual_refund_chatter_failed",
                    details={"error": r.clean_error_message(exc)},
                )
        try:
            sent = r.retry_after_order_email(msg, request, policy_exception=True)
            return {
                "ok": True,
                "recorded": True,
                "email_status": sent.get("status"),
                "message_id": msg,
                "message": "Manual refund recorded. "
                + sent.get("message", "Check Email log for delivery status."),
            }
        except Exception as exc:
            with r.db() as conn:
                r.record_after_order_event(
                    conn,
                    case_id,
                    "manual_refund_email_needs_attention",
                    details={"email_id": msg, "error": r.clean_error_message(exc)},
                )
            return {
                "ok": True,
                "recorded": True,
                "email_status": "needs_attention",
                "message_id": msg,
                "message": "Refund completion is saved. Email needs attention in Email log. Do not refund the customer again.",
            }

    def router(self):
        router = APIRouter(prefix="/api/after-order/cases")

        @router.get("/{case_id}/manual-refund")
        def view(case_id: int):
            case = self.r.after_order_case_by_id(case_id)
            if not case:
                raise HTTPException(404, "Case not found.")
            self.r.require_after_order_case_in_scope(case)
            with self.r.db() as conn:
                return {
                    "enabled": self.enabled(),
                    "test_mode": self.r.after_order_email_test_mode(),
                    "decision_version": case["decision_version"],
                    "options": self.scopes(conn, case),
                    "completed": [
                        dict(x)
                        for x in conn.execute(
                            "SELECT * FROM after_order_manual_refunds WHERE case_id=? ORDER BY id",
                            (case_id,),
                        ).fetchall()
                    ],
                }

        @router.post("/{case_id}/manual-refund")
        def complete(case_id: int, body: Completion, request: Request):
            try:
                return self.complete(case_id, body, request)
            except (ValueError, InvalidOperation) as exc:
                raise HTTPException(400, str(exc)) from exc

        return router

    def launch_router(self):
        router = APIRouter(prefix="/api/after-order/settings")

        @router.post("/manual-live-start")
        def launch(payload: dict):
            # Hold both rollout and refund-email worker locks until cancellation
            # is committed AND live settings are persisted. A second click cannot
            # clear new messages, and an old worker snapshot cannot escape reset.
            with self.r.db() as guard:
                guard.execute("SELECT pg_advisory_xact_lock(781905438)")
                if not guard.execute(
                    "SELECT pg_try_advisory_xact_lock(781905437) AS locked"
                ).fetchone()["locked"]:
                    raise HTTPException(
                        409,
                        "A refund notification worker is active. Wait for it to finish and retry.",
                    )
                saved = guard.execute(
                    "SELECT value FROM app_settings WHERE key='after_order_manual_live_started_at'"
                ).fetchone()
                if saved and json.loads(saved["value"] or '""'):
                    return {
                        "ok": True,
                        "already_started": True,
                        "settings": self.r.api_after_order_settings(),
                    }
                return self.start_live(payload)

        return router

    def start_live(self, payload):
        r = self.r
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
        if (
            payload.get("cutoff_date") != today
            or payload.get("confirm_cancel_queued") is not True
            or payload.get("confirm_live") is not True
        ):
            raise HTTPException(
                400,
                "Confirm today’s cutoff, cancellation of earlier queued messages, and real-customer mode.",
            )
        settings = r.get_service_settings()
        if settings.get("after_order_manual_live_started_at"):
            return {
                "ok": True,
                "already_started": True,
                "settings": r.api_after_order_settings(),
            }
        if not r.after_order_email_test_mode():
            raise HTTPException(
                409, "Enable test mode before clearing the old queue safely."
            )
        now = r.utc_now()
        r.set_service_settings(
            {
                "after_order_refund_mode": "manual",
                "after_order_approval_only_live": "true",
                "after_order_automation_enabled": "false",
                "after_order_cutoff_date": today,
                "after_order_manual_live_cutoff": today,
            }
        )
        with r.db() as conn:
            if conn.execute(
                "SELECT 1 FROM after_order_messages WHERE status IN ('sending','retrying') LIMIT 1"
            ).fetchone():
                raise HTTPException(
                    409,
                    "An email attempt is in flight. Wait for it to finish and retry. Test mode remains on.",
                )
            rows = conn.execute(
                "SELECT id,case_id,status,payload_json FROM after_order_messages WHERE status IN ('awaiting_approval','failed','queued','pending','delivery_unknown') FOR UPDATE"
            ).fetchall()
            counts = {
                "cancelled_emails": 0,
                "uncertain_emails_held": 0,
                "cancelled_sms": 0,
                "cancelled_refund_email_jobs": 0,
            }
            for row in rows:
                saved = json.loads(row["payload_json"] or "{}")
                saved["_care_rollout_cancelled_at"] = now
                status = (
                    "delivery_unknown"
                    if row["status"] == "delivery_unknown"
                    else "cancelled"
                )
                conn.execute(
                    "UPDATE after_order_messages SET status=?,payload_json=?,updated_at=? WHERE id=?",
                    (status, json.dumps(saved), now, row["id"]),
                )
                counts[
                    (
                        "uncertain_emails_held"
                        if status == "delivery_unknown"
                        else "cancelled_emails"
                    )
                ] += 1
                r.record_after_order_event(
                    conn,
                    row["case_id"],
                    "email_cancelled_for_manual_live_rollout",
                    actor_type="team",
                    details={
                        "message_id": row["id"],
                        "cutoff_date": today,
                        "prior_status": row["status"],
                    },
                )
            counts["cancelled_sms"] = conn.execute(
                "UPDATE after_order_sms SET status='cancelled',last_error='Earlier queue cancelled at manual-live rollout',updated_at=? WHERE status IN ('awaiting_approval','failed')",
                (now,),
            ).rowcount
            counts["cancelled_refund_email_jobs"] = conn.execute(
                "UPDATE airwallex_refund_emails SET state='cancelled',last_error='Earlier queued notification cancelled at manual-live rollout' WHERE state IN ('queued','retry','held','failed')"
            ).rowcount
            conn.execute(
                "UPDATE after_order_action_links SET invalidated_at=?,updated_at=? WHERE invalidated_at IS NULL AND case_id IN (SELECT c.id FROM after_order_cases c WHERE COALESCE((SELECT MIN(NULLIF(l.odoo_order_date,'')) FROM order_lines l WHERE l.store_id=c.store_id AND l.odoo_order_id=c.odoo_order_id),'')<?)",
                (now, now, today),
            )
        r.set_service_settings(
            {
                "after_order_manual_live_started_at": now,
                "after_order_email_test_mode": "false",
            }
        )
        return {
            "ok": True,
            **counts,
            "cutoff_date": today,
            "settings": r.api_after_order_settings(),
        }
