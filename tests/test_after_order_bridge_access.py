"""Exercise access policy without importing main's production startup hooks."""
import ast
import re
import unittest
from pathlib import Path
from types import SimpleNamespace


class BridgeAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse((Path(__file__).parents[1] / "app/main.py").read_text())
        names = {
            "UNAUTHENTICATED_PUBLIC_API_PREFIXES", "PUBLIC_FRONTEND_PATHS",
            "PUBLIC_API_PATHS", "PUBLIC_POST_API_PATHS",
            "PUBLIC_DISPATCH_POST_PREFIXES", "PUBLIC_DISPATCH_GET_PREFIXES",
        }
        nodes = [node for node in tree.body if (
            isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in names for t in node.targets
            )
        ) or (isinstance(node, ast.FunctionDef) and node.name == "request_requires_public_access")]
        scope = {"re": re, "Request": object}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "access_policy", "exec"), scope)
        cls.policy = staticmethod(scope["request_requires_public_access"])

    def requires_access(self, path, method="GET"):
        return self.policy(SimpleNamespace(url=SimpleNamespace(path=path), method=method))

    def test_bridge_get_and_post_use_handler_authentication(self):
        for method in ("GET", "POST"):
            self.assertFalse(self.requires_access("/api/public/after-order-bridge/action/test-token", method))
        self.assertFalse(self.requires_access("/api/public/after-order-bridge/orders/123"))
        self.assertTrue(self.requires_access("/api/public/after-order-bridge/orders/123", "POST"))

    def test_exemption_does_not_cover_other_routes_or_methods(self):
        for path in (
            "/api/public/after-order-bridge/action/",
            "/api/public/after-order-bridge/action/token/extra",
            "/api/public/after-order-bridge/settings",
            "/api/public/unrelated",
        ):
            self.assertTrue(self.requires_access(path))
        self.assertTrue(self.requires_access("/api/public/after-order-bridge/action/token", "DELETE"))


if __name__ == "__main__":
    unittest.main()
