"""Apex MCP Foundry: Universal Capability & Execution Hypervisor."""
from __future__ import annotations

from .catalog import RepositoryCatalog, HANDLERS
from .repository import RepositoryAnalyzer, repository_id
from .server import MCPStdioServer
from .knapsack import SubmodularKnapsackSelector
from .graph import DependencyGraphAnalyzer
from .sandbox import SandboxedAdapterRunner
from .benchmark import FoundryBenchmarkHarness

__version__ = "0.2.0"
__all__ = [
    "RepositoryCatalog",
    "HANDLERS",
    "RepositoryAnalyzer",
    "repository_id",
    "MCPStdioServer",
    "SubmodularKnapsackSelector",
    "DependencyGraphAnalyzer",
    "SandboxedAdapterRunner",
    "FoundryBenchmarkHarness",
]
