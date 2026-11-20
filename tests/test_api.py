"""REST + MCP endpoints of the web app (no AI involved)."""
import pytest
from fastapi.testclient import TestClient

from dcgen import service


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    service.HOME = tmp_path_factory.mktemp("dcgen-home")
    service.STATE.clear()
    from dcgen.api import app
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"]


def test_end_to_end(client):
    assert client.post("/api/projects", json={"name": "t1", "brief": {"topology": "4M3"}}).status_code == 200
    ex = client.get("/api/example").json()
    assert client.put("/api/projects/t1/site", json=ex["site"]).status_code == 200
    r = client.post("/api/projects/t1/options?max_options=2")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["options"] and body["selected_summary"]["electrical"]["topology"] == "4M3"
    r = client.post("/api/projects/t1/move", json={"element_id": "SUB", "dx": 0, "dy": 5})
    assert r.status_code == 200 and r.json()["checks"]
    r = client.get("/api/projects/t1/files/ifc")
    assert r.status_code == 200 and r.content.startswith(b"ISO-10303-21")
    assert "window.DCGEN_PROJECT" in client.get("/editor/t1").text


def test_bad_names_and_missing_site(client):
    assert client.post("/api/projects", json={"name": "../etc"}).status_code == 400
    client.post("/api/projects", json={"name": "nosite"})
    assert client.post("/api/projects/nosite/options").status_code == 400


def test_mcp_mounted(client):
    r = client.post("/mcp", headers={"Accept": "application/json, text/event-stream",
                                     "Content-Type": "application/json"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "test", "version": "0"}}})
    assert r.status_code == 200, r.text
    assert "dcgen" in r.text
