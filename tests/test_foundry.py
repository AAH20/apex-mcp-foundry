import io
import json
import tempfile
import unittest
from pathlib import Path

from apex_mcp_foundry.catalog import RepositoryCatalog
from apex_mcp_foundry.cli import _init_manifest
from apex_mcp_foundry.repository import RepositoryAnalyzer
from apex_mcp_foundry.server import MCPStdioServer


class FoundryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "sample-repo"
        self.root.mkdir()
        (self.root / "sample.py").write_text(
            '"""A sample repository."""\n\n'
            'def calculate_total(items: list[int]) -> int:\n'
            '    """Return the sum of item values."""\n'
            '    return sum(items)\n', encoding="utf-8")
        (self.root / ".env").write_text("SECRET=never-index\n", encoding="utf-8")
        (self.root / "notes.txt").write_text("not a source file\n", encoding="utf-8")
        self.manifest = _init_manifest(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def approve(self, handlers=("repo.overview", "repo.search_symbols", "repo.get_symbol")):
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        data["status"] = "approved"
        data["approved_handlers"] = list(handlers)
        self.manifest.write_text(json.dumps(data), encoding="utf-8")

    def test_static_analysis_indexes_declarations_without_executing_code(self):
        marker = self.root / "executed.marker"
        (self.root / "danger.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8")
        result = RepositoryAnalyzer(self.root).analyze()
        self.assertFalse(marker.exists())
        self.assertFalse(any(item["path"] == ".env" for item in result["symbols"]))
        self.assertEqual(result["symbols"][0]["qualified_name"], "calculate_total")
        self.assertEqual(result["symbols"][0]["parameters"], ["items"])
        self.assertFalse(result["execution_performed"])

    def test_no_manifest_or_draft_manifest_exposes_no_capabilities(self):
        catalog = RepositoryCatalog([self.root])
        self.assertEqual(catalog.capabilities(), [])
        with self.assertRaises(PermissionError):
            catalog.call("sample-repo:repo.overview", {})

    def test_approved_capability_is_typed_and_read_only(self):
        self.approve(("repo.overview", "repo.search_symbols"))
        catalog = RepositoryCatalog([self.root])
        matches = catalog.search("repo.overview")
        self.assertEqual(matches["total_matches"], 1)
        overview = catalog.call("sample-repo:repo.overview", {})
        self.assertEqual(overview["files_indexed"], 2)
        self.assertEqual(overview["symbols_indexed"], 1)
        with self.assertRaises(ValueError):
            catalog.call("sample-repo:repo.search_symbols", {"query": "sum", "unexpected": 1})
        with self.assertRaises(PermissionError):
            catalog.call("sample-repo:repo.get_symbol", {"qualified_name": "calculate_total"})

    def test_manifest_revocation_takes_effect_without_server_restart(self):
        self.approve(("repo.overview",))
        catalog = RepositoryCatalog([self.root])
        self.assertEqual(len(catalog.capabilities()), 1)
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        data["status"] = "draft"
        data["approved_handlers"] = []
        self.manifest.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(catalog.capabilities(), [])
        with self.assertRaises(PermissionError):
            catalog.call("sample-repo:repo.overview", {})

    def test_mcp_bootstrap_is_small_and_calls_return_structured_text(self):
        self.approve(("repo.overview",))
        server = MCPStdioServer(RepositoryCatalog([self.root]))
        initialized = server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        })
        self.assertEqual(initialized["result"]["protocolVersion"], "2025-11-25")
        listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual([tool["name"] for tool in listed["result"]["tools"]],
                         ["search_capabilities", "call_capability"])
        called = server.handle({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "call_capability", "arguments": {
                "capability_id": "sample-repo:repo.overview", "arguments": {}}},
        })
        payload = json.loads(called["result"]["content"][0]["text"])
        self.assertEqual(payload["repository"], "sample-repo")
        rejected = server.handle({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "call_capability", "arguments": {
                "capability_id": "sample-repo:python.exec", "arguments": {}}},
        })
        self.assertTrue(rejected["result"]["isError"])

    def test_stdio_serves_one_request_per_line_and_omits_notification_response(self):
        self.approve(("repo.overview",))
        server = MCPStdioServer(RepositoryCatalog([self.root]))
        incoming = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n"
        )
        outgoing = io.StringIO()
        server.serve(incoming, outgoing)
        lines = outgoing.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[1])["result"], {})


if __name__ == "__main__":
    unittest.main()
