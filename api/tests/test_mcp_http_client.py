"""McpHttpClient: session handshake, JSON and SSE answers, expiry and errors."""

import json

import httpx
import pytest

from api.services.mcp_http_client import (
    McpError,
    McpHttpClient,
    McpToolError,
    tool_result_payload,
)

URL = "https://mcp.example.test/mcp"


class FakeServer:
    """A Streamable-HTTP MCP server: JSON for most answers, SSE for tools/list."""

    def __init__(self, *, expire_first_call=False, tool_result=None, rpc_error=False):
        self.requests: list[tuple[str, dict, dict]] = []
        self.sessions = 0
        self.expire_first_call = expire_first_call
        self.tool_result = tool_result or {"content": [{"type": "text", "text": '{"trips": []}'}]}
        self.rpc_error = rpc_error

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            self.requests.append(("DELETE", dict(request.headers), {}))
            return httpx.Response(200)
        body = json.loads(request.content)
        method = body.get("method")
        self.requests.append((method, dict(request.headers), body))
        if method == "initialize":
            self.sessions += 1
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": f"session-{self.sessions}"},
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"protocolVersion": "2025-03-26", "serverInfo": {"name": "fake", "version": "1"}},
                },
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            cursor = body["params"].get("cursor")
            page = (
                {"tools": [{"name": "get_ports"}], "nextCursor": "page-2"}
                if cursor is None
                else {"tools": [{"name": "search_trips", "inputSchema": {"type": "object"}}]}
            )
            # A server notification before the answer, as SSE servers may send.
            stream = (
                'event: message\ndata: {"jsonrpc": "2.0", "method": "notifications/progress"}\n\n'
                f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'id': body['id'], 'result': page})}\n\n"
            )
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=stream)
        if method == "tools/call":
            if self.expire_first_call:
                self.expire_first_call = False
                return httpx.Response(404, text="session not found")
            if self.rpc_error:
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32602, "message": "bad args"}}
                )
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": self.tool_result})
        return httpx.Response(400)


def _client(server: FakeServer) -> McpHttpClient:
    return McpHttpClient(URL, http=httpx.Client(transport=httpx.MockTransport(server)))


def test_first_call_opens_a_session_and_sends_its_id_and_version():
    server = FakeServer()
    client = _client(server)

    client.call_tool("search_trips", {"departurePort": "Piraeus"})

    methods = [method for method, _, _ in server.requests]
    assert methods == ["initialize", "notifications/initialized", "tools/call"]
    _, headers, body = server.requests[-1]
    assert headers["mcp-session-id"] == "session-1"
    assert headers["mcp-protocol-version"] == "2025-03-26"
    assert "text/event-stream" in headers["accept"]
    assert body["params"] == {"name": "search_trips", "arguments": {"departurePort": "Piraeus"}}
    assert client.server_info == {"name": "fake", "version": "1"}


def test_tools_list_reads_sse_answers_and_follows_cursors():
    client = _client(FakeServer())

    tools = client.list_tools()

    assert [tool["name"] for tool in tools] == ["get_ports", "search_trips"]


def test_an_expired_session_is_reopened_once():
    server = FakeServer(expire_first_call=True)
    client = _client(server)

    client.call_tool("search_trips", {})

    assert server.sessions == 2
    assert server.requests[-1][1]["mcp-session-id"] == "session-2"


def test_a_tool_error_raises_with_its_text():
    server = FakeServer(tool_result={"isError": True, "content": [{"type": "text", "text": "Unknown port X"}]})

    with pytest.raises(McpToolError, match="Unknown port X"):
        _client(server).call_tool("search_trips", {})


def test_a_json_rpc_error_raises():
    with pytest.raises(McpError, match="bad args"):
        _client(FakeServer(rpc_error=True)).call_tool("search_trips", {})


def test_http_failures_raise_mcp_errors():
    def refuse(request):
        raise httpx.ConnectError("blocked", request=request)

    client = McpHttpClient(URL, http=httpx.Client(transport=httpx.MockTransport(refuse)))
    with pytest.raises(McpError, match="ConnectError"):
        client.list_tools()

    client = McpHttpClient(URL, http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))))
    with pytest.raises(McpError, match="HTTP 503"):
        client.list_tools()


def test_close_ends_the_session():
    server = FakeServer()
    client = _client(server)
    client.list_tools()

    client.close()

    assert server.requests[-1][0] == "DELETE"
    assert server.requests[-1][1]["mcp-session-id"] == "session-1"


def test_tool_result_payload_prefers_structured_content_then_json_text():
    assert tool_result_payload({"structuredContent": {"a": 1}, "content": []}) == {"a": 1}
    assert tool_result_payload({"content": [{"type": "text", "text": '[1, 2]'}]}) == [1, 2]
    assert tool_result_payload({"content": [{"type": "text", "text": "plain words"}]}) == "plain words"
    assert tool_result_payload({"content": []}) is None


def test_a_refused_initialized_notification_does_not_break_the_session():
    """Notifications have no answer; a server that answers 405 to one still serves calls."""

    class StrictServer(FakeServer):
        def __call__(self, request):
            if request.method == "POST" and json.loads(request.content).get("method") == "notifications/initialized":
                return httpx.Response(405)
            return super().__call__(request)

    client = _client(StrictServer())

    assert client.call_tool("search_trips", {})["content"][0]["text"] == '{"trips": []}'
