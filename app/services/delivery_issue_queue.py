"""Read-only delivery issue queue, shared by results and sidebar totals."""

DELIVERY_ISSUE_QUEUE_SQL = """(
  after_order_cases.case_type='delivery_confirmation'
  AND after_order_cases.current_decision='not_received'
  AND after_order_cases.status!='resolved'
  AND NOT EXISTS (
    SELECT 1 FROM after_order_cases newer
    WHERE newer.store_id=after_order_cases.store_id
      AND newer.odoo_order_id=after_order_cases.odoo_order_id
      AND newer.tracking_code=after_order_cases.tracking_code
      AND newer.current_decision IN ('received','not_received')
      AND (COALESCE(newer.decision_updated_at,'')>COALESCE(after_order_cases.decision_updated_at,'')
           OR (COALESCE(newer.decision_updated_at,'')=COALESCE(after_order_cases.decision_updated_at,'')
               AND newer.id>after_order_cases.id))
  )
)"""
