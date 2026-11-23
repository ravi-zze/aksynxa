# AKSynxa — Integrations Guide (V3.2)

AKSynxa ko baaki tools se jodne ke saare tareeke ek jagah. Sab kuch
existing backend ke upar **additive** hai — bot, dashboard aur
WhatsApp flow pehle jaise hi chalte hain.

| # | Integration | Kahan hai | Auth |
|---|---|---|---|
| 1 | MCP Server | `/mcp` (`app/mcp_server.py`) | Bearer `MCP_API_KEY` |
| 2 | Public REST API v1 | `/api/v1/*` (`app/api_v1.py`) | Per-brand API key |
| 3 | Outbound Webhooks | `app/webhooks.py` + admin endpoints | HMAC-SHA256 signature |
| 4 | GPT Actions pack | `docs/gpt-actions/` | API key (GPT me paste) |
| 5 | n8n Template | `templates/n8n-aksynxa-webhook.json` | Webhook secret (signature) |
| 6 | Python SDK | `sdk/aksynxa_client.py` | API key |
| 7 | Website Chat Widget | `widget/aksynxa-widget.js` | Koi nahi (demo `/simulate`) |
| 8 | Embeddable Dashboard | `/embed/{brand}` | Token = brand API key |
| 9 | PWA | `/manifest.webmanifest`, `/sw.js` | Dashboard login jaisa hi |
| 10 | WhatsApp Flows | `flows/booking_flow.json` | Meta (abhi definition only) |
| 11 | Setup Wizard | `/setup` (`app/wizard.py`) | Koi nahi (draft save) |

---

## 1. MCP Server

**Kya hai:** ChatGPT Developer Mode / Claude / Cursor jaise MCP
clients ke liye tools — `list_businesses`, `get_dashboard_stats`,
`list_leads`, `list_bookings`, `list_reminders`, `get_chat_history`,
`simulate_message`. Poora detail: **MCP_NOTES.md**.

**Enable/use:**
1. Env me `MCP_API_KEY` set karo (production me bina iske `/mcp`
   disabled rehta hai).
2. Client me server URL `https://aksynxa-api.onrender.com/mcp` do.
3. Header: `Authorization: Bearer <MCP_API_KEY>`.

## 2. Public REST API v1

**Kya hai:** Wahi data jo MCP deta hai, plain REST me:
`GET /api/v1/stats`, `GET /api/v1/leads`, `GET /api/v1/bookings`,
`GET /api/v1/reminders`, `POST /api/v1/simulate`. Interactive
reference: server ke **`/docs`** page par (tag "Public API v1").

**Enable/use:**
1. Key banao: `python scripts/create_api_key.py --brand <slug>`
   (ya super-admin se `POST /admin/api-keys`). Key ek hi baar
   dikhti hai; DB me sirf hash rehta hai.
2. Har request me header bhejo: `X-API-Key: aks_<...>`
   (ya `Authorization: Bearer aks_<...>`).
3. Key jis brand ki hai, sirf usi ka data milega — brand choose
   karne ka option hi nahi hai (tenant isolation).
4. Key band karni ho: `POST /admin/api-keys/{id}/revoke`.

## 3. Outbound Webhooks

**Kya hai:** Event hone par tumhare server ko signed POST:
`lead.created`, `booking.created`, `reminder.due`. Headers:
`X-AKSynxa-Event`, `X-AKSynxa-Delivery`, `X-AKSynxa-Signature:
sha256=<HMAC-SHA256(secret, body)>`. Fail ho to retry queue
(exponential backoff, 5 attempts) — scheduler loop retry karta hai.

**Enable/use:**
1. Super-admin se subscription banao: `POST /admin/webhooks`
   `{brand, url, events, secret?}` (secret na do to auto-generate,
   ek baar dikhega).
2. List: `GET /admin/webhooks?brand=<slug>`.
3. Apne receiver par signature verify karo (formula upar hai) —
   bina sahi signature wali request reject karo.

## 4. GPT Actions pack

**Kya hai:** Custom GPT ke liye ready schema + guide.
**Use:** `docs/gpt-actions/README.md` ke 4 steps — GPT banao →
Actions → `openapi.yaml` import → API key auth (`X-API-Key` header).

## 5. n8n Template

**Kya hai:** Booking aate hi Google Sheet me row + notify call
wala ready workflow (sticky notes me andar hi documentation hai).
**Use:**
1. n8n me **Import from File** → `templates/n8n-aksynxa-webhook.json`.
2. Webhook node ki Production URL copy karo.
3. AKSynxa me usi URL ki subscription banao (upar #3) —
   event `booking.created`.
4. Sheets node me apni Sheet select karo, Notify node ki URL badlo,
   workflow **Active** kar do.

## 6. Python SDK

**Kya hai:** `sdk/aksynxa_client.py` — sirf httpx par based chhota
client: `stats()`, `leads()`, `bookings()`, `reminders()`,
`simulate()`. Galti par `AKSynxaAPIError` (status code ke saath).
**Use:** `sdk/example.py` dekho — env me `AKSYNXA_BASE_URL` aur
`AKSYNXA_API_KEY` set karke chalao.

## 7. Website Chat Widget

**Kya hai:** Ek file ka chat bubble (`widget/aksynxa-widget.js`) —
koi build step nahi. Visitor website par hi bot se baat/book karega;
lead dashboard me aayegi.
**Use:** `widget/README.md` ka ek-line embed snippet — file ko apni
site par host karo, `data-brand` aur `data-api-base` set karo.
Local demo: `widget/demo.html`.

## 8. Embeddable Dashboard

**Kya hai:** Read-only stats page (counts + recent bookings) jo
iframe me kahin bhi lag sakti hai: `/embed/<brand-slug>`.
**Use:**
1. Brand ki API key lo (#2 wali).
2. iframe src: `https://aksynxa-api.onrender.com/embed/<slug>?token=<API key>`
3. Token = API key hi hai (simple rakha hai) — dusre brand ki key
   is page par nahi chalegi. Dhyaan: token URL me dikhta hai, isliye
   embed sirf bharosemand jagah par lagao; leak ho to key revoke
   karke nayi bana lo.

## 9. PWA

**Kya hai:** Owner dashboard ko phone par "Install" karke app ki
tarah khol sakta hai. Manifest `/manifest.webmanifest`, minimal
service worker `/sw.js` (sirf app shell cache; data hamesha network
se fresh). Icons brand colors me generated placeholders hain
(`/pwa/icon-192.png`, `/pwa/icon-512.png`) — asli logo mile to
inhi paths par replace kar dena.
**Use:** Kuch enable nahi karna — dashboard/business-login page
kholo, browser khud "Install app / Add to Home Screen" dega.

## 10. WhatsApp Flows

**Kya hai:** Booking form ki Flow JSON definition
(`flows/booking_flow.json`) — naam, service dropdown, date, time.
**Sthiti:** Definition only. WhatsApp number Meta se connect hone ke
baad Flow Builder me upload/publish hoga. Steps: `flows/README.md`.

## 11. Setup Wizard

**Kya hai:** Naye business owner ke liye 3-step easy form (`/setup`)
— business naam/type → services & prices → WhatsApp number/timings,
bade inputs, Hinglish labels, progress indicator.
**Sthiti (honest):** Brand configs file-based YAML hain, isliye
wizard live config NAHI badalta — ye **draft JSON** save karta hai
(`<data dir>/setup_drafts/<slug>.json`). Team us draft se asli
config banakar bot live karti hai. UI me bhi yahi saaf likha hai.

---

## Env vars

Naye env vars: **koi nahi** (Phase 2 ke liye). API keys aur webhook
subscriptions DB me hain. Sirf yaad rakho:

- `MCP_API_KEY` — MCP ke liye (render.yaml / .env.example me listed).
- `FRONTEND_ORIGIN` — set ho to CORS sirf us origin ko milega;
  khaali ho to widget ke liye sab origins allowed hain.

## Tests

```bash
pytest            # poora suite — API/MCP/integrations sab included
```
