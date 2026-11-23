"""V3.2 integrations tests — public REST API (key auth + isolation),
outbound webhooks (signature, firing, retry queue), embed token gate,
setup wizard (pages + draft save), admin key/webhook endpoints, PWA
assets, aur Python SDK (ASGI transport par).

test_v31/test_mcp wala hi pattern: env pehle, main fresh load, tmp dirs.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

# --- env PEHLE set karo, phir main ko fresh load karo -------------------
_TMP = tempfile.mkdtemp(prefix="aksynxa-int-test-")
_DATA = os.path.join(_TMP, "data")
_BRANDS = os.path.join(_TMP, "brands")
os.makedirs(_BRANDS, exist_ok=True)
for f in (PROJECT / "brands").glob("*.yaml"):
    shutil.copy(f, _BRANDS)
os.environ["AKSYNXA_DATA_DIR"] = _DATA
os.environ["BRANDS_DIR"] = _BRANDS
os.environ["ADMIN_PASSWORD"] = "testpass123"
os.environ["SCHEDULER_TICK_SECONDS"] = "3600"
os.environ.pop("APP_ENV", None)
os.environ.pop("DATABASE_URL", None)
os.environ.pop("WHATSAPP_APP_SECRET", None)
os.environ.pop("MCP_API_KEY", None)

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import webhooks as webhooks_mod  # noqa: E402
from app.api_keys import create_api_key  # noqa: E402

sys.path.insert(0, str(PROJECT / "sdk"))
from aksynxa_client import AKSynxaAPIError, AKSynxaClient  # noqa: E402


def _load_main(name: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT / "app" / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


main = _load_main("app_main_int")


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(scope="module")
def keys():
    _, dental = create_api_key(main.DATA, "demo-dental", "test-dental")
    _, aks = create_api_key(main.DATA, "aksynxa", "test-aks")
    return {"demo-dental": dental, "aksynxa": aks}


def _h(key):
    return {"X-API-Key": key}


DENTAL_CUST = "919111222333"
AKS_CUST = "919444555666"
BOOKER = "919777888999"


@pytest.fixture(scope="module")
def seeded(client):
    client.post("/simulate", json={"from": DENTAL_CUST, "text": "hi",
                                   "brand": "demo-dental"})
    for text in ("hi", "3", "Int Patient", "1", "kal 5 baje", "yes"):
        client.post("/simulate", json={"from": BOOKER, "text": text,
                                       "brand": "demo-dental"})
    client.post("/simulate", json={"from": AKS_CUST, "text": "hi",
                                   "brand": "aksynxa"})
    return True


# ===========================================================================
# REST API — key auth
# ===========================================================================
def test_api_key_auth(client, keys, seeded):
    # Bina key -> 401
    assert client.get("/api/v1/stats").status_code == 401
    # Galat key -> 401
    assert client.get("/api/v1/stats",
                      headers=_h("aks_galat-key")).status_code == 401
    # Sahi key -> 200, aur brand key wala hi
    r = client.get("/api/v1/stats", headers=_h(keys["demo-dental"]))
    assert r.status_code == 200
    assert r.json()["brand"] == "demo-dental"
    # Bearer style bhi chalta hai
    r = client.get("/api/v1/stats",
                   headers={"Authorization": f"Bearer {keys['aksynxa']}"})
    assert r.status_code == 200 and r.json()["brand"] == "aksynxa"
    # Revoked key -> 401
    row, plain = create_api_key(main.DATA, "demo-dental", "revoke-me")
    assert client.get("/api/v1/stats", headers=_h(plain)).status_code == 200
    main.DATA.revoke_api_key(row["id"])
    assert client.get("/api/v1/stats", headers=_h(plain)).status_code == 401


def test_api_tenant_isolation(client, keys, seeded):
    dental = client.get("/api/v1/leads", params={"limit": 50},
                        headers=_h(keys["demo-dental"])).json()
    numbers = {l["customer_number"] for l in dental["leads"]}
    assert DENTAL_CUST in numbers and BOOKER in numbers
    assert AKS_CUST not in numbers

    aks = client.get("/api/v1/leads", params={"limit": 50},
                     headers=_h(keys["aksynxa"])).json()
    a_numbers = {l["customer_number"] for l in aks["leads"]}
    assert AKS_CUST in a_numbers and DENTAL_CUST not in a_numbers

    bookings = client.get("/api/v1/bookings",
                          headers=_h(keys["demo-dental"])).json()
    assert any(b["customer_number"] == BOOKER for b in bookings["bookings"])
    aks_bookings = client.get("/api/v1/bookings",
                              headers=_h(keys["aksynxa"])).json()
    assert all(b["customer_number"] != BOOKER
               for b in aks_bookings["bookings"])

    stats = client.get("/api/v1/stats",
                       headers=_h(keys["demo-dental"])).json()
    assert stats["bookings"] >= 1 and stats["total_leads"] >= 2

    sim = client.post("/api/v1/simulate",
                      json={"from": "919000111222", "text": "hi"},
                      headers=_h(keys["aksynxa"]))
    assert sim.status_code == 200
    assert sim.json()["brand_slug"] == "aksynxa"


def test_openapi_docs_describe_v1(client):
    spec = client.get("/openapi.json").json()
    for path in ("/api/v1/stats", "/api/v1/leads", "/api/v1/bookings",
                 "/api/v1/reminders", "/api/v1/simulate"):
        assert path in spec["paths"], path
    tags = spec["paths"]["/api/v1/stats"]["get"]["tags"]
    assert tags == ["Public API v1"]
    assert client.get("/docs").status_code == 200


# ===========================================================================
# Admin endpoints — keys + webhooks (super-admin only)
# ===========================================================================
def test_admin_api_key_and_webhook_endpoints(client):
    anon = TestClient(main.app)
    assert anon.post("/admin/api-keys",
                     json={"brand": "demo-dental"}).status_code == 401
    assert anon.get("/admin/webhooks",
                    params={"brand": "demo-dental"}).status_code == 401

    admin = TestClient(main.app)
    r = admin.post("/admin/login", data={"password": "testpass123"},
                   follow_redirects=False)
    assert r.status_code == 303

    r = admin.post("/admin/api-keys",
                   json={"brand": "demo-dental", "label": "admin-made"})
    assert r.status_code == 200
    plain = r.json()["api_key"]
    assert plain.startswith("aks_")
    # Nayi key turant kaam karti hai
    assert client.get("/api/v1/stats", headers=_h(plain)).status_code == 200
    # List me hash NAHI aata
    lst = admin.get("/admin/api-keys",
                    params={"brand": "demo-dental"}).json()
    assert lst["keys"] and all("key_hash" not in k for k in lst["keys"])
    # Revoke endpoint
    kid = r.json()["id"]
    assert admin.post(f"/admin/api-keys/{kid}/revoke").status_code == 200
    assert client.get("/api/v1/stats", headers=_h(plain)).status_code == 401

    # Webhook subscription admin se
    r = admin.post("/admin/webhooks", json={
        "brand": "demo-dental", "url": "https://example.com/hook",
        "events": ["booking.created"], "secret": "admsecret"})
    assert r.status_code == 200
    subs = admin.get("/admin/webhooks",
                     params={"brand": "demo-dental"}).json()
    assert any(s["url"] == "https://example.com/hook" and
               s["events"] == ["booking.created"]
               for s in subs["subscriptions"])
    # Galat event -> 400
    r = admin.post("/admin/webhooks", json={
        "brand": "demo-dental", "url": "https://example.com/x",
        "events": ["bogus.event"]})
    assert r.status_code == 400


# ===========================================================================
# Outbound webhooks — firing, signature, retry queue
# ===========================================================================
class _Resp:
    def __init__(self, code):
        self.status_code = code


def _make_sub(brand, events, secret="hooksecret", url="https://hooks.test/x"):
    return main.DATA.create_webhook_subscription({
        "id": uuid.uuid4().hex, "brand_slug": brand, "url": url,
        "events": events, "secret": secret, "active": True,
        "created_at": "2026-11-23T00:00:00+00:00"})


def test_webhook_fires_on_booking_with_valid_signature(client, monkeypatch):
    calls = []

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append({"url": url, "data": data, "headers": headers})
        return _Resp(200)

    monkeypatch.setattr(webhooks_mod.requests, "post", fake_post)
    _make_sub("demo-dental", ["booking.created"])

    booker = "919123123123"
    for text in ("hi", "3", "Hook Patient", "1", "kal 5 baje", "yes"):
        client.post("/simulate", json={"from": booker, "text": text,
                                       "brand": "demo-dental"})
    # Sirf booking.created fire hua (hi wala lead.created is sub par
    # nahi). Admin test ki example.com wali subscription bhi fire ho
    # sakti hai — isliye apni URL se filter karo.
    mine = [c for c in calls if c["url"] == "https://hooks.test/x"]
    assert len(mine) == 1
    call = mine[0]
    assert call["headers"]["X-AKSynxa-Event"] == "booking.created"
    expected = webhooks_mod.sign_body("hooksecret", call["data"])
    assert call["headers"]["X-AKSynxa-Signature"] == expected
    payload = json.loads(call["data"])
    assert payload["brand"] == "demo-dental"
    assert payload["data"]["lead"]["customer_number"] == booker
    # Delivery DB me sent mark hai
    deliveries = main.DATA.list_webhook_deliveries(
        brand_slug="demo-dental", status="sent")
    assert any(d["event"] == "booking.created" for d in deliveries)


def test_webhook_failure_retry_queue(client, monkeypatch):
    def boom(url, data=None, headers=None, timeout=None):
        raise webhooks_mod.requests.ConnectionError("receiver down")

    monkeypatch.setattr(webhooks_mod.requests, "post", boom)
    _make_sub("aksynxa", ["lead.created"], url="https://hooks.test/down")

    cust = "919321321321"
    client.post("/simulate", json={"from": cust, "text": "hi",
                                   "brand": "aksynxa"})
    pending = [d for d in main.DATA.list_webhook_deliveries(
        brand_slug="aksynxa", status="pending")
        if d["event"] == "lead.created"]
    assert pending, "failed delivery retry queue me honi chahiye"
    d = pending[-1]
    assert d["attempts"] == 1 and d["next_retry_at"]
    assert "ConnectionError" in d["last_error"]

    # Abhi due nahi (backoff future me) -> retry pass kuch nahi karega
    stats = main.WEBHOOKS.process_retries()
    assert stats["tried"] == 0

    # Due bana kar retry -> is baar receiver theek hai -> sent
    main.DATA.update_webhook_delivery(
        d["id"], next_retry_at="2020-01-01T00:00:00+00:00")
    monkeypatch.setattr(webhooks_mod.requests, "post",
                        lambda url, data=None, headers=None, timeout=None:
                        _Resp(200))
    stats = main.WEBHOOKS.process_retries()
    assert stats["sent"] == 1
    row = [x for x in main.DATA.list_webhook_deliveries(
        brand_slug="aksynxa", status="sent") if x["id"] == d["id"]]
    assert row and row[0]["attempts"] == 2


# ===========================================================================
# Embed — token gate
# ===========================================================================
def test_embed_token_gate(client, keys, seeded):
    base = "/embed/demo-dental"
    assert client.get(base).status_code == 401
    assert client.get(base, params={"token": "aks_galat"}).status_code == 401
    # Dusre brand ki key is brand ke page par nahi chalegi
    assert client.get(base, params={
        "token": keys["aksynxa"]}).status_code == 401
    r = client.get(base, params={"token": keys["demo-dental"]})
    assert r.status_code == 200
    assert "SmileCare Dental Clinic" in r.text
    assert "Bookings" in r.text and "Pending Reminders" in r.text
    assert client.get("/embed/no-brand",
                      params={"token": keys["demo-dental"]}).status_code == 404


# ===========================================================================
# Setup wizard — pages + draft save
# ===========================================================================
def test_wizard_pages_and_draft_save(client):
    r = client.get("/setup")
    assert r.status_code == 200
    for marker in ("Step 1 of 3", "Step 2 of 3", "Step 3 of 3",
                   "Business ka naam", "WhatsApp number", "draft"):
        assert marker in r.text, marker

    body = {
        "business_name": "Test Salon XYZ",
        "business_type": "salon",
        "owner_name": "Test Owner",
        "services": [{"name": "Haircut", "price": "₹299"},
                     {"name": "", "price": ""}],
        "whatsapp_number": "98110 12345",
        "open_time": "10:00", "close_time": "21:00", "weekly_off": "Monday",
    }
    r = client.post("/setup/draft", json=body)
    assert r.status_code == 200 and r.json()["ok"] is True
    draft_file = Path(_DATA) / "setup_drafts" / "test-salon-xyz.json"
    assert draft_file.exists()
    draft = json.loads(draft_file.read_text(encoding="utf-8"))
    assert draft["status"] == "draft"
    assert draft["business_name"] == "Test Salon XYZ"
    assert draft["services"] == [{"name": "Haircut", "price": "₹299"}]
    assert draft["whatsapp_number"] == "9811012345"

    # Validation: naam na ho / number galat ho -> 400
    assert client.post("/setup/draft", json={
        **body, "business_name": "x"}).status_code == 400
    assert client.post("/setup/draft", json={
        **body, "whatsapp_number": "123"}).status_code == 400


# ===========================================================================
# PWA assets
# ===========================================================================
def test_pwa_assets(client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    manifest = r.json()
    assert manifest["short_name"] == "AKSynxa"
    assert manifest["start_url"] == "/dashboard"
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "aksynxa-pwa" in sw.text
    icon = client.get("/pwa/icon-192.png")
    assert icon.status_code == 200
    assert icon.content[:8] == b"\x89PNG\r\n\x1a\n"
    dash = client.get("/business/login")
    assert "manifest.webmanifest" in dash.text


# ===========================================================================
# Python SDK — live uvicorn server ke against (bilkul asli usage)
# ===========================================================================
@pytest.fixture(scope="module")
def live_server():
    import threading
    import time
    import urllib.request

    import uvicorn

    config = uvicorn.Config(main.app, host="127.0.0.1", port=8899,
                            log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:8899/health", timeout=1) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.1)
    yield "http://127.0.0.1:8899"
    server.should_exit = True
    thread.join(timeout=5)


def test_sdk_client(keys, seeded, live_server, monkeypatch):
    # Is sandbox ke proxy env vars (bracketed IPv6 wali NO_PROXY) se
    # httpx client construction fail hota hai — test ke liye saaf karo.
    # Asli users ke environment me ye dikkat nahi hoti.
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
                "https_proxy", "all_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)
    with AKSynxaClient(live_server, keys["demo-dental"]) as sdk:
        stats = sdk.stats()
        assert stats["brand"] == "demo-dental" and stats["bookings"] >= 1
        leads = sdk.leads(limit=50)
        assert DENTAL_CUST in {l["customer_number"] for l in leads["leads"]}
        bookings = sdk.bookings()
        assert any(b["customer_number"] == BOOKER
                   for b in bookings["bookings"])
        reminders = sdk.reminders()
        assert reminders["brand"] == "demo-dental"
        sim = sdk.simulate("919666777888", "hi")
        assert sim["replies"] and sim["brand_slug"] == "demo-dental"

    bad = AKSynxaClient(live_server, "aks_galat")
    with pytest.raises(AKSynxaAPIError) as exc:
        bad.stats()
    assert exc.value.status_code == 401
    bad.close()
