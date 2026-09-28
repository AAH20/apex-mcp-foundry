"""Command-line interface for inspecting repositories, serving approved capabilities, and running benchmarks."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .benchmark import FoundryBenchmarkHarness
from .catalog import RepositoryCatalog
from .graph import DependencyGraphAnalyzer
from .repository import RepositoryAnalyzer, repository_id
from .server import MCPStdioServer
from .transport import create_mcp_http_server


def _init_manifest(root: Path, *, force: bool = False, approve_all: bool = False):
    rid = repository_id(root)
    directory = root / ".mcp-foundry"
    path = directory / "capabilities.json"
    if path.exists() and not force:
        raise FileExistsError(f"Manifest exists: {path}; pass --force to replace it")
    directory.mkdir(parents=True, exist_ok=True)
    all_handlers = [
        "repo.overview",
        "repo.search_symbols",
        "repo.get_symbol",
        "repo.dependency_graph",
        "repo.budgeted_selection",
        "repo.execute_adapter",
    ]
    payload = {
        "schema_version": 1,
        "repository_id": rid,
        "status": "approved" if approve_all else "draft",
        "approved_handlers": all_handlers if approve_all else [],
        "available_handlers": all_handlers,
        "review_notes": "Review the repository path and data exposure before approving handlers. Read-only static analysis and sandboxed adapters are available.",
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def build_parser():
    parser = argparse.ArgumentParser(prog="apex-mcp-foundry", description="Apex MCP Foundry - Universal Capability & Execution Gateway")
    commands = parser.add_subparsers(dest="command", required=True)

    # inspect
    inspect = commands.add_parser("inspect", help="Analyze a repository without executing its code")
    inspect.add_argument("repository")
    inspect.add_argument("--max-files", type=int, default=5000)
    inspect.add_argument("--max-symbols", type=int, default=25000)

    # init
    init = commands.add_parser("init", help="Create a draft operator-review manifest")
    init.add_argument("repository")
    init.add_argument("--force", action="store_true", help="Overwrite existing manifest")
    init.add_argument("--approve-all", action="store_true", help="Immediately mark status=approved with all handlers")

    # graph
    graph = commands.add_parser("graph", help="Analyze and export inter-module dependency graph and detect cycles")
    graph.add_argument("repository")
    graph.add_argument("--max-files", type=int, default=5000)

    # benchmark
    benchmark = commands.add_parser("benchmark", help="Run empirical benchmarks matching evaluation-plan.md")
    benchmark.add_argument("repositories", nargs="+", help="Repository directories to benchmark")
    benchmark.add_argument("--iterations", type=int, default=50, help="Number of benchmark iterations")

    # scan-all
    scan_all = commands.add_parser("scan-all", help="Scan parent directory of repositories and initialize manifests on demand")
    scan_all.add_argument("parent_directory", help="Parent directory containing git repositories")
    scan_all.add_argument("--approve", action="store_true", help="Auto-approve all discovered repositories")

    # serve
    serve = commands.add_parser("serve", help="Serve approved capabilities over MCP stdio or HTTP/SSE")
    serve.add_argument("repositories", nargs="+", help="Repository directories; IDs derive from directory names")
    serve.add_argument("--transport", choices=["stdio", "http", "sse"], default="stdio", help="Transport protocol (default: stdio)")
    serve.add_argument("--host", default="127.0.0.1", help="HTTP/SSE bind host (default: 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8080, help="HTTP/SSE bind port (default: 8080)")
    serve.add_argument("--token", help="Bearer token secret for HTTP/SSE authentication")
    serve.add_argument("--max-files", type=int, default=5000)
    serve.add_argument("--max-symbols", type=int, default=25000)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "inspect":
            result = RepositoryAnalyzer(args.repository, max_files=args.max_files,
                                        max_symbols=args.max_symbols).analyze()
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0

        if args.command == "init":
            path = _init_manifest(Path(args.repository).expanduser().resolve(strict=True),
                                  force=args.force, approve_all=args.approve_all)
            print(path)
            if not args.approve_all:
                print("Review the draft, then set status=approved and select approved_handlers before serving.")
            else:
                print("Manifest created with status=approved and all handlers enabled.")
            return 0

        if args.command == "graph":
            analyzer = DependencyGraphAnalyzer(args.repository, max_files=args.max_files)
            result = analyzer.build_graph()
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0

        if args.command == "benchmark":
            catalog = RepositoryCatalog(args.repositories)
            harness = FoundryBenchmarkHarness(catalog)
            telemetry = harness.run_all(iterations=args.iterations)
            harness.print_report(telemetry)
            return 0

        if args.command == "scan-all":
            parent = Path(args.parent_directory).expanduser().resolve(strict=True)
            if not parent.is_dir():
                raise ValueError(f"Not a directory: {parent}")
            discovered = 0
            for child in sorted(parent.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    try:
                        _init_manifest(child, force=True, approve_all=args.approve)
                        discovered += 1
                        status_str = "APPROVED" if args.approve else "DRAFT"
                        print(f"[{status_str}] Initialized manifest for: {child.name}")
                    except Exception as exc:
                        print(f"[SKIP] {child.name}: {exc}")
            print(f"\nSuccessfully scanned and initialized {discovered} repositories.")
            return 0

        if args.command == "serve":
            catalog = RepositoryCatalog(args.repositories, max_files=args.max_files,
                                        max_symbols=args.max_symbols)
            if args.transport == "stdio":
                return MCPStdioServer(catalog).serve()
            else:
                print(f"Starting Apex MCP Foundry HTTP/SSE Server on http://{args.host}:{args.port}")
                if args.token:
                    print(f"Authentication: Bearer Token Enabled")
                srv = create_mcp_http_server(catalog, host=args.host, port=args.port, auth_token=args.token)
                try:
                    srv.serve_forever()
                except KeyboardInterrupt:
                    print("\nShutting down server.")
                    srv.shutdown()
                return 0

    except (OSError, ValueError) as exc:
        print(f"apex-mcp-foundry: {exc}", file=sys.stderr)
        return 2
    return 2
