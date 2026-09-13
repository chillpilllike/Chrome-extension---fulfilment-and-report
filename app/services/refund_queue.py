"""Read-only work-queue predicate shared by pagination and store totals."""

REFUND_QUEUE_SQL = """(
  (after_order_cases.status != 'resolved'
   AND after_order_cases.current_decision IN ('refund','exclude_item_and_proceed','cancel_affected_item')
   AND NOT EXISTS (SELECT 1 FROM after_order_refund_requests r
                   WHERE r.case_id=after_order_cases.id AND r.status='completed')
   AND NOT EXISTS (SELECT 1 FROM after_order_execution_jobs j
                   WHERE j.case_id=after_order_cases.id
                     AND j.decision_version=after_order_cases.decision_version AND j.status='completed'))
  OR EXISTS (SELECT 1 FROM after_order_refund_requests r
             WHERE r.case_id=after_order_cases.id
               AND r.status NOT IN ('completed','cancelled','withdrawn'))
  OR EXISTS (SELECT 1 FROM after_order_line_removals r
             WHERE r.case_id=after_order_cases.id AND r.test_mode=0
               AND r.status IN ('needs_approval','auto_remove_pending','finance_review','removed_refund_pending'))
  OR EXISTS (SELECT 1 FROM after_order_line_selections s
             WHERE s.case_id=after_order_cases.id AND s.test_mode=0
               AND s.status IN ('choosing','needs_approval','needs_review','processing','waiting_refund')
               AND COALESCE(s.refund_status,'') NOT IN ('completed','refunded','verified','not_required')
               AND CASE WHEN jsonb_typeof(CAST(s.product_json AS JSONB)->'difference')='number'
                        THEN CAST(CAST(s.product_json AS JSONB)->>'difference' AS NUMERIC)<0
                        ELSE s.status='waiting_refund' END)
)"""
