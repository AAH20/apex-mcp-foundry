"""Source-grounded import and call dependency graph analyzer with cycle detection.

Extracts inter-module import edges and call sites from Python ASTs, assigns
formal confidence labels, and applies Tarjan's Strongly Connected Components
(SCC) algorithm for polynomial-time cycle detection.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple


class DependencyGraphAnalyzer:
    """Analyzes imports, call graphs, and detects dependency cycles across repository modules."""

    def __init__(self, root: str | Path, *, max_files: int = 5000):
        self.root = Path(root).expanduser().resolve(strict=True)
        self.max_files = max_files

    def build_graph(self) -> Dict[str, Any]:
        """Build directed dependency graph with confidence annotations and cycles."""
        modules: Dict[str, Path] = {}
        nodes: Dict[str, Dict[str, Any]] = {}
        edges: List[Dict[str, Any]] = []
        adj: Dict[str, Set[str]] = defaultdict(set)

        # Collect Python source files
        py_files = sorted(self.root.rglob("*.py"))
        for path in py_files[: self.max_files]:
            if any(part.startswith(".") or part in {"__pycache__", "venv", ".venv"} for part in path.parts):
                continue
            rel = path.relative_to(self.root).as_posix()
            mod_name = rel[:-3].replace("/", ".")
            if mod_name.endswith(".__init__"):
                mod_name = mod_name[:-9]
            modules[mod_name] = path
            nodes[mod_name] = {
                "id": mod_name,
                "path": rel,
                "size_bytes": path.stat().st_size,
                "imports": [],
                "calls": [],
            }

        # Parse AST for imports and call sites
        for mod_name, path in modules.items():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, UnicodeError, SyntaxError, RecursionError):
                continue

            class GraphVisitor(ast.NodeVisitor):
                def __init__(self, current_mod: str):
                    self.current_mod = current_mod

                def visit_Import(self, node: ast.Import):
                    for alias in node.names:
                        target = alias.name
                        nodes[self.current_mod]["imports"].append(target)
                        # Check if internal
                        for internal_mod in modules:
                            if target == internal_mod or target.startswith(internal_mod + "."):
                                adj[self.current_mod].add(internal_mod)
                                edges.append({
                                    "source": self.current_mod,
                                    "target": internal_mod,
                                    "type": "import",
                                    "confidence": "EXACT_AST_IMPORT",
                                    "line": node.lineno,
                                })

                def visit_ImportFrom(self, node: ast.ImportFrom):
                    if node.module:
                        target_mod = node.module
                        nodes[self.current_mod]["imports"].append(target_mod)
                        for internal_mod in modules:
                            if target_mod == internal_mod or target_mod.startswith(internal_mod + "."):
                                adj[self.current_mod].add(internal_mod)
                                edges.append({
                                    "source": self.current_mod,
                                    "target": internal_mod,
                                    "type": "import_from",
                                    "confidence": "EXACT_AST_IMPORT",
                                    "line": node.lineno,
                                })

                def visit_Call(self, node: ast.Call):
                    called_name = None
                    if isinstance(node.func, ast.Name):
                        called_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        called_name = node.func.attr
                    if called_name:
                        nodes[self.current_mod]["calls"].append(called_name)

            GraphVisitor(mod_name).visit(tree)

        # Detect Strongly Connected Components via Tarjan's algorithm
        sccs = self._tarjan_scc(sorted(modules.keys()), adj)
        cycles = [comp for comp in sccs if len(comp) > 1 or (len(comp) == 1 and comp[0] in adj[comp[0]])]

        # Compute graph metrics
        in_degree: Dict[str, int] = defaultdict(int)
        out_degree: Dict[str, int] = defaultdict(int)
        for e in edges:
            out_degree[e["source"]] += 1
            in_degree[e["target"]] += 1

        for mod in nodes:
            nodes[mod]["in_degree"] = in_degree[mod]
            nodes[mod]["out_degree"] = out_degree[mod]

        return {
            "repository": self.root.name,
            "total_modules": len(nodes),
            "total_edges": len(edges),
            "is_acyclic": len(cycles) == 0,
            "cycles_detected": len(cycles),
            "strongly_connected_components": cycles,
            "nodes": nodes,
            "edges": edges,
        }

    @staticmethod
    def _tarjan_scc(vertices: List[str], adj: Dict[str, Set[str]]) -> List[List[str]]:
        """Tarjan's strongly connected components algorithm in O(V + E)."""
        idx = [0]
        stack: List[str] = []
        on_stack: Set[str] = set()
        indices: Dict[str, int] = {}
        lowlink: Dict[str, int] = {}
        result: List[List[str]] = []

        def strongconnect(v: str):
            indices[v] = idx[0]
            lowlink[v] = idx[0]
            idx[0] += 1
            stack.append(v)
            on_stack.add(v)

            for w in sorted(adj.get(v, set())):
                if w not in indices:
                    strongconnect(w)
                    lowlink[v] = min(lowlink[v], lowlink[w])
                elif w in on_stack:
                    lowlink[v] = min(lowlink[v], indices[w])

            if lowlink[v] == indices[v]:
                scc: List[str] = []
                while True:
                    w = stack.pop()
                    on_stack.remove(w)
                    scc.append(w)
                    if w == v:
                        break
                result.append(scc)

        for v in vertices:
            if v not in indices:
                strongconnect(v)

        return result
