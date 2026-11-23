"""AKSynxa V3.2 — Public REST API v1 + admin integration endpoints + embed.

Public API (`/api/v1/*`) — per-brand API key se authenticated:

    Header:  X-API-Key: aks_<...>     (ya Authorization: Bearer aks_<...>)

Key jis brand ki hai, API SIRF usi brand ka data degi — brand request
se choose hi nahi hota, isliye tenant isolation key se hi enforce
hoti hai. Endpoints wahi data dete hain jo MCP tools dete hain
(app/mcp_server.py). Interactive docs: /docs (tag "Public API v1").

Admin endpoints (super-admin login, ADMIN_PASSWORD session):
    POST /admin/api-keys            key banao (plaintext ek hi baar)
    GET  /admin/api-keys?brand=     keys ki list (hash kabhi nahi)
    POST /admin/api-keys/{id}/revoke
    POST /admin/webhooks            subscription banao
    GET  /admin/webhooks?brand=     subscriptions ki list

Embed (`GET /embed/{brand}?token=<api key>`): read-only stats page
jo iframe me daali ja sakti hai. Token = brand ki API key hi hai
(simple + consistent rakha hai; key hashed store hoti hai, yahan
bhi hash se hi verify hoti hai). Key dusre brand ki ho to page
nahi khulega.
"""
import html
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.api_keys import create_api_key, verify_api_key
from app.webhooks import EVENTS


def _clamp_limit(limit, default=20) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, 200))


def brand_stats(data, brands: dict, brand: str) -> dict:
    """Dashboard totals — MCP get_dashboard_stats ke bilkul same fields."""
    leads = data.list_leads(brand)
    turns = data.list_chat_turns(brand)
    pending = data.list_reminders(brand_slug=brand, status="pending")
    return {
        "brand": brand,
        "brand_name": brands[brand]["cfg"].name,
        "total_leads": len(leads),
        "bookings": sum(1 for l in leads
                        if l.get("stage") == "booking_confirmed"),
        "human_handoffs": sum(1 for l in leads
                              if l.get("stage") == "human_requested"),
        "new_enquiries": sum(1 for l in leads
                             if l.get("stage") == "new_enquiry"),
        "total_chat_turns": len(turns),
        "messages_in": sum(1 for t in turns if t.get("direction") == "in"),
        "messages_out": sum(1 for t in turns if t.get("direction") == "out"),
        "pending_reminders": len(pending),
    }


def build_api_router(ctx) -> APIRouter:
    """ctx: {"data": callable, "brands": callable,
             "process_message": callable, "is_admin": callable}"""
    router = APIRouter()

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------
    def require_key(request: Request) -> dict:
        key = request.headers.get("x-api-key", "").strip()
        if not key:
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                key = auth[7:].strip()
        row = verify_api_key(ctx["data"](), key)
        if row is None:
            raise HTTPException(
                status_code=401,
                detail="Valid API key required — X-API-Key header bhejo")
        return row

    def require_admin(request: Request):
        if not ctx["is_admin"](request):
            raise HTTPException(status_code=401, detail="admin login required")

    def brand_or_404(slug: str) -> str:
        brands = ctx["brands"]()
        if slug not in brands:
            raise HTTPException(status_code=404,
                                detail=f"unknown brand: {slug}")
        return slug

    # ------------------------------------------------------------------
    # Public API v1 — key ke brand ka hi data
    # ------------------------------------------------------------------
    @router.get("/api/v1/stats", tags=["Public API v1"],
                summary="Brand ke dashboard totals (leads, bookings, chats, pending reminders)")
    def api_stats(request: Request):
        row = require_key(request)
        return brand_stats(ctx["data"](), ctx["brands"](), row["brand_slug"])

    @router.get("/api/v1/leads", tags=["Public API v1"],
                summary="Brand ke recent leads (naye pehle). Query: ?limit=")
    def api_leads(request: Request, limit: int = 20):
        row = require_key(request)
        leads = ctx["data"]().list_leads(row["brand_slug"])
        recent = list(reversed(leads))[:_clamp_limit(limit)]
        return {"brand": row["brand_slug"], "count": len(recent),
                "leads": recent}

    @router.get("/api/v1/bookings", tags=["Public API v1"],
                summary="Brand ki recent confirmed bookings. Query: ?limit=")
    def api_bookings(request: Request, limit: int = 20):
        row = require_key(request)
        bookings = ctx["data"]().list_leads(
            row["brand_slug"], stage="booking_confirmed")
        recent = list(reversed(bookings))[:_clamp_limit(limit)]
        return {"brand": row["brand_slug"], "count": len(recent),
                "bookings": recent}

    @router.get("/api/v1/reminders", tags=["Public API v1"],
                summary="Brand ke reminders. Query: ?status=pending|sent|cancelled|all")
    def api_reminders(request: Request, status: str = "pending"):
        row = require_key(request)
        status_filter = None if status in ("", "all") else status
        items = ctx["data"]().list_reminders(
            brand_slug=row["brand_slug"], status=status_filter)
        return {"brand": row["brand_slug"], "status": status or "all",
                "count": len(items), "reminders": items[:200]}

    class SimulateIn(BaseModel):
        from_: str = ""
        text: str

        class Config:
            populate_by_name = True

    @router.post("/api/v1/simulate", tags=["Public API v1"],
                 summary="Bot ko test message bhejo (demo — asli WhatsApp message nahi jata)")
    def api_simulate(request: Request, payload: SimulateIn):
        row = require_key(request)
        user_id = str(payload.from_ or "api-user")
        return ctx["process_message"](user_id, payload.text,
                                      brand_slug=row["brand_slug"])

    # ------------------------------------------------------------------
    # Admin — API keys (super-admin)
    # ------------------------------------------------------------------
    class ApiKeyIn(BaseModel):
        brand: str
        label: str = ""

    @router.post("/admin/api-keys", tags=["Admin — Integrations"],
                 summary="Kisi brand ke liye API key banao (plaintext sirf ek baar milega)")
    def admin_create_key(request: Request, payload: ApiKeyIn):
        require_admin(request)
        brand_or_404(payload.brand)
        row, plaintext = create_api_key(
            ctx["data"](), payload.brand, payload.label)
        return {"ok": True, "id": row["id"], "brand": payload.brand,
                "label": row["label"], "api_key": plaintext,
                "note": "Ye key dobara nahi dikhegi — abhi save kar lo."}

    @router.get("/admin/api-keys", tags=["Admin — Integrations"],
                summary="Ek brand ki API keys ki list (hash kabhi return nahi hota)")
    def admin_list_keys(request: Request, brand: str):
        require_admin(request)
        brand_or_404(brand)
        keys = [{k: r[k] for k in ("id", "brand_slug", "label",
                                   "created_at", "revoked")}
                for r in ctx["data"]().list_api_keys(brand)]
        return {"brand": brand, "keys": keys}

    @router.post("/admin/api-keys/{key_id}/revoke",
                 tags=["Admin — Integrations"],
                 summary="API key band karo (uske baad wo kaam nahi karegi)")
    def admin_revoke_key(request: Request, key_id: int):
        require_admin(request)
        if ctx["data"]().get_api_key(key_id) is None:
            raise HTTPException(status_code=404, detail="key not found")
        ctx["data"]().revoke_api_key(key_id)
        return {"ok": True, "id": key_id, "revoked": True}

    # ------------------------------------------------------------------
    # Admin — outbound webhook subscriptions (super-admin)
    # ------------------------------------------------------------------
    class WebhookIn(BaseModel):
        brand: str
        url: str
        events: list = ["booking.created"]
        secret: str = ""

    @router.post("/admin/webhooks", tags=["Admin — Integrations"],
                 summary="Brand ke liye outbound webhook subscription banao")
    def admin_create_webhook(request: Request, payload: WebhookIn):
        require_admin(request)
        brand_or_404(payload.brand)
        bad = [e for e in payload.events if e not in EVENTS]
        if bad:
            raise HTTPException(
                status_code=400,
                detail=f"unknown events: {bad}; allowed: {list(EVENTS)}")
        if not payload.url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400,
                                detail="url http:// ya https:// se shuru ho")
        sub = {
            "id": uuid.uuid4().hex,
            "brand_slug": payload.brand,
            "url": payload.url,
            "events": payload.events,
            "secret": payload.secret or secrets.token_urlsafe(24),
            "active": True,
            "created_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
        }
        row = ctx["data"]().create_webhook_subscription(sub)
        return {"ok": True, "subscription": {
            k: row[k] for k in ("id", "brand_slug", "url", "events",
                                "active", "created_at")},
            "secret": sub["secret"],
            "note": "Secret isi se signature verify hoga — save kar lo."}

    @router.get("/admin/webhooks", tags=["Admin — Integrations"],
                summary="Brand ki webhook subscriptions ki list")
    def admin_list_webhooks(request: Request, brand: str):
        require_admin(request)
        brand_or_404(brand)
        subs = [{k: s[k] for k in ("id", "brand_slug", "url", "events",
                                   "active", "created_at")}
                for s in ctx["data"]().list_webhook_subscriptions(
                    brand_slug=brand)]
        return {"brand": brand, "subscriptions": subs}

    # ------------------------------------------------------------------
    # Embed — iframe ke liye read-only stats page (token = API key)
    # ------------------------------------------------------------------
    @router.get("/embed/{brand}", response_class=HTMLResponse,
                tags=["Embed"],
                summary="Read-only stats page (iframe embed). Query: ?token=<brand API key>")
    def embed_page(brand: str, token: str = ""):
        brands = ctx["brands"]()
        if brand not in brands:
            return HTMLResponse("<h3>Unknown brand</h3>", status_code=404)
        row = verify_api_key(ctx["data"](), token)
        if row is None or row["brand_slug"] != brand:
            return HTMLResponse(
                "<div style='font-family:Arial;padding:24px'>"
                "<h3>🔒 Embed token galat hai</h3>"
                "<p>Apni brand ki API key <code>?token=</code> me do.</p>"
                "</div>", status_code=401)
        data = ctx["data"]()
        stats = brand_stats(data, brands, brand)
        bookings = list(reversed(data.list_leads(
            brand, stage="booking_confirmed")))[:5]
        rows = "".join(
            "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                html.escape(str(b.get("name") or "—")),
                html.escape(str(b.get("interest") or "—")),
                html.escape(str(b.get("timestamp") or "")[:16]))
            for b in bookings) or \
            "<tr><td colspan='3'>Abhi koi booking nahi</td></tr>"
        name = html.escape(brands[brand]["cfg"].name)
        return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} — AKSynxa Stats</title>
<style>
  * {{ box-sizing: border-box; margin: 0; font-family: "Segoe UI", Arial, sans-serif; }}
  body {{ background: #f4f6fb; color: #14213d; padding: 18px; }}
  h1 {{ font-size: 17px; color: #0b1f4a; margin-bottom: 4px; }}
  .sub {{ font-size: 12px; color: #64748b; margin-bottom: 14px; }}
  .cards {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 16px; }}
  .card {{ background: #fff; border: 1px solid #dde3f0; border-radius: 12px; padding: 12px; text-align: center; }}
  .card b {{ display: block; font-size: 22px; color: #0b1f4a; }}
  .card span {{ font-size: 11px; color: #64748b; }}
  table {{ width: 100%; background: #fff; border: 1px solid #dde3f0; border-radius: 12px; border-collapse: collapse; }}
  th, td {{ text-align: left; padding: 8px 10px; font-size: 13px; border-bottom: 1px solid #eef2f7; }}
  th {{ color: #0b1f4a; }}
  @media (max-width: 560px) {{ .cards {{ grid-template-columns: repeat(2, 1fr); }} }}
</style></head><body>
  <h1>{name}</h1>
  <div class="sub">AKSynxa live stats &middot; Powered by AKSynxa</div>
  <div class="cards">
    <div class="card"><b>{stats['total_leads']}</b><span>Total Leads</span></div>
    <div class="card"><b>{stats['bookings']}</b><span>Bookings</span></div>
    <div class="card"><b>{stats['total_chat_turns']}</b><span>Chat Messages</span></div>
    <div class="card"><b>{stats['pending_reminders']}</b><span>Pending Reminders</span></div>
  </div>
  <table><tr><th>Recent Bookings</th><th>Service</th><th>Time</th></tr>{rows}</table>
</body></html>"""

    return router
