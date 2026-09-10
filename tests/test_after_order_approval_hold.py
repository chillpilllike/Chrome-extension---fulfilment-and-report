import ast
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from fastapi import HTTPException, Request


class ApprovalHoldTests(unittest.TestCase):
    def load_function(self, name, scope):
        tree = ast.parse(Path('app/main.py').read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        fn.decorator_list = []
        scope.update({'HTTPException': HTTPException, 'Request': Request, 'Any': Any})
        exec(compile(ast.Module(body=[fn], type_ignores=[]), '<guard>', 'exec'), scope)
        return scope[name]

    def test_approval_only_activation_requires_explicit_confirmation(self):
        setter = Mock()
        fn = self.load_function('api_after_order_approval_only_live', {'set_service_settings': setter, 'api_after_order_settings': lambda: {}})
        for payload in ({}, {'confirm_real_recipients': 'true'}, {'confirm_real_recipients': False}):
            with self.assertRaises(HTTPException):
                fn(payload)
        setter.assert_not_called()
        fn({'confirm_real_recipients': True})
        settings = setter.call_args.args[0]
        self.assertEqual('false', settings['after_order_automation_enabled'])
        self.assertEqual('false', settings['after_order_email_test_mode'])
        self.assertEqual('true', settings['after_order_approval_only_live'])
        self.assertNotIn('after_order_live_readiness_approved', settings)

    def test_approval_only_worker_exits_before_any_work(self):
        fn = self.load_function('run_after_order_automation', {'after_order_approval_only_live': lambda: True})
        self.assertFalse(fn()['ok'])

    def test_live_preparation_requires_guarded_mode(self):
        for test, guard in ((True, True), (False, False)):
            fn = self.load_function('api_after_order_prepare_live_email', {
                'after_order_email_test_mode': lambda: test,
                'after_order_approval_only_live': lambda: guard})
            with self.assertRaises(HTTPException) as exc:
                fn(1, None)
            self.assertEqual(409, exc.exception.status_code)

    def test_live_preparation_blocks_answered_and_no_scan_cases(self):
        for case in ({'current_decision': 'refund'}, {'case_type': 'tracking', 'context': {'risk_state': 'awaiting_first_scan'}}):
            send = Mock()
            fn = self.load_function('api_after_order_prepare_live_email', {
                'after_order_email_test_mode': lambda: False,
                'after_order_approval_only_live': lambda: True,
                'after_order_case_by_id': lambda _: case,
                'require_after_order_case_in_scope': Mock(),
                'send_after_order_email': send})
            with self.assertRaises(HTTPException):
                fn(1, None)
            send.assert_not_called()

    def test_workers_and_legacy_retry_cannot_send(self):
        tree = ast.parse(Path('app/main.py').read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'retry_after_order_email')
        fn.decorator_list = []
        scope = {'HTTPException': HTTPException, 'Request': Request, 'Any': Any, 'db': Mock()}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), '<retry>', 'exec'), scope)
        for kw in ({}, {'automatic': True}, {'automatic': True, 'approval_digest': 'anything'}):
            with self.assertRaises(HTTPException) as exc:
                scope['retry_after_order_email'](1, None, **kw)
            self.assertEqual(409, exc.exception.status_code)
        scope['db'].assert_not_called()

    def test_prepared_message_returns_before_provider_call(self):
        tree = ast.parse(Path('app/main.py').read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'send_after_order_email')
        hold = next(n for n in ast.walk(fn) if isinstance(n, ast.Return) and 'awaiting_approval' in ast.unparse(n))
        send = next(n for n in ast.walk(fn) if isinstance(n, ast.Call) and ast.unparse(n.func) == 'provider.send')
        self.assertLess(hold.lineno, send.lineno)

    def test_financial_workers_are_held_without_odoo_calls(self):
        tree = ast.parse(Path('app/services/alternative_workflow.py').read_text())
        for name in ('process', 'release'):
            fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
            fn.decorator_list = []
            scope = {}
            exec(compile(ast.Module(body=[fn], type_ignores=[]), '<finance>', 'exec'), scope)
            runtime = Mock()
            runtime.r.after_order_email_test_mode.return_value = False
            result = scope[name](runtime, 1, 2) if name == 'process' else scope[name](runtime, 1)
            self.assertEqual('needs_approval', result['status'])
            runtime.r.OdooClient.assert_not_called()
