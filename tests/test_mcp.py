"""V3.2 tests — MCP server (/mcp): bearer auth, tools/list, read tools
SQLite DB ke against, aur tenant isolation MCP layer par bhi.

test_v31.py wala hi pattern: env pehle set, phir app.main fresh load
(apna tmp data/brands dir), taaki is file ka instance alag rahe.
MCP calls raw JSON-RPC over HTTP hain — bilkul waise jaise koi asli
MCP client (ChatGPT/Claude) bhejta hai.
"""
import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

# --- env PEHLE set karo, phir main ko fresh load karo -------------------
_TMP = tempfile.mkdtemp(prefix="aksynxa-mcp-test-")
_DATA = os.path.join(_TMP, "data")
_BRANDS = os.path.join(_TMP, "brands")
os.makedirs(_BRANDS, exist_ok=True)
for f in (PROJECT / "brands").glob("*.yaml"):
    shutil.copy(f, _BRANDS)
os.environ["AKSYNXA_DATA_DIR"] = _DATA
os.environ["BRANDS_DIR"] = _BRANDS
os.environ["ADMIN_PASSWORD"] = "testpass123"
os.environ["SCHEDULER_TICK_SECONDS"] = "3600"
os.environ["MCP_API_KEY"] = "test-mcp-key-123"
os.environ.pop("APP_ENV", None)
os.environ.pop("DATABASE_URL", None)
os.environ.pop("WHATSAPP_APP_SECRET", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

MCP_KEY = "test-mcp-key-123"
AUTH = {"Authorization": f"Bearer {MCP_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"}

EXPECTED_TOOLS = {
    "list_businesses", "get_dashboard_stats", "list_leads",
    "list_bookings", "list_reminders", "get_chat_history",
    "simulate_message",
}


def _load_main(name: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT / "app" / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


main = _load_main("app_main_mcp")


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def _rpc(client, method, params=None, msg_id=1, headers=None):
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=headers or AUTH)


def _handshake(client):
    r = _rpc(client, "initialize", {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"}}, msg_id=0)
    assert r.status_code == 200, r.text
    assert r.json()["result"]["serverInfo"]["name"] == "AKSynxa"
    n = client.post("/mcp", json={"jsonrpc": "2.0",
                                  "method": "notifications/initialized"},
                    headers=AUTH)
    assert n.status_code in (200, 202)


def _call_tool(client, name, arguments, msg_id=99):
    r = _rpc(client, "tools/call",
             {"name": name, "arguments": arguments}, msg_id=msg_id)
    assert r.status_code == 200, r.text
    payload = r.json()
    assert "result" in payload, payload
    result = payload["result"]
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


# ===========================================================================
# Auth — token ke bina / galat token = 401
# ===========================================================================
def test_mcp_auth_rejection(client):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    # Koi Authorization header hi nahi
    r = client.post("/mcp", json=body, headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"})
    assert r.status_code == 401
    # Galat token
    r = client.post("/mcp", json=body, headers={
        **AUTH, "Authorization": "Bearer galat-token"})
    assert r.status_code == 401
    # Sahi token -> 401 nahi (handshake aage ke tests me)
    r = _rpc(client, "ping")
    assert r.status_code == 200


# ===========================================================================
# tools/list — saare 7 tools hone chahiye
# ===========================================================================
def test_mcp_tools_list(client):
    _handshake(client)
    r = _rpc(client, "tools/list", {}, msg_id=2)
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert names == EXPECTED_TOOLS


# ===========================================================================
# Read tools — seeded SQLite data ke against + tenant isolation
# ===========================================================================
DENTAL_CUST = "919444444444"
AKS_CUST = "919555555555"


@pytest.fixture(scope="module")
def seeded(client):
    client.post("/simulate", json={"from": DENTAL_CUST, "text": "hi",
                                   "brand": "demo-dental"})
    # Dental par ek full booking bhi
    booker = "919666666666"
    for text in ("hi", "3", "Mcp Patient", "1", "kal 5 baje", "yes"):
        client.post("/simulate", json={"from": booker, "text": text,
                                       "brand": "demo-dental"})
    client.post("/simulate", json={"from": AKS_CUST, "text": "hi",
                                   "brand": "aksynxa"})
    return {"booker": booker}


def test_mcp_read_tools(client, seeded):
    _handshake(client)
    biz = _call_tool(client, "list_businesses", {})
    assert {b["slug"] for b in biz["businesses"]} == {"demo-dental", "aksynxa"}

    stats = _call_tool(client, "get_dashboard_stats",
                       {"brand": "demo-dental"})
    assert stats["brand"] == "demo-dental"
    assert stats["total_leads"] >= 2
    assert stats["bookings"] >= 1
    assert stats["total_chat_turns"] >= 2
    assert stats["messages_in"] >= 1 and stats["messages_out"] >= 1
    assert stats["pending_reminders"] >= 0

    bookings = _call_tool(client, "list_bookings", {"brand": "demo-dental"})
    assert bookings["count"] >= 1
    assert any(b["customer_number"] == seeded["booker"]
               for b in bookings["bookings"])

    hist = _call_tool(client, "get_chat_history",
                      {"brand": "demo-dental", "phone": DENTAL_CUST})
    assert hist["count"] >= 2
    assert all(t["customer_number"] == DENTAL_CUST for t in hist["turns"])

    reminders = _call_tool(client, "list_reminders",
                           {"brand": "demo-dental", "status": "pending"})
    assert reminders["brand"] == "demo-dental"
    assert all(r["brand_slug"] == "demo-dental"
               for r in reminders["reminders"])

    # simulate_message tool — bot ka reply wapas aana chahiye
    sim = _call_tool(client, "simulate_message",
                     {"brand": "demo-dental", "from_phone": "919777777777",
                      "text": "hi"})
    assert sim["brand_slug"] == "demo-dental"
    assert sim["replies"], sim


def test_mcp_tenant_isolation(client, seeded):
    _handshake(client)
    # AKSynxa ke leads me dental ka customer NAHI hona chahiye
    aks_leads = _call_tool(client, "list_leads",
                           {"brand": "aksynxa", "limit": 50})
    numbers = {l["customer_number"] for l in aks_leads["leads"]}
    assert AKS_CUST in numbers
    assert DENTAL_CUST not in numbers
    assert seeded["booker"] not in numbers

    dental_leads = _call_tool(client, "list_leads",
                              {"brand": "demo-dental", "limit": 50})
    d_numbers = {l["customer_number"] for l in dental_leads["leads"]}
    assert DENTAL_CUST in d_numbers and AKS_CUST not in d_numbers

    # Dusre brand ke phone ki chat history = khaali
    hist = _call_tool(client, "get_chat_history",
                      {"brand": "demo-dental", "phone": AKS_CUST})
    assert hist["count"] == 0 and hist["turns"] == []

    # Bookings bhi cross-brand leak nahi hone chahiye
    aks_bookings = _call_tool(client, "list_bookings", {"brand": "aksynxa"})
    assert all(b["customer_number"] != seeded["booker"]
               for b in aks_bookings["bookings"])

    # Unknown brand -> saaf error, koi data nahi
    err = _call_tool(client, "list_leads", {"brand": "no-such-brand"})
    assert "error" in err and "leads" not in err


# ===========================================================================
# Production me MCP_API_KEY na ho to /mcp disabled, app normal
# ===========================================================================
def test_mcp_disabled_in_production_without_key():
    old = {k: os.environ.get(k) for k in
           ("APP_ENV", "WHATSAPP_APP_SECRET", "AKSYNXA_DATA_DIR",
            "BRANDS_DIR", "MCP_API_KEY", "ADMIN_PASSWORD")}
    tmp2 = tempfile.mkdtemp(prefix="aksynxa-mcp-prod-")
    brands2 = os.path.join(tmp2, "brands")
    os.makedirs(brands2, exist_ok=True)
    for f in (PROJECT / "brands").glob("*.yaml"):
        shutil.copy(f, brands2)
    os.environ["APP_ENV"] = "production"
    os.environ["WHATSAPP_APP_SECRET"] = "prod-secret-for-mcp-test"
    os.environ["AKSYNXA_DATA_DIR"] = os.path.join(tmp2, "data")
    os.environ["BRANDS_DIR"] = brands2
    os.environ.pop("MCP_API_KEY", None)
    try:
        prod = _load_main("app_main_mcp_prod")
        with TestClient(prod.app, raise_server_exceptions=False) as c:
            # Main app bilkul normal — health OK
            assert c.get("/health").status_code == 200
            # Lekin /mcp mount hi nahi hua -> 404
            r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                     "method": "tools/list", "params": {}},
                       headers={"Content-Type": "application/json"})
            assert r.status_code == 404
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
