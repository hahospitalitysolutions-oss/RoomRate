"""Minimal MCP client over Streamable HTTP, for server-to-server tool calls.

Only what RoomRate needs from a remote MCP server: open a session, list the
tools, call a tool. No sampling, no server-initiated requests, no resumable
streams. JSON-RPC 2.0 over POST; the server answers with plain JSON or with an
SSE stream that carries the response (MCP spec, Streamable HTTP transport).

* The ``Mcp-Session-Id`` the server hands out on ``initialize`` goes on every
  later request, with the negotiated ``MCP-Protocol-Version``.
* A 404 on a request that carried a session id means the session expired:
  the client opens a new one and retries that request once.
* One instance is used by one thread at a time (the scheduler jobs).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any

import httpx

PROTOCOL_VERSION = "2025-06-18"

# tools/list pages; a server that never stops paginating is cut here.
_MAX_TOOL_PAGES = 20


class McpError(RuntimeError):
    """Transport or JSON-RPC failure talking to an MCP server."""


class McpToolError(McpError):
    """The tool ran and reported an error (``isError: true``)."""

    def __init__(self, tool: str, message: str):
        super().__init__(f"{tool}: {message}")
        self.tool = tool


class _SessionExpired(Exception):
    pass


class McpHttpClient:
    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = 30.0,
        client_name: str = "roomrate",
        client_version: str = "1.0",
        http: httpx.Client | None = None,
    ):
        self.url = url
        self.client_name = client_name
        self.client_version = client_version
        self._http = http if http is not None else httpx.Client(timeout=timeout_seconds)
        self._owns_http = http is None
        self._session_id: str | None = None
        self._protocol_version: str | None = None
        self._next_id = 0
        self.server_info: dict[str, Any] | None = None

    def __enter__(self) -> McpHttpClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """End the session (best effort) and release the HTTP pool we own."""
        if self._session_id is not None:
            try:
                self._http.delete(self.url, headers=self._headers())
            except httpx.HTTPError:
                pass
            self._session_id = None
            self._protocol_version = None
        if self._owns_http:
            self._http.close()

    def initialize(self) -> dict[str, Any]:
        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": self.client_name, "version": self.client_version},
            },
            initializing=True,
        )
        self._protocol_version = str(result.get("protocolVersion") or PROTOCOL_VERSION)
        server_info = result.get("serverInfo")
        self.server_info = server_info if isinstance(server_info, dict) else None
        self._notify("notifications/initialized")
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(_MAX_TOOL_PAGES):
            result = self._call("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(tool for tool in result.get("tools") or [] if isinstance(tool, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._call("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise McpToolError(name, tool_result_text(result)[:500] or "isError without a message")
        return result

    def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._protocol_version is None:
            self.initialize()
        try:
            return self._request(method, params)
        except _SessionExpired:
            self._session_id = None
            self._protocol_version = None
            self.initialize()
            return self._request(method, params)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._protocol_version:
            headers["MCP-Protocol-Version"] = self._protocol_version
        return headers

    def _request(self, method: str, params: dict[str, Any], *, initializing: bool = False) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        try:
            # Streamed: a server may keep an SSE response open after the
            # answer; reading stops at the matching response.
            with self._http.stream("POST", self.url, json=body, headers=self._headers()) as response:
                if response.status_code == 404 and self._session_id and not initializing:
                    raise _SessionExpired()
                if response.status_code >= 400:
                    response.read()
                    raise McpError(f"{method}: HTTP {response.status_code}: {response.text[:300]}")
                session_id = response.headers.get("mcp-session-id")
                if session_id:
                    self._session_id = session_id
                message = _read_response(response, request_id)
        except httpx.HTTPError as exc:
            raise McpError(f"{method}: {type(exc).__name__}: {exc}") from exc
        error = message.get("error")
        if error is not None:
            detail = error if not isinstance(error, dict) else f"{error.get('code')} {error.get('message')}"
            raise McpError(f"{method}: JSON-RPC error {detail}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise McpError(f"{method}: response without a result object")
        return result

    def _notify(self, method: str) -> None:
        """Send a notification. It has no answer: a refusing status is not an error here."""
        try:
            self._http.post(self.url, json={"jsonrpc": "2.0", "method": method}, headers=self._headers())
        except httpx.HTTPError as exc:
            raise McpError(f"{method}: {type(exc).__name__}: {exc}") from exc


def tool_result_text(result: dict[str, Any]) -> str:
    """The text blocks of a tools/call result, joined."""
    return "\n".join(
        str(block.get("text", ""))
        for block in result.get("content") or []
        if isinstance(block, dict) and block.get("type") == "text"
    )


def tool_result_payload(result: dict[str, Any]) -> Any:
    """What a tool returned as data: structuredContent, else its text as JSON, else the text."""
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    text = tool_result_text(result).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def _read_response(response: httpx.Response, request_id: int) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("text/event-stream"):
        for message in _sse_messages(response.iter_lines()):
            if _answers(message, request_id):
                return message
        raise McpError("SSE stream ended without a response")
    response.read()
    try:
        message: Any = response.json()
    except ValueError as exc:
        raise McpError(f"non-JSON response ({content_type or 'no content type'})") from exc
    if isinstance(message, list):
        message = next((item for item in message if _answers(item, request_id)), None)
    if not isinstance(message, dict):
        raise McpError("response is not a JSON-RPC message")
    return message


def _answers(message: Any, request_id: int) -> bool:
    return (
        isinstance(message, dict)
        and message.get("id") == request_id
        and ("result" in message or "error" in message)
    )


def _sse_messages(lines: Iterable[str]) -> Iterator[Any]:
    """JSON payloads of the SSE events in ``lines``; non-JSON events are skipped."""
    data: list[str] = []
    for line in lines:
        if line == "":
            if data:
                yield from _decoded("\n".join(data))
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
    if data:
        yield from _decoded("\n".join(data))


def _decoded(data: str) -> Iterator[Any]:
    try:
        yield json.loads(data)
    except ValueError:
        return
