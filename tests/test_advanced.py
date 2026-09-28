import json
import tempfile
import unittest
from pathlib import Path

from apex_mcp_foundry.benchmark import FoundryBenchmarkHarness
from apex_mcp_foundry.catalog import RepositoryCatalog
from apex_mcp_foundry.cli import _init_manifest
from apex_mcp_foundry.graph import DependencyGraphAnalyzer
from apex_mcp_foundry.knapsack import SubmodularKnapsackSelector
from apex_mcp_foundry.sandbox import SandboxedAdapterRunner, SecurityViolationError


class AdvancedFoundryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "test-repo"
        self.root.mkdir()
        (self.root / "core.py").write_text(
            '"""Core math solver."""\n\n'
            'def add(a: int, b: int) -> int:\n'
            '    """Add two numbers."""\n'
            '    return a + b\n\n'
            'def multiply(x: int, y: int) -> int:\n'
            '    """Multiply numbers."""\n'
            '    return x * y\n',
            encoding="utf-8",
        )
        (self.root / "consumer.py").write_text(
            'from core import add\n\n'
            'def compute(n: int) -> int:\n'
            '    return add(n, 10)\n',
            encoding="utf-8",
        )
        self.manifest = _init_manifest(self.root, approve_all=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_submodular_budgeted_knapsack_and_prefix_sort(self):
        catalog = RepositoryCatalog([self.root])
        capabilities = catalog.capabilities()
        self.assertTrue(len(capabilities) >= 3)

        selector = SubmodularKnapsackSelector(token_budget=250)
        res = selector.select(capabilities, "search declarations symbols", max_tokens=250)
        
        self.assertLessEqual(res["tokens_used"], 250)
        self.assertTrue(res["selected_count"] >= 1)
        self.assertEqual(res["mode"], "submodular")

        # Verify Canonical Radix Prefix Ordering is deterministic
        ordered = selector.canonical_prefix_sort(res["capabilities"])
        self.assertEqual(res["capabilities"], ordered)

    def test_dependency_graph_and_tarjan_cycle_detection(self):
        analyzer = DependencyGraphAnalyzer(self.root)
        graph = analyzer.build_graph()

        self.assertEqual(graph["repository"], "test-repo")
        self.assertTrue(graph["is_acyclic"])
        self.assertEqual(graph["cycles_detected"], 0)
        self.assertTrue(any(e["source"] == "consumer" and e["target"] == "core" for e in graph["edges"]))

        # Now introduce a circular dependency (core imports consumer)
        (self.root / "core.py").write_text(
            'from consumer import compute\ndef add(a, b): return a + b\n', encoding="utf-8"
        )
        cyclic_graph = DependencyGraphAnalyzer(self.root).build_graph()
        self.assertFalse(cyclic_graph["is_acyclic"])
        self.assertEqual(cyclic_graph["cycles_detected"], 1)
        cycle_nodes = set(cyclic_graph["strongly_connected_components"][0])
        self.assertEqual(cycle_nodes, {"core", "consumer"})

    def test_sandboxed_adapter_runner_safety(self):
        runner = SandboxedAdapterRunner(self.root)
        
        # Test safe execution
        res = runner.execute("core.py", "add", {"a": 15, "b": 25})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["result"], 40)
        self.assertTrue(res["execution_time_us"] > 0)

        # Test path escape rejection
        with self.assertRaises(SecurityViolationError):
            runner.execute("../outside.py", "add", {})

        # Test prohibited import rejection
        (self.root / "malicious.py").write_text(
            'import subprocess\ndef exploit(): return 1\n', encoding="utf-8"
        )
        with self.assertRaises(SecurityViolationError):
            runner.execute("malicious.py", "exploit", {})

        # Test taint detection on arguments
        with self.assertRaises(SecurityViolationError):
            runner.execute("core.py", "add", {"a": "__import__('os').system('ls')", "b": 1})

    def test_catalog_budgeted_selection_call(self):
        catalog = RepositoryCatalog([self.root])
        # Call repo.budgeted_selection
        res = catalog.call("test-repo:repo.budgeted_selection", {"query": "symbols", "token_budget": 500})
        self.assertIn("capabilities", res)
        self.assertIn("tokens_used", res)

    def test_catalog_dependency_graph_call(self):
        catalog = RepositoryCatalog([self.root])
        res = catalog.call("test-repo:repo.dependency_graph", {})
        self.assertIn("total_modules", res)
        self.assertIn("cycles_detected", res)

    def test_benchmark_harness(self):
        catalog = RepositoryCatalog([self.root])
        harness = FoundryBenchmarkHarness(catalog)
        telemetry = harness.run_all(iterations=5)
        self.assertIn("token_reduction_pct", telemetry)
        self.assertIn("prefix_cache_retention_pct", telemetry)
        self.assertIn("discovery_latency_us", telemetry)
