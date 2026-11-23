"""AKSynxa WhatsApp Automation Base — FastAPI app (NEXT LEVEL).

Ek server, SAARE brands: startup par `brands/` folder ki har YAML
config load hoti hai. WhatsApp webhook par aane wale messages Meta ke
`phone_number_id` se sahi brand par route hote hain.

Endpoints:
  GET  /            Landing page
  GET  /demo        Browser demo chat (?brand=<slug> se brand chunein)
  GET  /brands      Saare brands ki list (slug + naam)
  GET  /brand       Ek brand ki info (?brand=<slug>, default = pehli brand)
  GET  /health      Health + saare brands + modes
  GET  /admin       Setup page — PASSWORD ke peeche (env ADMIN_PASSWORD)
  POST /admin/login / GET /admin/logout
  GET|POST /admin/config   Brand settings read/save (?brand= / body brand)
  GET  /dashboard   Owner dashboard (login ke peeche) + /dashboard/data
  GET|POST /business/login   Business user (owner/staff) login — email+password
  GET  /business/logout
  GET|POST /admin/users      Super-admin: brand ke business users dekho/banao
  GET  /webhook     Meta webhook verification
  POST /webhook     Meta messages -> signature check -> dedupe -> engine
  POST /simulate    Demo mode: {"from","text","brand"} -> bot replies
  GET  /api/v1/*    Public REST API (per-brand API key) — app/api_v1.py
  GET  /embed/{brand}  iframe stats page (token = brand API key)
  GET  /setup       Setup wizard — draft config save (app/wizard.py)
  POST /mcp         MCP server (Bearer MCP_API_KEY) — app/mcp_server.py

V3.1: leads / conversations / reminders / business users DATABASE me
(app/data_layer.py — SQLite local, PostgreSQL jab DATABASE_URL set).
Har brand ek tenant hai: har query brand_slug se filtered, business
user sirf apne brand ka data dekh sakta hai. APP_ENV=production me
ADMIN_PASSWORD ke saath WHATSAPP_APP_SECRET bhi lazmi hai — bina
uske server start hi nahi hoga aur unsigned webhook reject hoga.
"""
import asyncio
import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from app import auth as auth_mod
from app import sender
from app.api_v1 import build_api_router
from app.config_loader import load_all_configs, load_config
from app.data_layer import create_data_layer
from app.engine import Engine
from app.leads import LeadStore
from app.pwa import register_pwa
from app.scheduler import Scheduler
from app.sheets import ChatLogger
from app.store import Store, default_data_dir
from app.postgres_store import PostgresStore
from app.webhooks import WebhookDispatcher
from app.wizard import build_wizard_router

try:
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover
    IST = timezone(timedelta(hours=5, minutes=30))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("aksynxa.main")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
DATA_DIR = default_data_dir()

# ---------------------------------------------------------------------------
# Admin password — env ADMIN_PASSWORD; default sirf local demo ke liye
# ---------------------------------------------------------------------------
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()
APP_ENV = os.environ.get("APP_ENV", "development").lower()
if not ADMIN_PASSWORD and APP_ENV == "production":
    raise RuntimeError("ADMIN_PASSWORD must be set in production; refusing insecure startup")
# V3.1 webhook hardening: production me WhatsApp signature verification
# LAZMI hai — secret hi na ho to server start hi nahi hoga, taaki koi
# unsigned/fake webhook kabhi process na ho.
if APP_ENV == "production" and not os.environ.get("WHATSAPP_APP_SECRET", "").strip():
    raise RuntimeError("WHATSAPP_APP_SECRET must be set in production; "
                       "refusing to run with an unverifiable webhook")
if not ADMIN_PASSWORD:
    ADMIN_PASSWORD = "admin123"
if ADMIN_PASSWORD == "admin123" and not os.environ.get("ADMIN_PASSWORD"):
    log.warning("=" * 64)
    log.warning("ADMIN_PASSWORD set nahi hai — default 'admin123' chal raha hai.")
    log.warning("Ye SIRF local demo ke liye hai. Production me .env me "
                "ADMIN_PASSWORD=<strong password> zaroor rakhein!")
    log.warning("=" * 64)


def _admin_token() -> str:
    return hmac.new(ADMIN_PASSWORD.encode(), b"aksynxa-admin-session-v1",
                    hashlib.sha256).hexdigest()


def is_admin(request: Request) -> bool:
    cookie = request.cookies.get("aksynxa_admin", "")
    return hmac.compare_digest(cookie, _admin_token())


# ---------------------------------------------------------------------------
# Business-user sessions (V3.1) — email+password login, HMAC-signed cookie
# (admin token jaisi hi style, alag message ke saath). Cookie payload:
# "<user_id>:<brand_slug>:<role>" — server har request par DB se user ko
# dobara verify karta hai, isliye role/brand cookie se trust nahi hota.
# ---------------------------------------------------------------------------
def _biz_token(user: dict) -> str:
    payload = f"{user['id']}:{user['brand_slug']}:{user['role']}"
    sig = hmac.new(ADMIN_PASSWORD.encode(),
                   ("aksynxa-biz-session-v1:" + payload).encode(),
                   hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def current_business_user(request: Request):
    """Cookie valid ho AUR user DB me ab bhi waisa hi ho to user dict,
    warna None. Brand/role hamesha DB se aate hain (source of truth)."""
    raw = request.cookies.get("aksynxa_biz", "")
    parts = raw.split(":")
    if len(parts) != 4:
        return None
    uid, brand_slug, role, sig = parts
    payload = f"{uid}:{brand_slug}:{role}"
    expected = hmac.new(ADMIN_PASSWORD.encode(),
                        ("aksynxa-biz-session-v1:" + payload).encode(),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        user = DATA.get_user(int(uid))
    except (ValueError, TypeError):
        return None
    if user is None or user["brand_slug"] != brand_slug or user["role"] != role:
        return None
    return user


def current_user(request: Request):
    """{'kind': 'admin'} ya {'kind': 'business', 'user': {...}} ya None."""
    if is_admin(request):
        return {"kind": "admin"}
    user = current_business_user(request)
    if user is not None:
        return {"kind": "business", "user": user}
    return None


# ---------------------------------------------------------------------------
# Multi-brand state — har brand ka apna runtime (cfg/leads/sheets/engine)
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
STORE = PostgresStore(DATABASE_URL) if DATABASE_URL else Store()
DATA = create_data_layer(DATABASE_URL, DATA_DIR)
SCHEDULER = Scheduler(DATA_DIR, data_layer=DATA)
# V3.2 outbound webhooks — har naya lead data layer ke hook se
# dispatcher tak jata hai (lead.created / booking.created events).
WEBHOOKS = WebhookDispatcher(DATA)
DATA.lead_hook = WEBHOOKS.on_lead
BRANDS: dict = {}
DEFAULT_SLUG = ""


def _make_hooks(cfg):
    """Engine -> scheduler taar: booking pe reminder, enquiry pe follow-up."""
    def on_booking(user_id, data):
        SCHEDULER.schedule_booking_reminder(
            brand_slug=cfg.slug, brand_name=cfg.name, user_id=user_id,
            name=data.get("name", ""), service=data.get("service", ""),
            datetime_text=data.get("datetime", ""),
            hours_before=cfg.reminders_hours_before,
        )

    def on_new_enquiry(user_id):
        if cfg.follow_up_after_hours is not None:
            SCHEDULER.schedule_follow_up(
                brand_slug=cfg.slug, brand_name=cfg.name, user_id=user_id,
                after_hours=cfg.follow_up_after_hours,
                message_template=cfg.render(cfg.follow_up_message),
            )

    return {"on_booking": on_booking, "on_new_enquiry": on_new_enquiry}


def build_brand(cfg) -> dict:
    leads = LeadStore(cfg.slug, cfg.name, data_layer=DATA)
    sheets = ChatLogger(cfg, data_layer=DATA)
    engine = Engine(cfg, leads, sheets, session_store=STORE, hooks=_make_hooks(cfg))
    return {"cfg": cfg, "leads": leads, "sheets": sheets, "engine": engine}


def build_state() -> None:
    global DEFAULT_SLUG
    configs = load_all_configs()
    BRANDS.clear()
    for slug, cfg in configs.items():
        BRANDS[slug] = build_brand(cfg)
        # Tenant registry: har brand ek organization ke roop me DB me
        DATA.ensure_organization(slug, cfg.name)
    # Default brand: BRAND_CONFIG env jis file ko point kare wahi,
    # warna pehli (sorted) brand — purana behaviour backward compatible.
    DEFAULT_SLUG = sorted(BRANDS)[0]
    env_cfg = os.environ.get("BRAND_CONFIG", "").strip()
    if env_cfg:
        try:
            DEFAULT_SLUG = load_config(env_cfg).slug
        except Exception as exc:
            log.warning("BRAND_CONFIG default brand nahi ban paya: %s", exc)
    log.info("Brands ready: %s | default: %s",
             ", ".join(f"{s} ({BRANDS[s]['cfg'].name})" for s in BRANDS), DEFAULT_SLUG)
    log.info("Sender mode: %s", "live" if sender.is_live_mode() else "demo")


def brand_runtime(slug: str | None) -> dict:
    if slug and slug in BRANDS:
        return BRANDS[slug]
    return BRANDS[DEFAULT_SLUG]


def route_brand_by_phone_id(phone_number_id: str) -> str:
    """Webhook routing: Meta ka phone_number_id -> brand slug."""
    for slug, rt in BRANDS.items():
        if rt["cfg"].whatsapp_phone_number_id and \
                rt["cfg"].whatsapp_phone_number_id == str(phone_number_id):
            return slug
    if phone_number_id:
        log.warning("Unknown phone_number_id %s — default brand %s par route kiya",
                    phone_number_id, DEFAULT_SLUG)
    return DEFAULT_SLUG


build_state()
app = FastAPI(title="AKSynxa WhatsApp Automation Base")


def _page(filename: str) -> str:
    with open(os.path.join(TEMPLATES_DIR, filename), "r", encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Core message processing (shared by /webhook and /simulate)
# ---------------------------------------------------------------------------
def process_message(user_id: str, text: str, brand_slug: str | None = None,
                    profile_name: str = "") -> dict:
    rt = brand_runtime(brand_slug)
    cfg, engine, sheets = rt["cfg"], rt["engine"], rt["sheets"]

    replies, alerts = engine.handle(user_id, text)
    stage = engine.session_stage(user_id)
    name = engine.session_name(user_id) or profile_name
    choices = engine.current_choices(user_id)

    sheets.log_turn(user_id, name, "in", text, stage)
    for i, reply in enumerate(replies):
        is_last = i == len(replies) - 1
        if is_last and choices:
            # Aakhri reply ke saath asli WhatsApp buttons/list (live mode);
            # demo mode me sender khud text fallback par aa jata hai.
            sender.send_interactive(user_id, reply, choices, cfg.whatsapp_phone_number_id)
        else:
            sender.send_message(user_id, reply, cfg.whatsapp_phone_number_id)
        sheets.log_turn(user_id, name, "out", reply, stage)
    for alert in alerts:
        if alert.get("to"):
            sender.send_message(alert["to"], alert["text"], cfg.whatsapp_phone_number_id)

    return {
        "brand": cfg.name,
        "brand_slug": cfg.slug,
        "from": user_id,
        "input": text,
        "replies": replies,
        "choices": choices,
        "alerts": alerts,
        "stage": stage,
        "paused": stage == "human",
        "chat_logged": True,
    }


# ---------------------------------------------------------------------------
# Scheduler — reminders, follow-ups, sheets retry (background loop)
# ---------------------------------------------------------------------------
def _send_scheduled(item: dict) -> None:
    rt = BRANDS.get(item["brand_slug"])
    if rt is None:
        raise RuntimeError(f"brand {item['brand_slug']} nahi mili")
    cfg, engine, sheets = rt["cfg"], rt["engine"], rt["sheets"]
    name = item.get("name") or engine.session_name(item["user_id"])
    message = item["message"]
    if name:
        message = message.replace("{name}", name)
    else:
        # Naam pata na ho to "{name} ji" jaisi jagah saaf hata do
        message = message.replace("{name} ji", "").replace("{name}", "")
        message = " ".join(message.split())
    sender.send_message(item["user_id"], message, cfg.whatsapp_phone_number_id)
    stage = "reminder" if item["type"] == "reminder" else "follow_up"
    sheets.log_turn(item["user_id"], name, "out", message, stage)
    log.info("[SCHEDULER] %s bheja -> %s (%s)", item["type"], item["user_id"], cfg.slug)
    if item["type"] == "reminder":
        # V3.2 outbound webhook — reminder gaya, subscribers ko khabar
        try:
            WEBHOOKS.fire(item["brand_slug"], "reminder.due", {
                "reminder": {k: item.get(k) for k in
                             ("id", "type", "user_id", "name", "service",
                              "appointment", "datetime_text", "message")
                             if item.get(k) is not None},
            })
        except Exception as exc:  # webhook kabhi scheduler ko na roke
            log.warning("reminder.due webhook failed: %s", exc)


def _has_booking(brand_slug: str, user_id: str, since_iso: str) -> bool:
    rt = BRANDS.get(brand_slug)
    return bool(rt and rt["leads"].has_booking_after(user_id, since_iso))


async def _scheduler_loop() -> None:
    tick = float(os.environ.get("SCHEDULER_TICK_SECONDS", "30"))
    retry_every = float(os.environ.get("SHEETS_RETRY_SECONDS", "600"))
    since_retry = retry_every  # pehle hi tick par ek retry pass ho jaye
    while True:
        try:
            stats = SCHEDULER.run_due(_send_scheduled, _has_booking)
            if stats["sent"] or stats["cancelled"]:
                log.info("[SCHEDULER] %s", stats)
            since_retry += tick
            if since_retry >= retry_every:
                since_retry = 0
                for rt in BRANDS.values():
                    rt["sheets"].process_retry_queue()
                try:
                    WEBHOOKS.process_retries()
                except Exception as exc:
                    log.warning("webhook retry pass error: %s", exc)
        except Exception as exc:  # loop kabhi marna nahi chahiye
            log.warning("scheduler loop error: %s", exc)
        await asyncio.sleep(tick)


@app.on_event("startup")
async def _start_scheduler():
    asyncio.create_task(_scheduler_loop())
    log.info("Scheduler background loop shuru (tick=%ss)",
             os.environ.get("SCHEDULER_TICK_SECONDS", "30"))


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def landing():
    return _page("landing.html")


@app.get("/demo", response_class=HTMLResponse)
def demo_page():
    return _page("demo.html")


@app.get("/brands")
def brands_list():
    return {
        "default": DEFAULT_SLUG,
        "brands": [
            {"slug": slug, "name": rt["cfg"].name, "tagline": rt["cfg"].tagline}
            for slug, rt in sorted(BRANDS.items())
        ],
    }


@app.get("/brand")
def brand_info(brand: str | None = None):
    cfg = brand_runtime(brand)["cfg"]
    return {"name": cfg.name, "tagline": cfg.tagline, "slug": cfg.slug}


# ---------------------------------------------------------------------------
# Admin — password login + brand switcher ke saath config read/save
# ---------------------------------------------------------------------------
@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    if not is_admin(request):
        return _page("login.html")
    return _page("admin.html")


@app.post("/admin/login")
async def admin_login(request: Request):
    form = await request.form()
    password = str(form.get("password", ""))
    if hmac.compare_digest(password, ADMIN_PASSWORD):
        resp = RedirectResponse("/admin", status_code=303)
        resp.set_cookie("aksynxa_admin", _admin_token(),
                        httponly=True, samesite="lax", max_age=7 * 24 * 3600)
        return resp
    return HTMLResponse(_page("login.html").replace(
        "<!--ERROR-->", "<p class='err'>❌ Galat password. Dobara try karein.</p>"),
        status_code=401)


@app.get("/admin/logout")
def admin_logout():
    resp = RedirectResponse("/admin", status_code=303)
    resp.delete_cookie("aksynxa_admin")
    return resp


# ---------------------------------------------------------------------------
# Business-user login (V3.1) — brand ke owner/staff ke liye
# ---------------------------------------------------------------------------
@app.get("/business/login", response_class=HTMLResponse)
def business_login_page():
    return _page("business_login.html")


@app.post("/business/login")
async def business_login(request: Request):
    form = await request.form()
    email = str(form.get("email", ""))
    password = str(form.get("password", ""))
    user = auth_mod.authenticate(DATA, email, password)
    if user is not None:
        resp = RedirectResponse("/dashboard", status_code=303)
        resp.set_cookie("aksynxa_biz", _biz_token(user),
                        httponly=True, samesite="lax", max_age=7 * 24 * 3600)
        return resp
    return HTMLResponse(_page("business_login.html").replace(
        "<!--ERROR-->", "<p class='err'>❌ Galat email ya password. Dobara try karein.</p>"),
        status_code=401)


@app.get("/business/logout")
def business_logout():
    resp = RedirectResponse("/business/login", status_code=303)
    resp.delete_cookie("aksynxa_biz")
    return resp


# ---------------------------------------------------------------------------
# Super-admin: business users manage karo (sirf ADMIN_PASSWORD login)
# Pehla owner scripts/create_user.py se bhi ban sakta hai.
# ---------------------------------------------------------------------------
@app.get("/admin/users")
def admin_list_users(request: Request, brand: str | None = None):
    if not is_admin(request):
        return JSONResponse({"error": "login required"}, status_code=401)
    rt = brand_runtime(brand)
    users = [{k: u[k] for k in ("id", "brand_slug", "email", "role", "created_at")}
             for u in DATA.list_users(rt["cfg"].slug)]
    return {"brand": rt["cfg"].slug, "users": users}


class AdminUserIn(BaseModel):
    brand: str = ""
    email: str
    password: str
    role: str = "staff"


@app.post("/admin/users")
def admin_create_user(request: Request, payload: AdminUserIn):
    if not is_admin(request):
        return JSONResponse({"error": "login required"}, status_code=401)
    rt = brand_runtime(payload.brand or None)
    if payload.role not in ("owner", "staff"):
        return JSONResponse({"error": "role 'owner' ya 'staff' hona chahiye"},
                            status_code=400)
    if len(payload.password) < 8:
        return JSONResponse({"error": "password kam se kam 8 characters ka ho"},
                            status_code=400)
    try:
        user = auth_mod.create_business_user(
            DATA, rt["cfg"].slug, payload.email, payload.password, payload.role)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"ok": True, "user": {k: user[k] for k in
                                 ("id", "brand_slug", "email", "role", "created_at")}}


def _editable_config(rt: dict) -> dict:
    cfg = rt["cfg"]
    return {
        "slug": cfg.slug,
        "name": cfg.name,
        "tagline": cfg.tagline,
        "welcome": cfg.welcome,
        "fallback": cfg.fallback,
        "human_handoff": cfg.human_handoff,
        "owner_alert_number": cfg.owner_alert_number,
        "phone": cfg.phone,
        "address": cfg.address,
        "hours": cfg.hours,
        "sheet_webhook_url": cfg.google_sheet_webhook_url,
        "whatsapp_phone_number_id": cfg.whatsapp_phone_number_id,
        "reminders_hours_before": cfg.reminders_hours_before,
        "follow_up_after_hours": cfg.follow_up_after_hours,
        "follow_up_message": cfg.follow_up_message,
        "menu": [{"label": m["label"], "action": m.get("action", "")} for m in cfg.menu],
        "services": [
            {"name": s["name"], "price_label": s.get("price_label", ""),
             "description": s.get("description", "")}
            for s in cfg.services
        ],
        "faqs": [
            {"question": f["question"], "keywords": ", ".join(f.get("keywords", [])),
             "answer": f["answer"]}
            for f in cfg.faqs
        ],
    }


@app.get("/admin/config")
def admin_get_config(request: Request, brand: str | None = None):
    if not is_admin(request):
        return JSONResponse({"error": "login required"}, status_code=401)
    return _editable_config(brand_runtime(brand))


class AdminConfigIn(BaseModel):
    brand: str = ""
    slug: str = ""  # GET /admin/config "slug" deta hai — round-trip ke liye
    name: str
    tagline: str = ""
    welcome: str
    fallback: str
    human_handoff: str
    owner_alert_number: str = ""
    phone: str = ""
    address: str = ""
    hours: str = ""
    sheet_webhook_url: str = ""
    whatsapp_phone_number_id: str = ""
    reminders_hours_before: float = 1
    follow_up_after_hours: float | None = None
    follow_up_message: str = ""
    menu: list = []
    services: list = []
    faqs: list = []


@app.post("/admin/config")
def admin_save_config(request: Request, payload: AdminConfigIn):
    if not is_admin(request):
        return JSONResponse({"error": "login required"}, status_code=401)
    rt = brand_runtime(payload.brand or payload.slug or None)
    cfg = rt["cfg"]
    with open(cfg.source_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    raw["brand"]["name"] = payload.name.strip()
    raw["brand"]["tagline"] = payload.tagline.strip()
    raw["messages"]["welcome"] = payload.welcome
    raw["messages"]["fallback"] = payload.fallback
    raw["messages"]["human_handoff"] = payload.human_handoff
    raw["owner_alert_number"] = payload.owner_alert_number.strip()
    raw.setdefault("contact", {})
    raw["contact"]["phone"] = payload.phone.strip()
    raw["contact"]["address"] = payload.address.strip()
    raw["contact"]["hours"] = payload.hours.strip()
    if payload.menu and len(payload.menu) == len(raw.get("menu", [])):
        for saved, new in zip(raw["menu"], payload.menu):
            if new.get("label"):
                saved["label"] = new["label"].strip()
    raw["services"] = [
        {"name": s.get("name", "").strip(),
         "price_label": s.get("price_label", "").strip(),
         "description": s.get("description", "").strip()}
        for s in payload.services if s.get("name")
    ]
    raw["faqs"] = [
        {"question": f.get("question", "").strip(),
         "keywords": [k.strip() for k in (f.get("keywords", "") or "").split(",") if k.strip()],
         "answer": f.get("answer", "").strip()}
        for f in payload.faqs if f.get("question")
    ]
    raw.setdefault("google_sheet", {})
    raw["google_sheet"]["webhook_url"] = payload.sheet_webhook_url.strip()
    raw["whatsapp_phone_number_id"] = payload.whatsapp_phone_number_id.strip()
    raw.setdefault("reminders", {})
    raw["reminders"]["hours_before"] = payload.reminders_hours_before
    raw.setdefault("follow_up", {})
    if payload.follow_up_after_hours is None:
        raw["follow_up"].pop("after_hours", None)
    else:
        raw["follow_up"]["after_hours"] = payload.follow_up_after_hours
    if payload.follow_up_message.strip():
        raw["follow_up"]["message"] = payload.follow_up_message

    with open(cfg.source_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(raw, fh, allow_unicode=True, sort_keys=False, width=1000)

    # Sirf usi brand ka runtime rebuild — baaki brands chalte rahenge,
    # sessions SQLite me hain isliye kisi ki chat reset nahi hoti.
    BRANDS[cfg.slug] = build_brand(load_config(cfg.source_path))
    return {"ok": True,
            "message": "Saved! Bot ab nayi settings ke saath chal raha hai.",
            "brand": BRANDS[cfg.slug]["cfg"].name, "brand_slug": cfg.slug}


# ---------------------------------------------------------------------------
# Owner dashboard — per-brand summary + last 10 leads.
# V3.1 scoping: super-admin (ADMIN_PASSWORD) saare brands dekh sakta
# hai; business user (owner/staff) SIRF apne brand ka card + leads —
# dusre brand ka `?brand=` bhejne par bhi usi ke brand ka data milega.
# ---------------------------------------------------------------------------
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request):
    if current_user(request) is None:
        return RedirectResponse("/admin", status_code=303)
    return _page("dashboard.html")


@app.get("/dashboard/data")
def dashboard_data(request: Request, brand: str | None = None):
    cu = current_user(request)
    if cu is None:
        return JSONResponse({"error": "login required"}, status_code=401)
    today = datetime.now(IST).strftime("%Y-%m-%d")

    if cu["kind"] == "business":
        allowed = [cu["user"]["brand_slug"]]
        effective_brand = cu["user"]["brand_slug"]
    else:  # super-admin
        allowed = sorted(BRANDS)
        effective_brand = brand_runtime(brand)["cfg"].slug

    cards = []
    for slug in allowed:
        rt = BRANDS[slug]
        rows = rt["sheets"].all_rows()
        leads = rt["leads"].all_leads()
        cards.append({
            "slug": slug,
            "name": rt["cfg"].name,
            "chats_today": sum(1 for r in rows
                               if r.get("direction") == "in"
                               and str(r.get("timestamp", "")).startswith(today)),
            "total_leads": len(leads),
            "bookings": sum(1 for l in leads if l.get("stage") == "booking_confirmed"),
            "human_handoffs": sum(1 for l in leads if l.get("stage") == "human_requested"),
        })
    rt = BRANDS[effective_brand]
    last_leads = list(reversed(rt["leads"].all_leads()))[:10]
    if cu["kind"] == "business":
        pending = len(SCHEDULER.data_layer.list_reminders(
            brand_slug=effective_brand, status="pending")) \
            if SCHEDULER.data_layer is not None else len(SCHEDULER.pending())
    else:
        pending = len(SCHEDULER.pending())
    return {"cards": cards, "brand": rt["cfg"].slug,
            "brand_name": rt["cfg"].name, "last_leads": last_leads,
            "pending_schedules": pending,
            "viewer": cu["kind"]}


class BrandStaffIn(BaseModel):
    email: str
    password: str


@app.post("/dashboard/users")
def dashboard_create_staff(request: Request, payload: BrandStaffIn):
    """Brand ka OWNER apne brand ke liye staff user banaye.
    Role enforcement: staff khud staff nahi bana sakta (403), aur
    brand hamesha login wale user ke brand se aata hai — payload se
    brand badalna mumkin nahi (tenant isolation)."""
    cu = current_user(request)
    if cu is None:
        return JSONResponse({"error": "login required"}, status_code=401)
    if cu["kind"] == "business" and cu["user"]["role"] != "owner":
        return JSONResponse({"error": "sirf brand owner staff bana sakta hai"},
                            status_code=403)
    if cu["kind"] == "business":
        brand_slug = cu["user"]["brand_slug"]
    else:  # super-admin ko brand explicitly dena hoga — is endpoint par staff
        return JSONResponse({"error": "super-admin /admin/users use kare"},
                            status_code=400)
    if len(payload.password) < 8:
        return JSONResponse({"error": "password kam se kam 8 characters ka ho"},
                            status_code=400)
    try:
        user = auth_mod.create_business_user(
            DATA, brand_slug, payload.email, payload.password, "staff")
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"ok": True, "user": {k: user[k] for k in
                                 ("id", "brand_slug", "email", "role", "created_at")}}


# ---------------------------------------------------------------------------
# Health / simulate / Meta webhook
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "brands": [
            {"slug": slug, "name": rt["cfg"].name,
             "phone_number_id": rt["cfg"].whatsapp_phone_number_id or "(placeholder)",
             "sheets": rt["sheets"].status()}
            for slug, rt in sorted(BRANDS.items())
        ],
        "default_brand": DEFAULT_SLUG,
        "sender_mode": "live" if sender.is_live_mode() else "demo",
        "sessions": "postgres (persistent)" if DATABASE_URL else "sqlite (persistent)",
        "data_layer": DATA.dialect,
        "app_env": APP_ENV,
        "organizations": len(DATA.list_organizations()),
        "scheduler_pending": len(SCHEDULER.pending()),
    }


class SimulateIn(BaseModel):
    from_: str = ""
    text: str

    class Config:
        populate_by_name = True


@app.post("/simulate")
async def simulate(request: Request):
    body = await request.json()
    user_id = str(body.get("from") or body.get("from_") or "demo-user")
    text = str(body.get("text") or "")
    brand = body.get("brand") or request.query_params.get("brand")
    return process_message(user_id, text, brand_slug=brand)


@app.get("/webhook")
def webhook_verify(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge", "")
    expected = os.environ.get("VERIFY_TOKEN", "")
    if not expected and BRANDS:
        expected = BRANDS[DEFAULT_SLUG]["cfg"].verify_token_fallback
    if mode == "subscribe" and expected and token == expected:
        return PlainTextResponse(challenge)
    return PlainTextResponse("Verification failed", status_code=403)


def _verify_signature(request: Request, raw_body: bytes) -> bool:
    """WHATSAPP_APP_SECRET set ho to X-Hub-Signature-256 lazmi check.
    Dev/demo me secret unset ho to skip — lekin production me ye
    branch pahunch hi nahi sakti: APP_ENV=production par startup guard
    WHATSAPP_APP_SECRET ke bina server start hi nahi hone deta, isliye
    production me unsigned/invalid webhook hamesha 403 hoga."""
    secret = os.environ.get("WHATSAPP_APP_SECRET", "").strip()
    if not secret:
        log.warning("WHATSAPP_APP_SECRET set nahi — webhook signature check SKIP "
                    "(sirf demo ke liye theek hai)")
        return True
    header = request.headers.get("x-hub-signature-256", "")
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header, expected)


def _extract_text(msg: dict) -> str:
    """Text message ya interactive (button/list) reply se engine ke
    samajhne layak text nikalo — button ids hamare menu numbers/'yes'/
    'no' hi hote hain, isliye seedha engine me ja sakte hain."""
    if msg.get("type") == "text":
        return msg.get("text", {}).get("body", "")
    if msg.get("type") == "interactive":
        inter = msg.get("interactive", {})
        reply = inter.get("button_reply") or inter.get("list_reply") or {}
        return reply.get("id") or reply.get("title") or ""
    return ""


@app.post("/webhook")
async def webhook_receive(request: Request):
    raw_body = await request.body()
    if not _verify_signature(request, raw_body):
        log.warning("Webhook signature GALAT — request reject")
        return JSONResponse({"status": "bad signature"}, status_code=403)
    try:
        import json as _json

        body = _json.loads(raw_body or b"{}")
    except Exception:
        return JSONResponse({"status": "ignored"})
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            metadata = value.get("metadata", {})
            brand_slug = route_brand_by_phone_id(metadata.get("phone_number_id", ""))
            contacts = {c.get("wa_id"): c.get("profile", {}).get("name", "")
                        for c in value.get("contacts", [])}
            for msg in value.get("messages", []):
                msg_id = msg.get("id", "")
                # Dedupe: Meta retries par ek hi message dobara aata hai —
                # reply sirf pehli baar jayega.
                if msg_id and STORE.is_processed(msg_id):
                    log.info("Duplicate webhook message skip: %s", msg_id)
                    continue
                if msg_id:
                    STORE.mark_processed(msg_id, brand_slug)
                user_id = msg.get("from", "")
                process_message(user_id, _extract_text(msg),
                                brand_slug=brand_slug,
                                profile_name=contacts.get(user_id, ""))
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# V3.2 integrations — Public REST API v1 (/api/v1/*, per-brand API
# key), admin api-key/webhook endpoints, embed page (/embed/{brand}),
# setup wizard (/setup, draft save), PWA (manifest + service worker)
# aur website chat widget ke liye CORS. Detail: INTEGRATIONS.md
# ---------------------------------------------------------------------------
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("FRONTEND_ORIGIN", "").strip() or "*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)
app.include_router(build_api_router({
    "data": lambda: DATA,
    "brands": lambda: BRANDS,
    "process_message": process_message,
    "is_admin": is_admin,
}))
app.include_router(build_wizard_router(DATA_DIR))
register_pwa(app)


# ---------------------------------------------------------------------------
# MCP server (V3.2) — /mcp par mount: koi bhi MCP client (ChatGPT
# Developer Mode, Claude, ...) Bearer token ke saath connect karke
# brand-scoped tools use kar sakta hai (list_businesses,
# get_dashboard_stats, list_leads, list_bookings, list_reminders,
# get_chat_history, simulate_message). Tools/data/auth ki poori
# detail app/mcp_server.py me hai. MCP_API_KEY production me na ho
# to /mcp mount hi nahi hota — baaki app bilkul pehle jaisa chalta
# hai. Detail: MCP_NOTES.md
# ---------------------------------------------------------------------------
from app.mcp_server import mount_mcp  # noqa: E402

mount_mcp(app, {
    "data": lambda: DATA,
    "brands": lambda: BRANDS,
    "process_message": process_message,
    "app_env": APP_ENV,
})
