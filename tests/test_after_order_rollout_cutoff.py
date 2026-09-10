import ast
import unittest
from datetime import datetime
from pathlib import Path


class RolloutCutoffTests(unittest.TestCase):
    def helpers(self, configured):
        tree = ast.parse(Path("app/main.py").read_text())
        names = {"after_order_cutoff_date", "after_order_case_is_in_scope"}
        module = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[])
        scope = {"datetime": datetime, "Any": object,
                 "clean_text": lambda v: str(v or "").strip(),
                 "get_service_settings": lambda: {"after_order_cutoff_date": configured}}
        exec(compile(module, "cutoff", "exec"), scope)
        return scope

    def test_floor_cannot_be_lowered(self):
        for value in (None, "", "invalid", "2026-08-01", "2026-08-19", "2026-08-20"):
            self.assertEqual("2026-08-20", self.helpers(value)["after_order_cutoff_date"]())

    def test_later_cutoff_allowed(self):
        self.assertEqual("2026-09-10", self.helpers("2026-09-10")["after_order_cutoff_date"]())

    def test_order_date_boundaries(self):
        eligible = self.helpers("2026-08-01")["after_order_case_is_in_scope"]
        for value in (None, "", "invalid", "9999-99-99", "2026-08-19 23:59:59"):
            self.assertFalse(eligible({"odoo_order_date": value}))
        for value in ("2026-08-20 00:00:00", "2026-09-10 12:00:00"):
            self.assertTrue(eligible({"odoo_order_date": value}))
