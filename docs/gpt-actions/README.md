# AKSynxa ko apne Custom GPT se jodo (GPT Actions)

Is pack se tumhara Custom GPT seedha AKSynxa se baat karega —
*"aaj kitni bookings aayi?"* puchho, GPT khud API call karke jawab
dega. Is folder me hai: `openapi.yaml` (sirf `/api/v1` endpoints
wala trimmed schema).

## Pehle: API key banao

```bash
python scripts/create_api_key.py --brand <brand-slug> --label "My GPT"
```

Key ek hi baar dikhegi — save kar lo. (Ya super-admin se
`POST /admin/api-keys`.)

## Setup — 4 steps

1. **GPT banao** — ChatGPT → *Explore GPTs → Create* → apne GPT ka
   naam/instructions likho (e.g. "Tum mere business assistant ho,
   AKSynxa data se jawab do").
2. **Actions kholo** — GPT editor me neeche **Actions → Create new
   action** dabao.
3. **Schema import karo** — *Import from URL* ki jagah is file
   (`openapi.yaml`) ka pura content **paste** kar do (ya "Upload"
   se file do). Server URL pehle se
   `https://aksynxa-api.onrender.com` set hai.
4. **API key auth lagao** — Action ki *Authentication* setting me
   **API Key** select karo, Auth Type **Custom**, header name
   `X-API-Key`, aur value me upar banayi hui AKSynxa key paste karo.
   Save → GPT publish/update kar do.

## Test

GPT se puchho:

- "Mere business ke stats dikhao" → `getStats`
- "Recent bookings kaun si hain?" → `listBookings`
- "Bot ko 'hi' bhej kar test karo" → `simulateMessage`

## Dhyaan do

- Key brand-scoped hai — ek GPT = ek business. Dusre business ke
  liye dusri key + dusra action/GPT.
- Render free instance soya hota hai — pehli call par ~50 sec lag
  sakte hain, GPT thoda wait karega, error nahi hai.
- Key leak ho jaye to `POST /admin/api-keys/{id}/revoke` se band
  karke nayi bana lo.
