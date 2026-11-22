"""WhatsApp sending abstraction.

- If WHATSAPP_TOKEN is set (aur bhej ne ke liye ek phone_number_id —
  brand config se ya PHONE_NUMBER_ID env se), messages go out through
  the official Meta WhatsApp Cloud API.
- Otherwise the sender runs in DEMO mode: it logs the message and
  returns it, so the whole bot can be tested without any Meta account.

Interactive messages: menu/choices ke liye asli WhatsApp reply-buttons
(zyada se zyada 3) ya list message (zyada options par). Demo mode me
numbered text hi automatic fallback hai — engine ka flow nahi toot ta.

It never crashes just because credentials are missing.
"""
import logging
import os

import requests

log = logging.getLogger("aksynxa.sender")

GRAPH_API_VERSION = "v21.0"


def _effective_phone_id(phone_number_id: str | None) -> str:
    """Brand ka phone_number_id asli (sirf digits) ho to wahi, warna env."""
    if phone_number_id and str(phone_number_id).strip().isdigit():
        return str(phone_number_id).strip()
    return os.environ.get("PHONE_NUMBER_ID", "")


def is_live_mode(phone_number_id: str | None = None) -> bool:
    return bool(os.environ.get("WHATSAPP_TOKEN") and _effective_phone_id(phone_number_id))


def _post(payload: dict, phone_number_id: str | None) -> dict:
    token = os.environ["WHATSAPP_TOKEN"]
    pid = _effective_phone_id(phone_number_id)
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{pid}/messages"
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        ok = resp.status_code < 300
        if not ok:
            log.error("Meta API error %s: %s", resp.status_code, resp.text[:300])
        return {"mode": "live", "to": payload.get("to"), "sent": ok,
                "status_code": resp.status_code}
    except requests.RequestException as exc:  # network problem should not kill the webhook
        log.error("Meta API request failed: %s", exc)
        return {"mode": "live", "to": payload.get("to"), "sent": False, "error": str(exc)}


def send_message(to: str, text: str, phone_number_id: str | None = None) -> dict:
    """Send one text message. Returns a small status dict."""
    if not is_live_mode(phone_number_id):
        log.info("[DEMO MODE] To %s: %s", to, text)
        return {"mode": "demo", "to": to, "text": text, "sent": False}

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    return _post(payload, phone_number_id)


def send_interactive(to: str, body_text: str, choices: dict,
                     phone_number_id: str | None = None) -> dict:
    """Choices ko asli WhatsApp interactive message me bhejo.

    choices = {"kind": "buttons"|"list", "options": [{"id", "title"}]}
    - buttons (<=3 options): reply buttons
    - list: list message (button 'Options chunein')
    Demo mode me kuch extra nahi bhejte — body_text me numbered menu
    pehle se hota hai, wahi fallback hai. Return me 'fallback' batata hai.
    """
    options = (choices or {}).get("options") or []
    if not options:
        return send_message(to, body_text, phone_number_id)

    if not is_live_mode(phone_number_id):
        log.info("[DEMO MODE] Interactive fallback (text) to %s: %d options %s",
                 to, len(options), [o["title"] for o in options])
        return {"mode": "demo", "to": to, "text": body_text,
                "fallback": "numbered_text", "sent": False}

    kind = choices.get("kind")
    if kind == "buttons" and len(options) <= 3:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text[:1024]},
                "action": {
                    "buttons": [
                        {"type": "reply",
                         "reply": {"id": str(o["id"])[:256], "title": str(o["title"])[:20]}}
                        for o in options
                    ]
                },
            },
        }
    else:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": body_text[:1024]},
                "action": {
                    "button": "Options Chunein",
                    "sections": [{
                        "title": "Menu",
                        "rows": [
                            {"id": str(o["id"])[:200], "title": str(o["title"])[:24]}
                            for o in options[:10]
                        ],
                    }],
                },
            },
        }
    return _post(payload, phone_number_id)
