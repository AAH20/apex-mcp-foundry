"""Command-line interface for inspecting repositories and serving their approved capabilities."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .catalog import RepositoryCatalog
from .repository import RepositoryAnalyzer, repository_id
from .server import MCPStdioServer


def _init_manifest(root: Path, *, force=False):
    rid = repository_id(root)
    directory = root / ".mcp-foundry"
    path = directory / "capabilities.json"
    if path.exists() and not force:
        raise FileExistsError(f"Manifest exists: {path}; pass --force to replace it")
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "repository_id": rid,
        "status": "draft",
        "approved_handlers": [],
        "available_handlers": ["repo.overview", "repo.search_symbols", "repo.get_symbol"],
        "review_notes": "Review the repository path and data exposure before approving handlers. Only built-in read-only static analysis handlers are accepted in this MVP.",
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def build_parser():
    parser = argparse.ArgumentParser(prog="apex-mcp-foundry")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Analyze a repository without executing its code")
    inspect.add_argument("repository")
    inspect.add_argument("--max-files", type=int, default=5000)
    inspect.add_argument("--max-symbols", type=int, default=25000)
    init = commands.add_parser("init", help="Create a draft operator-review manifest")
    init.add_argument("repository")
    init.add_argument("--force", action="store_true")
    serve = commands.add_parser("serve", help="Serve approved capabilities over MCP stdio")
    serve.add_argument("repositories", nargs="+", help="Repository directories; IDs derive from directory names")
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
                                  force=args.force)
            print(path)
            print("Review the draft, then set status=approved and select approved_handlers before serving.")
            return 0
        if args.command == "serve":
            catalog = RepositoryCatalog(args.repositories, max_files=args.max_files,
                                        max_symbols=args.max_symbols)
            return MCPStdioServer(catalog).serve()
    except (OSError, ValueError) as exc:
        print(f"apex-mcp-foundry: {exc}", file=sys.stderr)
        return 2
    return 2
