from cadfree.agent.mcp import PROTOCOL, handle_rpc
from cadfree.main import create_app
from fastapi.testclient import TestClient


def _meta():
    return {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL,
        "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }


def _headers(method: str, name: str | None = None) -> dict[str, str]:
    headers = {
        "MCP-Protocol-Version": PROTOCOL,
        "Mcp-Method": method,
        "Accept": "application/json, text/event-stream",
    }
    if name:
        headers["Mcp-Name"] = name
    return headers


def test_mcp_discover_is_stateless(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    client = TestClient(create_app())
    health = client.get("/api/health").json()
    assert health["mcp"]["sessions"] is False
    assert health["mcp"]["protocol"] == PROTOCOL

    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": _meta()}},
        headers=_headers("server/discover"),
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["resultType"] == "complete"
    assert PROTOCOL in result["supportedVersions"]
    assert "tools" in result["capabilities"]
    assert "check_feasibility" in result["instructions"].lower()
    assert "catalog" in result["instructions"].lower()
    assert "score_vehicle" not in result["instructions"].lower()


def test_mcp_tools_list_and_call(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    client = TestClient(create_app())
    listed = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"_meta": _meta()}},
        headers=_headers("tools/list"),
    )
    names = {t["name"] for t in listed.json()["result"]["tools"]}
    assert "ask_survey" in names
    assert "check_feasibility" in names
    assert "search_parts" in names
    assert "score_vehicle" not in names
    assert "propose_vehicle" not in names
    assert all("project_id" in (t["inputSchema"].get("properties") or {}) for t in listed.json()["result"]["tools"])

    created = client.post("/api/projects", json={"name": "mcp", "spec_text": "bracket", "constraints": {}})
    pid = created.json()["id"]
    called = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "get_workshop",
                "arguments": {"project_id": pid},
                "_meta": _meta(),
            },
        },
        headers=_headers("tools/call", "get_workshop"),
    )
    assert called.status_code == 200
    body = called.json()["result"]
    assert body["resultType"] == "complete"
    catalog = body["structuredContent"]["catalog"]
    assert catalog
    class_ids = {c["id"] for c in catalog["cots_classes"]}
    assert "whoop_65" in class_ids
    assert "five_inch" in class_ids


def test_mcp_header_mismatch():
    status, payload = handle_rpc(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/list",
            "params": {"_meta": _meta()},
        },
        {"mcp-protocol-version": PROTOCOL, "mcp-method": "server/discover"},
    )
    assert status == 400
    assert payload["error"]["code"] == -32020


def test_mcp_ask_survey_is_input_required_not_a_session(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    client = TestClient(create_app())
    pid = client.post("/api/projects", json={"name": "s", "spec_text": "drone", "constraints": {}}).json()["id"]
    first = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "ask_survey",
                "arguments": {"project_id": pid, "template": "drone"},
                "_meta": _meta(),
            },
        },
        headers=_headers("tools/call", "ask_survey"),
    )
    result = first.json()["result"]
    assert result["resultType"] == "input_required"
    sid = result["requestState"]
    retry = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "ask_survey",
                "arguments": {"project_id": pid, "template": "drone"},
                "requestState": sid,
                "inputResponses": {
                    "survey": {
                        "action": "accept",
                        "content": {"speed_mph": 25, "budget_usd": 100},
                    }
                },
                "_meta": _meta(),
            },
        },
        headers=_headers("tools/call", "ask_survey"),
    )
    done = retry.json()["result"]
    assert done["resultType"] == "complete"
    assert done["structuredContent"]["answers"]["speed_mph"] == 25
