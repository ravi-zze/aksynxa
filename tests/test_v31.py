"""V3.1 tests — data layer, tenant isolation, business auth, roles,
DB-backed scheduler, production webhook hardening.

Sab isolated tmp folders me chalta hai. `app.main` ko fresh module ke
roop me load kiya jata hai (test_api.py wale instance se alag), taaki
env + data dir is file ke apne hon.
"""
import hashlib
import hmac
import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

# --- env PEHLE set karo, phir main ko fresh load karo -------------------
_TMP = tempfile.mkdtemp(prefix="aksynxa-v31-test-")
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

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth as auth_mod  # noqa: E402
from app.data_layer import DataLayer  # noqa: E402
from app.scheduler import Scheduler  # noqa: E402


def _load_main(name: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT / "app" / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


main = _load_main("app_main_v31")


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def _persona():
    c = TestClient(main.app)
    return c


# ===========================================================================
# Data layer — tenant isolation (unit level)
# ===========================================================================
def test_data_layer_tenant_isolation(tmp_path):
    dl = DataLayer(db_path=str(tmp_path / "t.db"))
    dl.ensure_organization("brand-a", "Brand A")
    dl.ensure_organization("brand-b", "Brand B")
    assert {o["brand_slug"] for o in dl.list_organizations()} == {"brand-a", "brand-b"}

    lead = {"timestamp": "2026-11-22T10:00:00+00:00", "brand": "Brand A",
            "customer_number": "911", "name": "Asha", "interest": "Cleaning",
            "stage": "booking_confirmed", "summary": "Booking: Cleaning"}
    dl.add_lead("brand-a", lead)
    dl.add_lead("brand-b", {**lead, "brand": "Brand B", "customer_number": "922"})

    a_leads = dl.list_leads("brand-a")
    b_leads = dl.list_leads("brand-b")
    assert len(a_leads) == 1 and a_leads[0]["customer_number"] == "911"
    assert len(b_leads) == 1 and b_leads[0]["customer_number"] == "922"
    # stage / customer filters bhi tenant ke andar hi rehte hain
    assert dl.list_leads("brand-a", customer_number="922") == []
    assert len(dl.list_leads("brand-b", stage="booking_confirmed")) == 1

    row = {"timestamp": "2026-11-22 10:00:00 IST", "brand": "Brand A",
           "customer_number": "911", "name": "Asha", "direction": "in",
           "message": "hi", "stage": "menu"}
    dl.add_chat_turn("brand-a", row)
    assert len(dl.list_chat_turns("brand-a")) == 1
    assert dl.list_chat_turns("brand-b") == []

    # Users bhi tenant-scoped hain; email global unique hai
    dl.create_user("brand-a", "owner@a.example", "hash-a", "owner")
    dl.create_user("brand-b", "owner@b.example", "hash-b", "owner")
    assert [u["email"] for u in dl.list_users("brand-a")] == ["owner@a.example"]
    assert dl.count_users() == 2 and dl.count_users("brand-b") == 1
    with pytest.raises(ValueError):
        dl.create_user("brand-b", "owner@a.example", "hash-x", "staff")


def test_password_hashing_roundtrip():
    h = auth_mod.hash_password("Secret123!")
    assert h.startswith("pbkdf2_sha256$") and "Secret123!" not in h
    assert auth_mod.verify_password("Secret123!", h)
    assert not auth_mod.verify_password("galat-password", h)
    assert not auth_mod.verify_password("Secret123!", "junk-format")
    # Salt random hai — same password, alag hash
    assert auth_mod.hash_password("Secret123!") != h


def test_scheduler_db_backed(tmp_path):
    dl = DataLayer(db_path=str(tmp_path / "s.db"))
    sched = Scheduler(str(tmp_path), data_layer=dl)
    item = sched.schedule_follow_up(
        brand_slug="demo-dental", brand_name="SmileCare",
        user_id="919", after_hours=0, message_template="follow up!")
    # File nahi bani — DB me gaya
    assert not (tmp_path / "reminders.json").exists()
    assert [i["id"] for i in dl.list_reminders(status="pending")] == [item["id"]]
    assert dl.list_reminders(brand_slug="aksynxa") == []

    # "Restart": naya Scheduler, wahi DB — pending waisa hi milta hai
    sched2 = Scheduler(str(tmp_path), data_layer=dl)
    assert len(sched2.pending()) == 1
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    sent = []
    stats = sched2.run_due(lambda it: sent.append(it),
                           has_booking=lambda b, u, s: False,
                           now=datetime.now(ZoneInfo("Asia/Kolkata")) + timedelta(minutes=1))
    assert stats["sent"] == 1 and sent[0]["user_id"] == "919"
    assert sched2.pending() == []
    assert dl.list_reminders(status="sent")[0]["id"] == item["id"]


# ===========================================================================
# API level — business auth + tenant isolation + roles
# ===========================================================================
DENTAL_CUST = "919111111111"
AKS_CUST = "919222222222"


@pytest.fixture(scope="module")
def seeded(client):
    # Dono brands par ek-ek customer activity
    client.post("/simulate", json={"from": DENTAL_CUST, "text": "hi",
                                   "brand": "demo-dental"})
    client.post("/simulate", json={"from": AKS_CUST, "text": "hi",
                                   "brand": "aksynxa"})
    # Business users: dental owner, dental staff, aksynxa owner
    auth_mod.create_business_user(main.DATA, "demo-dental",
                                  "owner@dental.example", "DentalPass1", "owner")
    auth_mod.create_business_user(main.DATA, "demo-dental",
                                  "staff@dental.example", "StaffPass1", "staff")
    auth_mod.create_business_user(main.DATA, "aksynxa",
                                  "owner@aksynxa.example", "AksPass123", "owner")
    return True


def _biz_login(email, password):
    c = _persona()
    r = c.post("/business/login", data={"email": email, "password": password},
               follow_redirects=False)
    return c, r


def test_business_login_success_and_failure(seeded):
    c, r = _biz_login("owner@dental.example", "DentalPass1")
    assert r.status_code == 303
    assert c.get("/dashboard").status_code == 200
    # Galat password -> 401, dashboard band
    c2, r2 = _biz_login("owner@dental.example", "WrongPass999")
    assert r2.status_code == 401
    assert c2.get("/dashboard/data").status_code == 401
    # Unknown email -> 401
    _, r3 = _biz_login("nobody@example.com", "whatever123")
    assert r3.status_code == 401


def test_business_user_sees_only_own_brand(seeded):
    c, _ = _biz_login("owner@dental.example", "DentalPass1")
    d = c.get("/dashboard/data").json()
    assert [card["slug"] for card in d["cards"]] == ["demo-dental"]
    assert d["brand"] == "demo-dental"
    numbers = {l["customer_number"] for l in d["last_leads"]}
    assert DENTAL_CUST in numbers and AKS_CUST not in numbers
    # Dusre brand ka data maangne par bhi apna hi milega (isolation)
    d2 = c.get("/dashboard/data", params={"brand": "aksynxa"}).json()
    assert d2["brand"] == "demo-dental"
    assert AKS_CUST not in {l["customer_number"] for l in d2["last_leads"]}

    # AKSynxa owner ko ulta — sirf aksynxa
    c2, _ = _biz_login("owner@aksynxa.example", "AksPass123")
    d3 = c2.get("/dashboard/data").json()
    assert [card["slug"] for card in d3["cards"]] == ["aksynxa"]
    assert AKS_CUST in {l["customer_number"] for l in d3["last_leads"]}


def test_role_enforcement_owner_vs_staff(seeded):
    # Staff: dashboard dekh sakta hai, lekin staff NAHI bana sakta
    staff, _ = _biz_login("staff@dental.example", "StaffPass1")
    assert staff.get("/dashboard/data").status_code == 200
    r = staff.post("/dashboard/users",
                   json={"email": "new@dental.example", "password": "NewPass123"})
    assert r.status_code == 403
    # Owner bana sakta hai — aur naya user usi ke brand ka banta hai
    owner, _ = _biz_login("owner@dental.example", "DentalPass1")
    r = owner.post("/dashboard/users",
                   json={"email": "new@dental.example", "password": "NewPass123"})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["brand_slug"] == "demo-dental"
    assert body["user"]["role"] == "staff"
    # Naya staff login kar sakta hai
    _, r2 = _biz_login("new@dental.example", "NewPass123")
    assert r2.status_code == 303


def test_super_admin_still_sees_all_and_manages_users(seeded, client):
    r = client.post("/admin/login", data={"password": "testpass123"},
                    follow_redirects=False)
    assert r.status_code == 303
    d = client.get("/dashboard/data").json()
    assert {c["slug"] for c in d["cards"]} == {"demo-dental", "aksynxa"}
    # Admin users list + create (kisi bhi brand ke liye)
    users = client.get("/admin/users", params={"brand": "aksynxa"}).json()
    assert [u["email"] for u in users["users"]] == ["owner@aksynxa.example"]
    assert "password_hash" not in users["users"][0]
    r = client.post("/admin/users", json={
        "brand": "aksynxa", "email": "help@aksynxa.example",
        "password": "HelpPass123", "role": "staff"})
    assert r.status_code == 200 and r.json()["user"]["role"] == "staff"
    # Business user admin endpoints par nahi ja sakta
    biz, _ = _biz_login("owner@dental.example", "DentalPass1")
    assert biz.get("/admin/users").status_code == 401
    assert biz.get("/admin/config").status_code == 401


def test_booking_lands_in_db_and_dashboard(seeded):
    # Dental par full booking (naya customer)
    cust = "919333333333"
    anon = _persona()
    for text in ("hi", "3", "Test Patient", "1", "kal 5 baje", "yes"):
        anon.post("/simulate", json={"from": cust, "text": text,
                                     "brand": "demo-dental"})
    # DB me booking_confirmed lead honi chahiye (source of truth)
    leads = main.DATA.list_leads("demo-dental", stage="booking_confirmed")
    assert any(l["customer_number"] == cust for l in leads)
    # Aur AKSynxa ke DB me is customer ka kuch nahi
    assert main.DATA.list_leads("aksynxa", customer_number=cust) == []
    # Dental owner ke dashboard par dikhegi
    owner, _ = _biz_login("owner@dental.example", "DentalPass1")
    d = owner.get("/dashboard/data").json()
    assert cust in {l["customer_number"] for l in d["last_leads"]}
    card = d["cards"][0]
    assert card["bookings"] >= 1


# ===========================================================================
# Webhook hardening — production vs dev
# ===========================================================================
def _webhook_payload(msg_id, user="919999900555"):
    return {
        "entry": [{
            "changes": [{
                "value": {
                    "metadata": {"phone_number_id": "unknown-id"},
                    "contacts": [{"wa_id": user, "profile": {"name": "Test"}}],
                    "messages": [{
                        "id": msg_id, "from": user, "type": "text",
                        "text": {"body": "hi"},
                    }],
                }
            }]
        }]
    }


def test_dev_webhook_unsigned_still_accepted(client):
    # Dev mode (koi secret nahi) — purana behaviour barkarar
    assert os.environ.get("WHATSAPP_APP_SECRET") in (None, "")
    r = client.post("/webhook", json=_webhook_payload("wamid.DEV31"))
    assert r.status_code == 200


def test_production_webhook_requires_signature():
    secret = "<redacted>"
    old = {k: os.environ.get(k) for k in
           ("APP_ENV", "WHATSAPP_APP_SECRET", "AKSYNXA_DATA_DIR", "BRANDS_DIR")}
    tmp2 = tempfile.mkdtemp(prefix="aksynxa-prod-test-")
    brands2 = os.path.join(tmp2, "brands")
    os.makedirs(brands2, exist_ok=True)
    for f in (PROJECT / "brands").glob("*.yaml"):
        shutil.copy(f, brands2)
    os.environ["APP_ENV"] = "production"
    os.environ["WHATSAPP_APP_SECRET"] = secret
    os.environ["AKSYNXA_DATA_DIR"] = os.path.join(tmp2, "data")
    os.environ["BRANDS_DIR"] = brands2
    try:
        prod = _load_main("app_main_prod")
        with TestClient(prod.app, raise_server_exceptions=False) as c:
            body = json.dumps(_webhook_payload("wamid.PROD1")).encode()
            # Unsigned -> 403 (production me signature lazmi)
            r = c.post("/webhook", content=body,
                       headers={"Content-Type": "application/json"})
            assert r.status_code == 403
            # Galat signature -> 403
            r = c.post("/webhook", content=body,
                       headers={"Content-Type": "application/json",
                                "X-Hub-Signature-256": "sha256=deadbeef"})
            assert r.status_code == 403
            # Sahi signature -> 200
            sig = "sha256=" + hmac.new(secret.encode(), body,
                                       hashlib.sha256).hexdigest()
            r = c.post("/webhook", content=body,
                       headers={"Content-Type": "application/json",
                                "X-Hub-Signature-256": sig})
            assert r.status_code == 200
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_production_startup_refuses_without_secret():
    old = {k: os.environ.get(k) for k in ("APP_ENV", "WHATSAPP_APP_SECRET")}
    os.environ["APP_ENV"] = "production"
    os.environ.pop("WHATSAPP_APP_SECRET", None)
    try:
        with pytest.raises(RuntimeError, match="WHATSAPP_APP_SECRET"):
            _load_main("app_main_prod_nosecret")
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
