"""API tests — multi-brand routing, admin login, dashboard, webhook
dedupe + signature. Sab isolated tmp data/brands par chalta hai:
asli data/ aur brands/ files ko haath nahi lagta.
"""
import hashlib
import hmac
import json
import os
import shutil
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

# --- env PEHLE set karo, phir app import (main.py import par state banata hai)
_TMP = tempfile.mkdtemp(prefix="aksynxa-test-")
_DATA = os.path.join(_TMP, "data")
_BRANDS = os.path.join(_TMP, "brands")
os.makedirs(_BRANDS, exist_ok=True)
for f in (PROJECT / "brands").glob("*.yaml"):
    shutil.copy(f, _BRANDS)
os.environ["AKSYNXA_DATA_DIR"] = _DATA
os.environ["BRANDS_DIR"] = _BRANDS
os.environ["ADMIN_PASSWORD"] = "testpass123"
os.environ["SCHEDULER_TICK_SECONDS"] = "3600"  # tests me loop shaant rahe

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as main  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def test_health_lists_all_brands(client):
    d = client.get("/health").json()
    slugs = {b["slug"] for b in d["brands"]}
    assert slugs == {"demo-dental", "aksynxa"}
    assert d["sessions"] == "sqlite (persistent)"


def test_simulate_brand_routing(client):
    r = client.post("/simulate", json={"from": "u-a", "text": "hi", "brand": "aksynxa"})
    d = r.json()
    assert d["brand"] == "AKSynxa" and "AKSynxa" in d["replies"][0]
    r = client.post("/simulate?brand=demo-dental",
                    json={"from": "u-b", "text": "hi"})
    d = r.json()
    assert d["brand_slug"] == "demo-dental"
    assert "SmileCare Dental Clinic" in d["replies"][0]
    # Choices bhi aane chahiye (interactive fallback info)
    assert d["choices"] and len(d["choices"]["options"]) == 5


def test_admin_requires_login(client):
    r = client.get("/admin")
    assert r.status_code == 200 and "Admin Login" in r.text
    assert client.get("/admin/config").status_code == 401
    assert client.get("/dashboard/data").status_code == 401
    # Galat password
    r = client.post("/admin/login", data={"password": "wrong"},
                    follow_redirects=False)
    assert r.status_code == 401


def test_admin_login_switcher_and_save(client):
    r = client.post("/admin/login", data={"password": "testpass123"},
                    follow_redirects=False)
    assert r.status_code == 303
    # Ab admin page (login form nahi) aana chahiye
    r = client.get("/admin")
    assert "Admin Setup" in r.text and "brandSel" in r.text
    # Brand switcher: dono brands ke config alag-alag
    d = client.get("/admin/config", params={"brand": "demo-dental"}).json()
    assert d["name"] == "SmileCare Dental Clinic"
    d2 = client.get("/admin/config", params={"brand": "aksynxa"}).json()
    assert d2["name"] == "AKSynxa"
    # Rename test — sirf dental badlo, aksynxa waisa hi rahe
    d["name"] = "TestCare Dental Clinic"
    out = client.post("/admin/config", json=d).json()
    assert out["ok"] and out["brand_slug"] == "demo-dental"
    r = client.post("/simulate", json={"from": "u-c", "text": "hi",
                                       "brand": "demo-dental"})
    assert "TestCare Dental Clinic" in r.json()["replies"][0]
    # Wapas original naam
    d["name"] = "SmileCare Dental Clinic"
    assert client.post("/admin/config", json=d).json()["ok"]


def test_dashboard_data(client):
    client.post("/simulate", json={"from": "u-dash", "text": "hi", "brand": "aksynxa"})
    d = client.get("/dashboard/data", params={"brand": "aksynxa"}).json()
    assert len(d["cards"]) == 2
    aks = [c for c in d["cards"] if c["slug"] == "aksynxa"][0]
    assert aks["total_leads"] >= 1 and aks["chats_today"] >= 1
    assert d["brand"] == "aksynxa"
    r = client.get("/dashboard")
    assert r.status_code == 200 and "Owner Dashboard" in r.text


def _webhook_payload(msg_id, text="hi", user="919999900555"):
    return {
        "entry": [{
            "changes": [{
                "value": {
                    "metadata": {"phone_number_id": "unknown-id"},
                    "contacts": [{"wa_id": user, "profile": {"name": "Test"}}],
                    "messages": [{
                        "id": msg_id, "from": user, "type": "text",
                        "text": {"body": text},
                    }],
                }
            }]
        }]
    }


def _chatlog_in_rows(slug, user):
    path = Path(_DATA) / f"chatlog_{slug}.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [r for r in rows
            if r["customer_number"] == user and r["direction"] == "in"]


def test_webhook_dedupe(client):
    payload = _webhook_payload("wamid.DEDUPE1")
    assert client.post("/webhook", json=payload).status_code == 200
    assert client.post("/webhook", json=payload).status_code == 200
    # Default brand aksynxa hai (sorted pehli) — in-rows sirf 1 honi chahiye
    assert len(_chatlog_in_rows("aksynxa", "919999900555")) == 1


def test_webhook_signature(monkeypatch, client):
    secret = "<redacted>"
    monkeypatch.setenv("WHATSAPP_APP_SECRET", secret)
    body = json.dumps(_webhook_payload("wamid.SIG1", user="919999900556")).encode()
    # Bina signature -> 403
    r = client.post("/webhook", content=body,
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 403
    # Sahi signature -> 200 aur process hoga
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    r = client.post("/webhook", content=body,
                    headers={"Content-Type": "application/json",
                             "X-Hub-Signature-256": sig})
    assert r.status_code == 200
    assert len(_chatlog_in_rows("aksynxa", "919999900556")) == 1
    monkeypatch.delenv("WHATSAPP_APP_SECRET")
