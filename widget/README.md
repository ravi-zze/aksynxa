# AKSynxa Website Chat Widget

Ek file, koi build step nahi, koi dependency nahi — vanilla JS.
Kisi bhi website par lagao, visitor seedha business ke WhatsApp-wale
bot se website par hi baat karega (booking bhi wahi se hogi, lead
AKSynxa dashboard me aayegi).

## Embed — bas ek line

```html
<script src="https://aksynxa-api.onrender.com/widget/aksynxa-widget.js"
        data-brand="YOUR-BRAND-SLUG" defer></script>
```

> Note: widget file abhi repo me hai (`widget/aksynxa-widget.js`) —
> use apni website/CDN par host karke `src` wahan ka do, aur
> `data-api-base` me AKSynxa server ka URL do. Server par static
> route abhi nahi hai; file self-host karna hi current tareeka hai.

## Options (script tag ke attributes)

| Attribute | Zaruri? | Matlab |
|---|---|---|
| `data-brand` | Haan | Kis brand ka bot chalega (e.g. `demo-dental`) |
| `data-api-base` | Nahi | API server base URL. Default: script ke origin se guess, warna production URL |
| `data-title` | Nahi | Chat header ka naam (default "Chat with us") |

```html
<script src="https://your-site.com/aksynxa-widget.js"
        data-brand="demo-dental"
        data-api-base="https://aksynxa-api.onrender.com"
        data-title="SmileCare Dental — Chat" defer></script>
```

## Kaise kaam karta hai

- Right-bottom me floating 💬 bubble; click par chat panel khulta hai.
- Har visitor ko ek random id milta hai (`web-xxxxxxxx`) — sab ki
  apni conversation, bilkul WhatsApp ki tarah.
- Messages `POST /simulate` par jate hain (wahi demo engine);
  booking confirm ho to lead dashboard/API/MCP sab jagah dikhegi.
- Cross-origin ke liye server par CORS on hai (`FRONTEND_ORIGIN`
  env se restrict kar sakte ho).

## Local demo

Server chalao aur `widget/demo.html` ko kisi static server se kholo
(ya tests me istemal hone wala tareeka: demo page + widget file dono
isi repo se serve hote hain). Bubble dabao → "hi" se bot shuru hoga.
