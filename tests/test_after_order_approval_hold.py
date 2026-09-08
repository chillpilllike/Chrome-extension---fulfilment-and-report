import ast
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from fastapi import HTTPException, Request


class ApprovalHoldTests(unittest.TestCase):
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
