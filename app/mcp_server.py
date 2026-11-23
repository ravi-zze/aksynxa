"""AKSynxa MCP server (V3.2) — platform ka data + bot, MCP tools ke roop me.

Koi bhi MCP client (ChatGPT Developer Mode, Claude, Cursor, ...) is
server se streamable HTTP par connect karke AKSynxa ke business data ko
padh sakta hai aur bot ko test kar sakta hai:

    POST /mcp        (Authorization: Bearer <MCP_API_KEY>)

Design:
  * FastMCP (official `mcp` Python SDK), streamable HTTP transport,
    stateless + JSON responses — Render jaisi free hosting par bhi
    aaram se chalta hai, koi sticky session nahi chahiye.
  * Server EXISTING FastAPI app ke andar hi `/mcp` par mount hota hai
    (app/main.py ke aakhir me `mount_mcp(...)`) — alag hosting nahi.
  * Tools sirf READ karte hain (+ ek simulate tool jo bilkul /simulate
    jaisa behave karta hai). Koi delete/update tool nahi hai, aur koi
    tool asli WhatsApp message NAHI bhejta — simulate bhi demo sender
    se hi guzarta hai, bilkul /simulate endpoint ki tarah.
  * Har tool `brand` argument leta hai aur data layer ki brand_slug
    filtering se padhta hai — ek brand ka data dusre brand ke tool
    call me kabhi nahi aayega (tenant isolation data layer me hai).

Auth:
  * `MCP_API_KEY` env var set ho to /mcp par Bearer token LAZMI hai
    (galat/missing = 401).
  * Dev (non-production) me key na ho to /mcp bina auth ke chalta hai
    (warning ke saath) — sirf local testing ke liye.
  * APP_ENV=production me key na ho to /mcp mount hi NAHI hota
    (warning log hoti hai); main app bilkul pehle jaisa chalta rehta
    hai — MCP ki wajah se production kabhi crash nahi hoga.
"""
import contextlib
import hmac
import logging
import os

log = logging.getLogger("aksynxa.mcp")

_MAX_LIMIT = 200


def _clamp_limit(limit) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 20
    return max(1, min(n, _MAX_LIMIT))


def build_mcp_server(ctx):
    """Ek FastMCP server banao jiske tools `ctx` ke through app state
    padhte hain. ctx keys:
        data()            -> app.data_layer.DataLayer (DATA)
        brands()          -> main.BRANDS dict {slug: runtime}
        process_message   -> main.process_message (bot engine entry)
    Har mount ka apna server instance banta hai — koi global state
    nahi, isliye tests/multi-app me ctx kabhi mix nahi hota."""
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    # DNS-rebinding protection OFF: ye server internet-facing hai aur
    # uski asli security Bearer token hai; SDK ka default localhost-only
    # Host check Render ke hostname ko bina wajah reject kar dega.
    mcp = FastMCP(
        "AKSynxa",
        instructions=(
            "AKSynxa WhatsApp automation platform ke tools. Har business "
            "ek 'brand' hai — pehle list_businesses se slugs dekho, phir "
            "usi brand ke leads/bookings/chats padho. simulate_message "
            "bot ko test message bhejta hai (demo mode, asli WhatsApp "
            "message nahi jata)."
        ),
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False),
    )

    def _unknown_brand(brand: str):
        brands = ctx["brands"]()
        if brand in brands:
            return None
        return {"error": f"unknown brand: {brand!r}",
                "known_brands": sorted(brands)}

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------
    @mcp.tool()
    def list_businesses() -> dict:
        """Platform par configured saare businesses (brands) ki list —
        slug, naam aur tagline. Baaki tools ke `brand` argument ke liye
        slug yahi se lo."""
        brands = ctx["brands"]()
        return {"businesses": [
            {"slug": slug, "name": rt["cfg"].name,
             "tagline": rt["cfg"].tagline}
            for slug, rt in sorted(brands.items())
        ]}

    @mcp.tool()
    def get_dashboard_stats(brand: str) -> dict:
        """Ek brand ke dashboard totals: total leads, confirmed
        bookings, human handoffs, new enquiries, total chat turns
        (in/out alag), aur pending reminders ki count."""
        err = _unknown_brand(brand)
        if err:
            return err
        data = ctx["data"]()
        leads = data.list_leads(brand)
        turns = data.list_chat_turns(brand)
        pending = data.list_reminders(brand_slug=brand, status="pending")
        return {
            "brand": brand,
            "brand_name": ctx["brands"]()[brand]["cfg"].name,
            "total_leads": len(leads),
            "bookings": sum(1 for l in leads
                            if l.get("stage") == "booking_confirmed"),
            "human_handoffs": sum(1 for l in leads
                                  if l.get("stage") == "human_requested"),
            "new_enquiries": sum(1 for l in leads
                                 if l.get("stage") == "new_enquiry"),
            "total_chat_turns": len(turns),
            "messages_in": sum(1 for t in turns
                               if t.get("direction") == "in"),
            "messages_out": sum(1 for t in turns
                                if t.get("direction") == "out"),
            "pending_reminders": len(pending),
        }

    @mcp.tool()
    def list_leads(brand: str, limit: int = 20) -> dict:
        """Ek brand ke sabse recent leads (naye pehle). Har lead:
        timestamp, customer_number, name, interest, stage, summary."""
        err = _unknown_brand(brand)
        if err:
            return err
        leads = ctx["data"]().list_leads(brand)
        recent = list(reversed(leads))[:_clamp_limit(limit)]
        return {"brand": brand, "count": len(recent), "leads": recent}

    @mcp.tool()
    def list_bookings(brand: str, limit: int = 20) -> dict:
        """Ek brand ki sabse recent confirmed bookings (nayi pehle).
        Bookings leads table me stage='booking_confirmed' ke roop me
        rehti hain — service/time lead ke summary me hota hai."""
        err = _unknown_brand(brand)
        if err:
            return err
        bookings = ctx["data"]().list_leads(brand, stage="booking_confirmed")
        recent = list(reversed(bookings))[:_clamp_limit(limit)]
        return {"brand": brand, "count": len(recent), "bookings": recent}

    @mcp.tool()
    def list_reminders(brand: str, status: str = "pending") -> dict:
        """Ek brand ke reminders. status: 'pending' (default), 'sent',
        'cancelled' ya 'all' (saare). Booking reminders aur lead
        follow-ups dono isi me aate hain."""
        err = _unknown_brand(brand)
        if err:
            return err
        status_filter = None if (not status or status == "all") else status
        items = ctx["data"]().list_reminders(brand_slug=brand,
                                             status=status_filter)
        return {"brand": brand, "status": status or "all",
                "count": len(items), "reminders": items[:_MAX_LIMIT]}

    @mcp.tool()
    def get_chat_history(brand: str, phone: str, limit: int = 50) -> dict:
        """Ek brand ke ek customer number ki recent chat history
        (chronological order, aakhri `limit` turns). phone wahi number
        jo customer WhatsApp par use karta hai (jaise '9198...')."""
        err = _unknown_brand(brand)
        if err:
            return err
        turns = [t for t in ctx["data"]().list_chat_turns(brand)
                 if t.get("customer_number") == str(phone)]
        recent = turns[-_clamp_limit(limit):]
        return {"brand": brand, "phone": str(phone),
                "count": len(recent), "turns": recent}

    @mcp.tool()
    def simulate_message(brand: str, from_phone: str, text: str) -> dict:
        """Bot ko ek test message bhejo — bilkul /simulate endpoint
        jaisa. Bot ka reply, current stage aur menu choices wapas
        milte hain. DEMO tool hai: chat turn log hota hai (jaise
        /simulate par hota hai), lekin asli WhatsApp message nahi
        jata (sender demo mode me hai jab tak number connect na ho)."""
        err = _unknown_brand(brand)
        if err:
            return err
        return ctx["process_message"](str(from_phone), str(text),
                                      brand_slug=brand)

    return mcp


class _BearerAuthMiddleware:
    """Pure-ASGI Bearer check — /mcp ke aage lagta hai. Sahi
    `Authorization: Bearer <MCP_API_KEY>` na ho to 401 JSON."""

    def __init__(self, app, token: str):
        self.app = app
        self._expected = f"Bearer {token}"

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = {k.lower(): v for k, v in scope.get("headers", [])}
            auth = headers.get(b"authorization", b"").decode("latin-1")
            if not hmac.compare_digest(auth, self._expected):
                body = (b'{"error":"unauthorized","detail":'
                        b'"Authorization: Bearer <MCP_API_KEY> required"}')
                await send({
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"www-authenticate", b"Bearer"),
                    ],
                })
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def mount_mcp(fastapi_app, ctx) -> bool:
    """FastMCP server ko existing FastAPI app par /mcp mount karo.

    ctx: {"data": callable, "brands": callable,
          "process_message": callable, "app_env": str}

    Return True agar mount hua. Production me MCP_API_KEY missing ho
    to mount NAHI hota (False) — main app unaffected rehta hai."""
    api_key = os.environ.get("MCP_API_KEY", "").strip()
    app_env = str(ctx.get("app_env") or
                  os.environ.get("APP_ENV", "development")).lower()

    if not api_key and app_env == "production":
        log.warning("MCP_API_KEY set nahi hai — production me /mcp "
                    "DISABLED hai. MCP chahiye to Render env me "
                    "MCP_API_KEY set karo. Main app normal chalega.")
        return False

    mcp = build_mcp_server(ctx)
    starlette_app = mcp.streamable_http_app()
    manager = mcp.session_manager

    if api_key:
        starlette_app = _BearerAuthMiddleware(starlette_app, api_key)
        log.info("MCP server /mcp par mounted — Bearer auth ON")
    else:
        log.warning("=" * 64)
        log.warning("MCP_API_KEY set nahi hai — /mcp BINA auth ke chal "
                    "raha hai. Ye SIRF local dev ke liye hai!")
        log.warning("=" * 64)

    fastapi_app.mount("/mcp", starlette_app)

    # Streamable HTTP session manager ko app ke startup par chalana
    # padta hai. Ek hi baar enter karte hain (SDK ka run() ek instance
    # par ek hi baar chal sakta hai) aur process lifetime tak rehne
    # dete hain — production me ek hi startup hota hai, aur tests me
    # baar-baar TestClient banne par bhi ye dobara enter nahi hoga.
    stack = contextlib.AsyncExitStack()
    state = {"entered": False}

    async def _start_mcp_session_manager():
        if not state["entered"]:
            await stack.enter_async_context(manager.run())
            state["entered"] = True
            log.info("MCP session manager started")

    fastapi_app.on_event("startup")(_start_mcp_session_manager)
    return True
