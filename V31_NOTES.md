# AKSynxa V3.1 — Production Backend Upgrade

V3 (verified baseline) ke upar P0 upgrade. V2/V3 ka conversation engine
**byte-for-byte untouched** hai — badlav sirf data layer, auth, webhook
hardening aur unki wiring me hai.

## Kya badla (summary)

| Area | Pehle (V3) | Ab (V3.1) |
|---|---|---|
| Leads | `data/leads_<brand>.json/.csv` files | **DB table `leads`** (files = export mirror) |
| Conversations | `data/chatlog_<brand>.json/.csv` | **DB table `chat_turns`** (files + Google Sheets = mirror) |
| Reminders | `data/reminders.json` | **DB table `reminders`** (data layer ke through) |
| Tenants | sirf YAML configs | **`organizations` table** — har brand startup par register |
| Login | sirf super-admin (ADMIN_PASSWORD) | **+ business users** (email+password, owner/staff roles) |
| Dashboard | admin sab brands dekhta tha | business user **sirf apna brand**; admin sab |
| Webhook | secret ho to verify, na ho to skip | **production me secret LAZMI** — warna server start hi nahi; unsigned = 403 |

## Nayi files

- `app/data_layer.py` — unified data layer (neeche dekho)
- `app/auth.py` — password hashing (stdlib `hashlib.pbkdf2_hmac`, SHA-256,
  200k iterations, per-user salt) + authenticate/create helpers
- `app/templates/business_login.html` — `/business/login` page
- `scripts/create_user.py` — pehla owner/staff banane ka CLI
- `migrations/002_v31_data_layer.sql` — PostgreSQL schema (reference;
  app startup par bhi idempotently banata hai)
- `tests/test_v31.py` — 11 naye tests (total suite: **30 passed**)

## Badli hui files

- `app/main.py` — data layer wiring, business login/logout endpoints,
  `/admin/users` (super-admin user management), `/dashboard/users`
  (owner apne brand ke liye staff banaye), tenant-scoped
  `/dashboard/data`, production `WHATSAPP_APP_SECRET` startup guard,
  `/health` me `data_layer`/`app_env`/`organizations` fields
- `app/leads.py` — `LeadStore` ab data layer me likhta hai, reads DB se;
  JSON/CSV mirror barkarar; purane constructor calls bina `data_layer`
  ke bhi chalte hain (file fallback)
- `app/sheets.py` — `ChatLogger` har turn DB me bhi likhta hai;
  `all_rows()` DB se (tenant-filtered); Sheets webhook/gspread + retry
  queue bilkul pehle jaise
- `app/scheduler.py` — `Scheduler(data_dir, data_layer=...)`: reminders
  DB-backed (status changes bhi DB me persist); data layer na ho to
  purana `reminders.json` fallback (purane tests isiliye bina badle pass)
- `app/data_layer.py` ke alawa engine/config/sender/store files **same**

## DB schema (har table me `brand_slug` = tenant key)

```
organizations(brand_slug PK, name, created_at)
leads(id PK, brand_slug, timestamp, brand_name, customer_number, name,
      interest, stage, summary)                      -- index (brand_slug, id)
chat_turns(id PK, brand_slug, timestamp, brand_name, customer_number,
      name, direction, message, stage)               -- index (brand_slug, id)
reminders(id PK (text), brand_slug, type, user_id, status, due_at,
      payload_json, created_at)                      -- index (brand_slug, status)
users(id PK, brand_slug, email UNIQUE, password_hash,
      role ['owner'|'staff'], created_at)            -- index (brand_slug)
sessions / processed_messages                        -- V3 wale hi
      (app/store.py SQLite ya app/postgres_store.py PostgreSQL)
```

`reminders.payload_json` me pura reminder item (message text, service,
appointment text) rehta hai — scheduler ka round-trip bilkul pehle
jaisa rehta hai, jabki `status`/`due_at` columns queryable hain.

## Data layer design

`app/data_layer.py` ek interface, do backends:

- `DATABASE_URL` **set** → `PostgresDataLayer` (psycopg v3, connection
  pool, lazy import — bina psycopg ke module import ho jata hai)
- `DATABASE_URL` **unset** → `DataLayer` (SQLite stdlib,
  `<AKSYNXA_DATA_DIR>/aksynxa.db`, WAL mode)

Dono ka SQL ek hi code path se jata hai (`?` placeholders; PG par
`%s` me translate). **Har query `brand_slug` se filtered hai** — ye
isolation data layer me hai, sirf UI me nahi.

## Tenant isolation kaise enforce hoti hai

1. **Data layer:** `list_leads` / `list_chat_turns` / `list_users` /
   `list_reminders` sab `WHERE brand_slug=?` ke saath.
2. **Session:** business login par HMAC-signed cookie
   (`aksynxa_biz`) milti hai jisme user id hota hai; har request par
   server **DB se user ko dobara padhta hai** — brand/role cookie se
   trust nahi hote, DB se aate hain.
3. **Endpoints:** business user ke liye `/dashboard/data` ka brand
   hamesha uske apne brand par lock hai — `?brand=<dusra>` bhejne se
   bhi dusre brand ka data nahi milta (test se verified). Cards bhi
   sirf usi ke brand ke aate hain.
4. **Super-admin** (ADMIN_PASSWORD login) purani tarah sab brands dekh
   sakta hai aur `/admin` config bhi sirf usi ke paas hai.

## Roles

- **owner** — apna dashboard + `POST /dashboard/users` se apne brand
  ke staff bana sakta hai
- **staff** — apna dashboard dekh sakta hai; staff banana = **403**
- **super-admin** — sab brands, `/admin` setup, `/admin/users` se kisi
  bhi brand ka user banana

## Pehla owner user kaise banayein

```bash
# Project root se — WAHI env rakho jo server ka hai (khaas taur par
# DATABASE_URL / AKSYNXA_DATA_DIR), warna user galat DB me banega:
python scripts/create_user.py --brand demo-dental \
    --email owner@business.example --role owner
# password prompt par aayega (ya --password '...' do)
```

Ya server chalne ke baad super-admin se:
`POST /admin/users {"brand": "...", "email": "...", "password": "...", "role": "owner"}`

Login page: **`/business/login`** → login ke baad seedha `/dashboard`.

## Locally kaise chalao (SQLite, zero setup)

```bash
./run.sh                      # ya
uvicorn app.main:app --port 8000
pytest                        # 30 tests — 19 purane + 11 naye
```

Data `data/` me jayega: `sessions.db` (sessions/dedupe) +
`aksynxa.db` (leads/chats/reminders/users) + file mirrors.

## Asli PostgreSQL (jaise Neon) ke saath

1. Neon par ek database banao, connection string lo
   (`postgresql://user:pass@host/db?sslmode=require`).
2. Env set karo:
   ```
   DATABASE_URL=postgresql://...        # itna hi — tables app khud bana lega
   APP_ENV=production
   ADMIN_PASSWORD=<strong random>
   WHATSAPP_APP_SECRET=<Meta app secret>  # production me LAZMI hai
   ```
3. Server start karo — sessions bhi Postgres me (`aksynxa_sessions`),
   baaki data `leads`/`chat_turns`/`reminders`/`users`/`organizations` me.
4. Pehla owner: upar wala `scripts/create_user.py` **usi `DATABASE_URL`
   ke saath** chalao.

Note: `psycopg[binary,pool]` requirements.txt me pehle se hai; production
image me install hoga. Is dev machine ke venv me psycopg installed nahi
tha, isliye PG path ka live test nahi ho paya — code `postgres_store.py`
ke proven pattern par hai aur SQLite path puri tarah tested hai.

## Production webhook hardening

- `APP_ENV=production` + `WHATSAPP_APP_SECRET` missing → **startup
  RuntimeError** (ADMIN_PASSWORD guard jaisa hi pattern).
- Production me har `POST /webhook` par `X-Hub-Signature-256` verify
  hota hai; unsigned/galat = **403**, kuch process nahi hota.
- Dev mode me purana behaviour: secret ho to verify, na ho to skip
  (warning ke saath) — existing tests isiliye bina badle pass hote hain.

## Ab bhi baki (is scope ke bahar)

- Hybrid AI integration (intent/FAQ fallback) — P1
- React business dashboard / Super Admin panel — P1 (abhi server-rendered
  dashboard hi scoped hokar chalta hai)
- Billing / subscriptions / analytics — P2
- Durable background jobs (abhi in-process scheduler loop hai; reminders
  DB-backed hain lekin worker alag process nahi hai) — P1
- PostgreSQL ka live integration test (Neon milne par) + purane
  file-based data ka one-time import script (fresh deploy par zaroorat
  nahi; purana data files me hi rahega as archive)
- Password reset / email verification flows business users ke liye
  (abhi admin reset karta hai)
