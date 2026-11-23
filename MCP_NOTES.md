# AKSynxa MCP Server (V3.2)

AKSynxa ka data aur WhatsApp bot ab **MCP (Model Context Protocol)**
tools ke roop me available hai — koi bhi MCP client (ChatGPT Developer
Mode, Claude, Cursor, ...) connect karke kisi bhi brand ke leads,
bookings, chats aur stats padh sakta hai, aur bot ko test kar sakta hai.

## Endpoint

| | |
|---|---|
| Production | `https://aksynxa-api.onrender.com/mcp` |
| Local | `http://localhost:8000/mcp` |
| Transport | Streamable HTTP (stateless, JSON responses) |
| Auth | `Authorization: Bearer <MCP_API_KEY>` header — LAZMI |

Server existing FastAPI app ke andar hi mount hai (`app/mcp_server.py`,
`app/main.py` ke aakhir me wiring) — alag hosting nahi hai. Render par
wahi service deploy hoti hai, bas `/mcp` path add ho gaya hai.

## Tools

| Tool | Kya karta hai |
|---|---|
| `list_businesses` | Platform par configured saare brands (slug, naam, tagline) |
| `get_dashboard_stats(brand)` | Dashboard totals: leads, bookings, human handoffs, enquiries, chat turns (in/out), pending reminders |
| `list_leads(brand, limit)` | Brand ke sabse recent leads (naye pehle) |
| `list_bookings(brand, limit)` | Recent confirmed bookings (stage = `booking_confirmed` wale leads) |
| `list_reminders(brand, status)` | Reminders — default `pending`; `sent` / `cancelled` / `all` bhi |
| `get_chat_history(brand, phone)` | Ek customer number ki recent chat history |
| `simulate_message(brand, from_phone, text)` | Bot ko test message — bilkul `/simulate` jaisa reply/stage/choices. Chat turn log hota hai, asli WhatsApp message NAHI jata |

Saare tools **read-only** hain (simulate demo tool hai) — koi
delete/update tool nahi hai, aur koi tool asli WhatsApp message nahi
bhejta. Har tool ka data `brand` se scoped hai: ek brand ka data dusre
brand ke call me kabhi nahi aata (data layer ki `brand_slug` filtering).

## Auth — MCP_API_KEY

- Key env var `MCP_API_KEY` se aati hai (Render env / local `.env`).
- Client har request me header bheje: `Authorization: Bearer <key>`.
  Galat ya missing token = **401**.
- **Production me key set na ho to `/mcp` mount hi nahi hota** (log me
  warning aati hai) — main app bilkul normal chalta rehta hai.
- Local dev me key na ho to `/mcp` bina auth ke chalta hai (warning ke
  saath) — sirf apni machine par testing ke liye.
- Asli key `~/workspace/aksynxa/deploy-secrets.env` me rakhi hai
  (repo me NAHI hai). Render par deploy ke time wahi value env var me
  dalni hai.

## ChatGPT Developer Mode me connect karna (4–5 steps)

1. ChatGPT kholo → **Settings → Connectors** (Developer Mode on hona
   chahiye — Plus/Pro par available hai).
2. **Create** (new connector) dabao.
3. Name: `AKSynxa`, URL: `https://aksynxa-api.onrender.com/mcp`.
4. Authentication me **Bearer token** select karke `MCP_API_KEY` ki
   value paste karo (ya header `Authorization: Bearer <key>` set karo,
   jo option mile).
5. **Create/Save** — phir kisi chat me connector on karke puchho:
   *"AKSynxa par kaun se businesses hain?"* ya *"SmileCare Dental ke
   dashboard stats dikhao"* — ChatGPT khud sahi tool call karega.

Claude / Cursor jaise clients me bhi yahi pattern hai: remote MCP
server URL + `Authorization` header me Bearer token.

## Deploy ke bache hue steps

1. Code ko GitHub repo **ravi-zze/aksynxa** par push karo — Render
   auto-redeploy karega (`requirements.txt` me `mcp` package add ho
   chuka hai, `render.yaml` me `MCP_API_KEY` env listed hai).
2. Render dashboard → `aksynxa-api` service → **Environment** →
   `MCP_API_KEY` = wahi value jo `deploy-secrets.env` me hai.
3. Deploy ke baad verify: `GET https://aksynxa-api.onrender.com/health`
   200 aaye, aur koi MCP client se `list_businesses` call karke dekho.
   (Note: Render free instance soya hota hai — pehli request par
   ~50 sec lag sakte hain.)

## Local test

```bash
MCP_API_KEY=<koi-bhi-test-key> uvicorn app.main:app --port 8000
pytest tests/test_mcp.py     # auth, tools/list, read tools, isolation
```
