"""Read-only, source-grounded repository inventory. Repository code is never imported or run."""
from __future__ import annotations

import ast
import os
import re
from collections import Counter
from pathlib import Path

EXCLUDED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "vendor",
    "dist", "build", "target", "__pycache__", ".next", ".cache",
}
EXCLUDED_NAMES = {".env", ".env.local", "id_rsa", "id_ed25519", "credentials.json"}
EXCLUDED_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore"}
SOURCE_SUFFIXES = {
    ".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".java", ".cs", ".rb", ".php",
    ".swift", ".kt", ".scala", ".sh", ".md", ".toml", ".yaml", ".yml",
    ".json",
}


def repository_id(path: Path) -> str:
    """Make a stable, conservative catalog ID from a directory name."""
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", path.name.strip()).strip("-.").lower()
    if not value:
        raise ValueError(f"Cannot derive a repository ID from {path}")
    return value


class RepositoryAnalyzer:
    def __init__(self, root: str | Path, *, max_files: int = 5000,
                 max_symbols: int = 25000, max_file_bytes: int = 1_000_000):
        resolved = Path(root).expanduser().resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError(f"Repository path is not a directory: {resolved}")
        if type(max_files) is not int or not 1 <= max_files <= 100_000:
            raise ValueError("max_files must be between 1 and 100000")
        if type(max_symbols) is not int or not 1 <= max_symbols <= 500_000:
            raise ValueError("max_symbols must be between 1 and 500000")
        self.root = resolved
        self.max_files = max_files
        self.max_symbols = max_symbols
        self.max_file_bytes = max_file_bytes
        self._result: dict | None = None

    def analyze(self) -> dict:
        if self._result is not None:
            return self._result
        files: list[Path] = []
        warnings: list[str] = []
        truncated = False
        for folder, dirs, names in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(
                name for name in dirs
                if name not in EXCLUDED_DIRS and not (Path(folder) / name).is_symlink()
            )
            for name in sorted(names):
                path = Path(folder) / name
                if path.is_symlink() or name in EXCLUDED_NAMES or path.suffix.lower() in EXCLUDED_SUFFIXES:
                    continue
                if path.suffix.lower() not in SOURCE_SUFFIXES:
                    continue
                if not path.is_file():
                    continue
                files.append(path)
                if len(files) > self.max_files:
                    truncated = True
                    break
            if truncated:
                break

        symbols: list[dict] = []
        languages: Counter[str] = Counter()
        python_files = 0
        parse_errors = 0
        for path in files[:self.max_files]:
            relative = path.relative_to(self.root).as_posix()
            suffix = path.suffix.lower()
            language = {
                ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript",
                ".mjs": "JavaScript", ".cjs": "JavaScript", ".ts": "TypeScript",
                ".tsx": "TypeScript", ".go": "Go", ".rs": "Rust", ".c": "C",
                ".h": "C/C++", ".cc": "C++", ".cpp": "C++", ".hpp": "C++",
                ".java": "Java", ".cs": "C#", ".rb": "Ruby", ".php": "PHP",
                ".swift": "Swift", ".kt": "Kotlin", ".scala": "Scala",
                ".sh": "Shell", ".md": "Markdown", ".toml": "TOML",
                ".yaml": "YAML", ".yml": "YAML", ".json": "JSON",
            }.get(suffix, "Other")
            languages[language] += 1
            if suffix != ".py":
                continue
            python_files += 1
            try:
                if path.stat().st_size > self.max_file_bytes:
                    warnings.append(f"{relative}: exceeds parse size limit; inventory only")
                    continue
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=relative)
            except (OSError, UnicodeError, SyntaxError, RecursionError) as exc:
                parse_errors += 1
                warnings.append(f"{relative}: {type(exc).__name__}; inventory only")
                continue
            module_doc = ast.get_docstring(tree)

            class Collector(ast.NodeVisitor):
                def __init__(self):
                    self.scope: list[str] = []

                def visit_ClassDef(self, node):
                    self._definition(node, "class")

                def visit_FunctionDef(self, node):
                    self._definition(node, "function")

                def visit_AsyncFunctionDef(self, node):
                    self._definition(node, "function")

                def _definition(self, node, kind):
                    if len(symbols) >= self_outer.max_symbols:
                        return
                    name = ".".join([*self.scope, node.name])
                    args = node.args
                    parameters = [arg.arg for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]]
                    if args.vararg:
                        parameters.append("*" + args.vararg.arg)
                    if args.kwarg:
                        parameters.append("**" + args.kwarg.arg)
                    symbols.append({
                        "name": node.name, "qualified_name": name, "kind": kind,
                        "path": relative, "line": node.lineno, "parameters": parameters,
                        "summary": (ast.get_docstring(node) or "")[:400],
                    })
                    self.scope.append(node.name)
                    self.generic_visit(node)
                    self.scope.pop()

            self_outer = self
            Collector().visit(tree)
            if module_doc and not symbols:
                warnings.append(f"{relative}: module documentation found; no symbols parsed")

        self._result = {
            "repository": self.root.name,
            "files_indexed": min(len(files), self.max_files),
            "file_limit_reached": truncated,
            "python_files_parsed": python_files,
            "python_parse_errors": parse_errors,
            "symbols_indexed": len(symbols),
            "symbol_limit_reached": len(symbols) >= self.max_symbols,
            "languages": dict(sorted(languages.items())),
            "symbols": symbols,
            "warnings": warnings,
            "execution_performed": False,
        }
        return self._result

    def overview(self) -> dict:
        result = self.analyze()
        return {key: value for key, value in result.items() if key != "symbols"}

    def search_symbols(self, query: str, *, limit: int = 20) -> dict:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        terms = [term.casefold() for term in query.split()]
        matches = []
        for symbol in self.analyze()["symbols"]:
            searchable = " ".join((symbol["name"], symbol["qualified_name"],
                                   symbol["path"], symbol["summary"])).casefold()
            if all(term in searchable for term in terms):
                matches.append(symbol)
        return {"query": query, "total_matches": len(matches),
                "results": matches[:limit]}

    def get_symbol(self, qualified_name: str) -> dict:
        if not isinstance(qualified_name, str) or not qualified_name:
            raise ValueError("qualified_name must be a non-empty string")
        for symbol in self.analyze()["symbols"]:
            if symbol["qualified_name"] == qualified_name:
                return symbol
        raise KeyError(f"No Python symbol named {qualified_name!r} was indexed")
