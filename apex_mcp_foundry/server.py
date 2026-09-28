"""Small MCP stdio endpoint; only exposes catalog search and allowlisted static-analysis calls."""
from __future__ import annotations

import json
import sys

from .catalog import RepositoryCatalog

PROTOCOL_VERSION = "2025-11-25"
SERVER_INFO = {"name": "apex-mcp-foundry", "version": "0.1.0"}
BOOTSTRAP_TOOLS = [
    {
        "name": "search_capabilities",
        "title": "Search repository capabilities",
        "description": "Find approved repository capabilities by intent. Search first, then call a returned capability ID.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "minLength": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        }, "required": ["query"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "call_capability",
        "title": "Call an approved repository capability",
        "description": "Invoke one allowlisted, read-only repository-analysis capability returned by search_capabilities. Repository code is never executed.",
        "inputSchema": {"type": "object", "properties": {
            "capability_id": {"type": "string", "minLength": 1},
            "arguments": {"type": "object"},
        }, "required": ["capability_id", "arguments"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
    },
]


class MCPStdioServer:
    def __init__(self, catalog: RepositoryCatalog):
        self.catalog = catalog
        self.initialized = False

    @staticmethod
    def _error(request_id, code, message):
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": code, "message": message}}

    @staticmethod
    def _result(request_id, result):
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def handle(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return self._error(message.get("id") if isinstance(message, dict) else None,
                               -32600, "Invalid JSON-RPC request")
        method = message.get("method")
        request_id = message.get("id")
        if not isinstance(method, str):
            return self._error(request_id, -32600, "Missing method")
        if request_id is None:
            # MCP notifications have no response, including initialized.
            return None
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return self._error(request_id, -32602, "params must be an object")
        if method == "initialize":
            requested = params.get("protocolVersion")
            if requested and requested not in {PROTOCOL_VERSION, "2025-06-18", "2025-03-26"}:
                return self._error(request_id, -32602, "Unsupported protocol version")
            self.initialized = True
            return self._result(request_id, {
                "protocolVersion": requested or PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": "Search capabilities before calling them. This server indexes repositories statically and never imports or executes their code.",
            })
        if method == "ping":
            return self._result(request_id, {})
        if method == "tools/list":
            return self._result(request_id, {"tools": BOOTSTRAP_TOOLS})
        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments") or {}
            try:
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be an object")
                if tool_name == "search_capabilities":
                    value = self.catalog.search(arguments.get("query"),
                                                limit=arguments.get("limit", 25))
                elif tool_name == "call_capability":
                    value = self.catalog.call(arguments.get("capability_id"),
                                              arguments.get("arguments"))
                else:
                    return self._error(request_id, -32602, f"Unknown tool {tool_name!r}")
                return self._result(request_id, {"content": [{
                    "type": "text", "text": json.dumps(value, ensure_ascii=False, sort_keys=True)
                }]})
            except (KeyError, ValueError, TypeError, PermissionError) as exc:
                return self._result(request_id, {
                    "content": [{"type": "text", "text": str(exc)}], "isError": True
                })
        if method.startswith("notifications/"):
            return None
        return self._error(request_id, -32601, f"Method not found: {method}")

    def serve(self, input_stream=None, output_stream=None):
        input_stream = input_stream or sys.stdin
        output_stream = output_stream or sys.stdout
        for line in input_stream:
            try:
                message = json.loads(line)
                response = self.handle(message)
            except json.JSONDecodeError as exc:
                response = self._error(None, -32700, f"Parse error: {exc.msg}")
            if response is not None:
                output_stream.write(json.dumps(response, separators=(",", ":"),
                                              ensure_ascii=False) + "\n")
                output_stream.flush()
