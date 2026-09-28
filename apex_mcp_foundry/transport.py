"""Authenticated remote HTTP and Server-Sent Events (SSE) MCP transport.

Implements standard JSON-RPC 2.0 over HTTP POST and streaming Server-Sent Events,
enforcing Bearer Token authentication and payload size limits using the Python standard library.
"""
from __future__ import annotations

import hmac
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional

from .catalog import RepositoryCatalog
from .server import MCPStdioServer

logger = logging.getLogger("apex_mcp_foundry.transport")


class AuthenticatedMCPHandler(BaseHTTPRequestHandler):
    """HTTP and SSE request handler for Model Context Protocol."""

    server_instance: HTTPServer
    catalog: RepositoryCatalog
    auth_token: Optional[str] = None
    max_payload_bytes: int = 2_000_000

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _check_auth(self) -> bool:
        if not self.auth_token:
            return True
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return False
        token = auth_header[7:].strip()
        return hmac.compare_digest(token, self.auth_token)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self._send_cors_headers()
            self.end_headers()
            payload = json.dumps({"status": "healthy", "service": "apex-mcp-foundry"}).encode()
            self.wfile.write(payload)
            return

        if not self._check_auth():
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("Content-Type", "application/json")
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(b'{"error": "Unauthorized"}')
            return

        if self.path in {"/sse", "/events"}:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self._send_cors_headers()
            self.end_headers()
            init_event = f"event: endpoint\ndata: /rpc\n\n".encode()
            self.wfile.write(init_event)
            self.wfile.flush()
            return

        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def do_POST(self) -> None:
        if not self._check_auth():
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("Content-Type", "application/json")
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(b'{"error": "Unauthorized"}')
            return

        if self.path not in {"/rpc", "/jsonrpc", "/"}:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > self.max_payload_bytes:
            self.send_response(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            self.end_headers()
            return

        body = self.rfile.read(content_length)
        try:
            req_data = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self.send_response(HTTPStatus.BAD_REQUEST)
            self.send_header("Content-Type", "application/json")
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(b'{"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}}')
            return

        # Execute via stdio server handler
        stdio_srv = MCPStdioServer(self.catalog)
        response = stdio_srv.handle(req_data)

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self._send_cors_headers()
        self.end_headers()

        if response is not None:
            resp_bytes = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.wfile.write(resp_bytes)

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Quiet logging for high-throughput testing


def create_mcp_http_server(
    catalog: RepositoryCatalog,
    host: str = "127.0.0.1",
    port: int = 8080,
    auth_token: Optional[str] = None,
) -> HTTPServer:
    """Create a configured HTTPServer instance for MCP."""
    class ConfiguredHandler(AuthenticatedMCPHandler):
        pass

    ConfiguredHandler.catalog = catalog
    ConfiguredHandler.auth_token = auth_token

    return HTTPServer((host, port), ConfiguredHandler)
