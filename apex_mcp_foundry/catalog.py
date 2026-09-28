"""Lazy repository catalog; only operator-approved, allowlisted handlers are visible."""
from __future__ import annotations

import json
from pathlib import Path

from .repository import RepositoryAnalyzer, repository_id

HANDLERS = {
    "repo.overview": {
        "description": "Return a bounded, read-only inventory and analysis summary for one repository.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "repo.search_symbols": {
        "description": "Search indexed Python declarations and their docstrings without executing repository code.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "minLength": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        }, "required": ["query"], "additionalProperties": False},
    },
    "repo.get_symbol": {
        "description": "Return metadata for one indexed Python declaration; source bodies are not returned.",
        "inputSchema": {"type": "object", "properties": {
            "qualified_name": {"type": "string", "minLength": 1},
        }, "required": ["qualified_name"], "additionalProperties": False},
    },
}


def _validate_args(value, schema, path="$", errors=None):
    errors = [] if errors is None else errors
    if not isinstance(value, dict):
        return [f"{path}: expected object"]
    properties = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in value:
            errors.append(f"{path}.{key}: required field missing")
    for key, child in value.items():
        rule = properties.get(key)
        if rule is None:
            if schema.get("additionalProperties", True) is False:
                errors.append(f"{path}.{key}: unknown field")
            continue
        kind = rule["type"]
        valid = {
            "string": lambda: isinstance(child, str),
            "integer": lambda: type(child) is int,
            "number": lambda: type(child) in (int, float),
            "boolean": lambda: type(child) is bool,
            "object": lambda: isinstance(child, dict),
            "array": lambda: isinstance(child, list),
        }[kind]()
        if not valid:
            errors.append(f"{path}.{key}: expected {kind}")
            continue
        if "minLength" in rule and len(child) < rule["minLength"]:
            errors.append(f"{path}.{key}: must not be empty")
        if "minimum" in rule and child < rule["minimum"]:
            errors.append(f"{path}.{key}: below minimum {rule['minimum']}")
        if "maximum" in rule and child > rule["maximum"]:
            errors.append(f"{path}.{key}: above maximum {rule['maximum']}")
    return errors


class RepositoryCatalog:
    def __init__(self, roots, *, max_files=5000, max_symbols=25000):
        if not roots:
            raise ValueError("At least one repository path is required")
        self.roots = {}
        for raw_path in roots:
            root = Path(raw_path).expanduser().resolve(strict=True)
            if not root.is_dir():
                raise ValueError(f"Repository path is not a directory: {root}")
            rid = repository_id(root)
            if rid in self.roots:
                raise ValueError(f"Repository ID collision {rid!r}; use distinct directory names")
            self.roots[rid] = root
        self.max_files = max_files
        self.max_symbols = max_symbols
        self._analyzers = {}

    def repository_ids(self):
        return sorted(self.roots)

    def _analyzer(self, rid):
        if rid not in self.roots:
            raise KeyError(f"Unknown repository ID {rid!r}")
        if rid not in self._analyzers:
            self._analyzers[rid] = RepositoryAnalyzer(
                self.roots[rid], max_files=self.max_files, max_symbols=self.max_symbols)
        return self._analyzers[rid]

    def _approved_handlers(self, rid):
        # Re-read on every discovery and invocation so local revocation applies immediately.
        manifest_path = self.roots[rid] / ".mcp-foundry" / "capabilities.json"
        if not manifest_path.is_file() or manifest_path.is_symlink():
            return set()
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid capability manifest for {rid}: {exc}") from exc
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise ValueError(f"Unsupported capability manifest for {rid}")
        if manifest.get("repository_id") != rid:
            raise ValueError(f"Repository ID mismatch in manifest for {rid}")
        if manifest.get("status") != "approved":
            return set()
        handlers = manifest.get("approved_handlers")
        if not isinstance(handlers, list) or any(
                type(item) is not str or item not in HANDLERS for item in handlers):
            raise ValueError(f"Manifest contains an unknown handler for {rid}")
        return set(handlers)

    def capabilities(self):
        available = []
        for rid in self.repository_ids():
            approved = self._approved_handlers(rid)
            for handler in sorted(approved):
                definition = HANDLERS[handler]
                available.append({
                    "id": f"{rid}:{handler}",
                    "repository_id": rid,
                    "name": handler,
                    "description": definition["description"],
                    "inputSchema": definition["inputSchema"],
                    "execution": "read_only_static_analysis",
                    "requires_repository_code_execution": False,
                })
        return available

    def search(self, query, *, limit=25):
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        terms = query.casefold().split()
        ranked = []
        for capability in self.capabilities():
            text = " ".join((capability["id"], capability["repository_id"],
                             capability["name"], capability["description"])).casefold()
            score = sum(term in text for term in terms)
            if score:
                ranked.append((score, capability))
        ranked.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
        return {"query": query, "total_matches": len(ranked),
                "results": [item for _, item in ranked[:limit]]}

    def call(self, capability_id, arguments):
        if not isinstance(capability_id, str) or ":" not in capability_id:
            raise ValueError("capability_id must use repository_id:handler format")
        rid, handler = capability_id.split(":", 1)
        if handler not in HANDLERS:
            raise KeyError(f"Handler {handler!r} is not approved")
        if handler not in self._approved_handlers(rid):
            raise PermissionError(f"Capability {capability_id!r} is not approved in its repository manifest")
        schema = HANDLERS[handler]["inputSchema"]
        errors = _validate_args(arguments, schema)
        if errors:
            raise ValueError("Invalid arguments: " + "; ".join(errors))
        analyzer = self._analyzer(rid)
        if handler == "repo.overview":
            return analyzer.overview()
        if handler == "repo.search_symbols":
            return analyzer.search_symbols(arguments["query"], limit=arguments.get("limit", 20))
        if handler == "repo.get_symbol":
            return analyzer.get_symbol(arguments["qualified_name"])
        raise KeyError(f"No callable implementation for {handler!r}")
