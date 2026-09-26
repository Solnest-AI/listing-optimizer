"""
mcp_http.py — minimal read-only client for a remote MCP server over streamable HTTP.

RankBreeze and IntelliHost expose their data only as MCP servers. This speaks just enough
of the protocol to call one read tool: initialize, send `notifications/initialized`, then
`tools/call`. Responses may arrive as JSON or as a server-sent-event stream.

Never log the URL (RankBreeze puts the key in the path) or the Authorization header.
"""
from __future__ import annotations

import json

import httpx

PROTOCOL = "2025-06-18"
# Cloudflare in front of IntelliHost rejects Python's default user agent (Error 1010).
USER_AGENT = "Mozilla/5.0 (listing-optimizer)"


class MCPError(RuntimeError):
    """A transport, protocol or tool error. Messages never contain the URL or token."""


def _parse(r: httpx.Response) -> dict:
    if r.headers.get("content-type", "").startswith("text/event-stream"):
        for line in r.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:])
        return {}
    return r.json() if r.text.strip() else {}


class Session:
    def __init__(self, url: str, token: str | None = None, timeout: float = 90):
        self.url = url
        self.headers = {"Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json", "User-Agent": USER_AGENT}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.client = httpx.Client(timeout=timeout)
        self._id = 0
        try:
            r = self.client.post(url, headers=self.headers, json=self._msg("initialize", {
                "protocolVersion": PROTOCOL, "capabilities": {},
                "clientInfo": {"name": "listing-optimizer", "version": "1"}}))
        except httpx.HTTPError as e:
            raise MCPError(f"could not reach the MCP server ({type(e).__name__})") from e
        if r.status_code in (401, 403):
            raise MCPError(f"MCP server refused the credentials (HTTP {r.status_code})")
        if r.status_code >= 400:
            raise MCPError(f"MCP initialize failed (HTTP {r.status_code})")
        if r.headers.get("mcp-session-id"):
            self.headers["Mcp-Session-Id"] = r.headers["mcp-session-id"]
        self.client.post(url, headers=self.headers,
                         json={"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _msg(self, method: str, params: dict) -> dict:
        self._id += 1
        return {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}

    def call(self, tool: str, arguments: dict):
        """Return the tool's JSON result. A tool-level error raises MCPError with its text."""
        try:
            r = self.client.post(self.url, headers=self.headers,
                                 json=self._msg("tools/call", {"name": tool, "arguments": arguments}))
            data = _parse(r)
        except (httpx.HTTPError, ValueError) as e:
            raise MCPError(f"{tool}: bad response ({type(e).__name__})") from e
        if data.get("error"):
            raise MCPError(f"{tool}: {str(data['error'].get('message', data['error']))[:200]}")
        result = data.get("result") or {}
        text = "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        if result.get("isError"):
            raise MCPError(f"{tool}: {text[:300]}")
        try:
            return json.loads(text)
        except ValueError as e:
            raise MCPError(f"{tool}: result was not JSON") from e

    def close(self):
        self.client.close()
