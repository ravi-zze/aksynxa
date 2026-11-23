# WhatsApp Flows — Booking Form (definition only)

`booking_flow.json` ek **WhatsApp Flow** ki definition hai: customer
WhatsApp chat ke andar hi ek sundar form bhar kar booking karega —
naam, service (dropdown), date, time — bina chat chhode.

## Abhi ki sthiti (honest)

- Ye sirf **definition (JSON)** hai. Flow tabhi live hoga jab
  AKSynxa ka WhatsApp number Meta se connect hoga.
- Koi Meta API call is repo se NAHI hoti — publishing manual hai.

## Live karne ke steps (WhatsApp connect hone ke baad)

1. **Meta App Dashboard** → WhatsApp → **Flows** → *Create Flow*.
2. Naam do (e.g. "Book Appointment") aur is JSON ka content
   **Flow JSON** editor me paste karo — ya Flow Builder me file
   upload karo.
3. **Services dropdown**: JSON me services abhi *placeholder* hain
   (`__example__` wale). Do tareeke:
   - **Static flow**: Flow Builder me har brand ki asli services
     haath se bhar do (chhoti list ke liye theek hai), ya
   - **Dynamic flow (data_exchange)**: Flow ka endpoint AKSynxa
     server par banao jo brand ki services config se list lautaye —
     ye endpoint abhi NAHI bana hai, WhatsApp connect ke baad ka
     kaam hai.
4. Flow ko **Publish** karo, phir use bot ke message me CTA button
   ("Book Appointment") ke saath jodo — customer form bharega,
   payload webhook par wapas aayega jise engine booking me badlega
   (ye wiring bhi connect ke baad ki hai).

## Payload (form submit par)

```json
{
  "customer_name": "Rahul Sharma",
  "service_id": "1",
  "appointment_date": "2026-11-25",
  "appointment_time": "5:00 PM"
}
```

Dhyaan do: tab tak chat-based booking flow (menu → naam → service →
time → confirm) hi live tareeka hai — ye Flow usi ka upgrade hai,
replacement ki ghoshna abhi mat karo.
