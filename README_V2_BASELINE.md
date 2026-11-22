# AKSynxa WhatsApp Automation Base — NEXT LEVEL 🚀

Ek **ready-made, multi-brand WhatsApp bot product**. Ek hi server par
**saare clients (brands) ek saath** chalte hain — har client ki sirf ek
chhoti si **config file (YAML)** hai. Naam, welcome message, menu,
services, prices, FAQs, reminders — sab config se. **Code chhune ki
zaroorat nahi** — settings badalne ke liye password-protected **Admin
page** aur haal dekhne ke liye **Owner Dashboard** bhi hai.

## Kya-kya milta hai

| Cheez | Kahan |
|---|---|
| Landing page | `http://localhost:8000/` |
| **Admin / Setup page** (password ke peeche) | `http://localhost:8000/admin` |
| **Owner Dashboard** (aaj ki chats, leads, bookings) | `http://localhost:8000/dashboard` |
| **Demo chat** (browser me bot test, brand switch ke saath) | `http://localhost:8000/demo` |
| Bot ka flow | Welcome → Menu (buttons/list) → Services / Prices / Booking / FAQs / Owner se baat |
| **Booking reminders** | Appointment se pehle apne aap (config: `reminders.hours_before`) |
| **Lead follow-up** | Enquiry ke baad booking na hui to 1 polite message (config: `follow_up.after_hours`) |
| Leads | `data/leads_<brand>.json` + `.csv` (aur Sheet me `LEAD:` wali rows) |
| **Har chat ka record** | Google Sheet + `data/chatlog_<brand>.json/.csv` |
| Sessions | **SQLite** (`data/sessions.db`) — server restart par bhi chats continue |

## Architecture (simple shabdon me)

- `app/main.py` — FastAPI server: pages, Meta webhook (signature check +
  dedupe + phone_number_id se brand routing), `/simulate`, background
  scheduler loop.
- `app/engine.py` — baatcheet ka dimaag: menu, booking flow (naam →
  service → time → confirm), FAQ matching, owner handoff. Har stage par
  kaunse options hain, ye bhi engine batata hai — sender unhe asli
  WhatsApp **buttons/list** me badal deta hai.
- `app/config_loader.py` — `brands/` ki **saari** YAML files load +
  validate karta hai. `BRAND_CONFIG` sirf *default* brand chunta hai.
- `app/store.py` — SQLite store: chat sessions (restart-safe) + webhook
  message dedupe.
- `app/scheduler.py` — reminders + lead follow-ups, `data/reminders.json`
  me persist; Hinglish time parsing ("kal 5 baje", "25 Nov, 11 AM").
- `app/sender.py` — Meta Cloud API ya demo mode; text ke saath
  interactive messages (reply buttons / list) bhi.
- `app/leads.py` — leads per-brand JSON + CSV me.
- `app/sheets.py` — har chat turn Google Sheet me; fail ho to retry
  queue (`data/sheets_retry.json`), 3 koshish ke baad local CSV final.
- `brands/*.yaml` — har client ki ek file. Yahi product ki asli taakat hai.

## Chalana kaise hai

Sabse asan — ek command:

```bash
./run.sh
```

Ya manually:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8000
```

Phir browser me kholo: **http://localhost:8000/demo** — upar se brand
badal kar dono bots test karo. `BRAND_CONFIG=brands/aksynxa.yaml` dene
se sirf *default* brand badalta hai; baaki brands bhi saath chalte hain.

### Server par deploy (Docker)

```bash
cp .env.example .env    # values bharo — khaas karke ADMIN_PASSWORD
docker compose up -d --build
```

`data/` aur `brands/` volumes hain — container rebuild par bhi leads,
sessions aur configs safe rehte hain.

## Admin login — password zaroor badlo ⚠️

**http://localhost:8000/admin** aur **/dashboard** password ke peeche
hain. Password `.env` me `ADMIN_PASSWORD` se set hota hai.

> 🔴 **BADI WARNING:** `ADMIN_PASSWORD` set na ho to default password
> **`admin123`** chalta hai — ye **SIRF local demo** ke liye hai.
> Internet par deploy karne se pehle `.env` me strong password rakhna
> **lazmi** hai, warna koi bhi aapki settings badal sakta hai. Server
> start hote hi log me bhi ye warning dikhti hai jab default chal raha ho.

Admin page par upar **brand switcher** hai — jis brand ko chunein, usi
ki settings dikhti aur save hoti hain (business name, welcome, services,
FAQs, owner number, Sheet URL, WhatsApp Phone Number ID, reminder /
follow-up settings). SAVE dabate hi **usi brand** ka bot turant nayi
settings se chalta hai — baaki brands aur chal rahi chats (sessions DB
me hain) bilkul disturb nahi hoti.

## Dashboard

**http://localhost:8000/dashboard** (login ke baad) — har brand ke liye:
aaj ki total chats, total leads, bookings, human handoffs; neeche kisi
bhi brand ki **aakhri 10 leads** ki table aur pending reminders /
follow-ups ki ginti.

## Demo mode me test (curl)

```bash
# Kisi bhi brand par (brand = YAML file ka naam bina .yaml ke)
curl -s -X POST "localhost:8000/simulate?brand=demo-dental" \
  -H 'Content-Type: application/json' -d '{"from": "919999999999", "text": "hi"}'

curl -s -X POST "localhost:8000/simulate?brand=aksynxa" \
  -H 'Content-Type: application/json' -d '{"from": "919999999999", "text": "hi"}'

# Booking flow — ek hi number se lagatar bhejo; server restart karne
# par bhi bot wahi se continue karega jahan chhoda tha (SQLite sessions)
curl -s -X POST "localhost:8000/simulate?brand=demo-dental" \
  -H 'Content-Type: application/json' -d '{"from": "919999999999", "text": "3"}'
# ... phir naam, service number, "kal 5 baje", "yes"
```

Response me `choices` bhi aata hai — yahi options live WhatsApp par
buttons/list bankar jayenge; demo mode me numbered text hi fallback hai.

## Naya client onboard karna — sirf 3 steps (multi-brand)

1. `brands/demo-dental.yaml` ki copy banao — jaise `brands/sharma-bakery.yaml` —
   aur client ka naam, services, prices, FAQs, owner number bharo.
   (Ya server chala kar **/admin** se bharo — aur bhi asan.)
2. Client ka WhatsApp number Meta par jud jaye to uski **Phone Number
   ID** YAML me `whatsapp_phone_number_id` me daalo. Tab tak placeholder
   chalega — webhook ke messages isi ID se sahi brand par route honge.
3. Server **restart** karo (nayi file tabhi load hoti hai) — naya client
   live hai, purane clients bina ruke chalte rahenge. Engine ek hi hai.

## Reminders & Follow-ups — jo hum bechte hain, wahi bot karta hai

- **Booking reminder:** booking confirm hote hi schedule ho jata hai.
  Customer ke likhe time ("kal 5 baje", "25 Nov, 11 AM") ko bot samajh
  kar appointment se `reminders.hours_before` ghante pehle reminder
  bhejta hai. Time samajh na aaye to item `unparseable` mark hokar
  `data/reminders.json` me rehta hai — owner manually bhej sakta hai.
- **Lead follow-up:** nayi enquiry ke `follow_up.after_hours` ghante
  baad bhi booking nahi hui, to brand ke `follow_up.message` wala ek
  polite message jata hai — **zyada se zyada ek baar**. Booking ho gayi
  ho to follow-up apne aap cancel.
- Dono `data/reminders.json` me persist hote hain — restart par kuch
  nahi khota. Background loop har 30 second (env
  `SCHEDULER_TICK_SECONDS`) me due items check karta hai.

## Chat Log — HAR chat Google Sheet me

Sirf leads nahi — **har ek message** (customer ka bhi, bot ka bhi,
reminder/follow-up bhi) Sheet me ek row banta hai, brand ke hisaab se:

`Timestamp (IST) | Brand | Customer Number | Name | Direction (in/out) | Message | Stage`

Lead banne par ek extra row aati hai jiska **Stage `LEAD: booking_confirmed`**
jaisa hota hai — Sheet me filter laga kar saari leads ek saath dikh jayengi.

**Reliability:** Sheet ko bhejna fail ho jaye to row
`data/sheets_retry.json` queue me jati hai; background loop har 10
minute (env `SHEETS_RETRY_SECONDS`) dobara koshish karta hai. 3 baar
fail hone ke baad row queue se hat jati hai — **local CSV hi final
record** rehta hai, wo hamesha likha jata hai. Sheet fail ho to bhi
chat kabhi nahi rukti.

### Method A — Apps Script webhook (sabse asan, recommended)

1. Ek nayi **Google Sheet** banao (sheets.google.com).
2. Upar menu me **Extensions → Apps Script** kholo.
3. Wahan ka code hata kar is repo ki **`docs/apps-script.gs`** wali file ka
   pura code paste karo aur Save karo.
4. **Deploy → New deployment → Web app** chuno; "Who has access" me
   **Anyone** select karke Deploy karo.
5. Jo Web App URL mile, use `.env` me daalo:
   `GOOGLE_SHEET_WEBHOOK_URL=<wo URL>`
   — ya bot ke **/admin** page me "Google Sheet Webhook URL" me bhar do.

### Method B — Service account (gspread, optional)

`.env` me `GOOGLE_SERVICE_ACCOUNT_JSON` (service-account JSON file ka path
ya pura JSON) aur `GOOGLE_SHEET_ID` daalo, aur `pip install gspread` karo.
gspread na ho to ye method apne aap band rehta hai — baaki sab chalta rehta hai.

## Asli WhatsApp number se jodna (baad me)

1. Meta for Developers me app banao → WhatsApp product jodo → har client
   ka number register karo.
2. Har number ki **Phone Number ID** us brand ki YAML me
   `whatsapp_phone_number_id` me daalo; **Permanent Access Token**
   `.env` ke `WHATSAPP_TOKEN` me (token saare numbers ke liye ek hi
   app ka hota hai).
3. `.env` me `WHATSAPP_APP_SECRET` bhi bharo — isse har webhook request
   ki **signature verify** hogi (nakli requests reject). Aur `VERIFY_TOKEN`
   wahi rakho jo Meta webhook setup me doge.
4. Meta app ke webhook settings me URL do: `https://<aapka-server>/webhook`
5. Server restart karo — sender apne aap live mode me chala jayega.
   Menu/choices asli **buttons aur list messages** bankar jayenge,
   button par tap ka jawab bhi bot samajh lega. Duplicate messages
   (Meta retries) apne aap dedupe ho jayenge — customer ko dobara
   reply nahi jayega.

## Tests

```bash
.venv/bin/python -m pytest
```

`tests/test_engine.py` — dono brands ke core flows (greeting, prices,
FAQ, pura booking flow, human handoff), multi-brand ek saath, session
persistence (restart simulation), scheduler (time parsing, reminder,
follow-up cancel/send). `tests/test_api.py` — HTTP level par brand
routing, admin login + save, dashboard, webhook dedupe aur signature.

## Folder structure

```
whatsapp-base/
├── app/
│   ├── main.py          # server + pages + webhook + scheduler loop
│   ├── engine.py        # conversation flow + interactive choices
│   ├── config_loader.py # saari brand YAMLs load/validate
│   ├── store.py         # SQLite: sessions + webhook dedupe
│   ├── scheduler.py     # reminders + lead follow-ups
│   ├── sender.py        # Meta API / demo mode + buttons/list
│   ├── leads.py         # leads -> data/*.json + .csv
│   ├── sheets.py        # har chat -> Google Sheet (+ retry queue)
│   └── templates/       # landing / login / admin / dashboard / demo
├── brands/              # har client ki ek YAML
│   ├── demo-dental.yaml # sample: fictional dental clinic
│   └── aksynxa.yaml     # AKSynxa agency khud ki config
├── docs/apps-script.gs  # Sheet logger ka Apps Script code
├── data/                # leads, chatlogs, sessions.db, reminders.json
├── tests/               # pytest suite
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── run.sh               # ek command: setup + start
```

---
AKSynxa — AI Automation Agency, New Delhi · *"Business, on Autopilot"*
