"""AKSynxa V3.2 — outbound webhooks.

Jab koi event hota hai (lead.created / booking.created / reminder.due),
us brand ke active subscriptions par signed POST jata hai:

    POST <subscription.url>
    Content-Type: application/json
    X-AKSynxa-Event: booking.created
    X-AKSynxa-Delivery: <delivery id>
    X-AKSynxa-Signature: sha256=<HMAC-SHA256(secret, raw body)>

Body: {"event": ..., "brand": <slug>, "created_at": <iso>, "data": {...}}

Delivery fail ho to delivery row `pending` rehti hai aur
`process_retries()` use dobara try karta hai (exponential backoff,
max attempts ke baad `failed`) — bilkul Sheets retry queue wale
pattern par. Scheduler loop har retry pass me ise bulata hai
(app/main.py). Pehli koshish event ke time par hi hoti hai
(synchronous, chhota timeout) taaki khush-khabri turant jaye aur
failure turant queue me dikhe.
"""
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger("aksynxa.webhooks")

EVENTS = ("lead.created", "booking.created", "reminder.due")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def sign_body(secret: str, body: bytes) -> str:
    """Webhook signature — receiver isi formula se verify karega."""
    return "sha256=" + hmac.new(
        (secret or "").encode(), body, hashlib.sha256).hexdigest()


def event_for_lead_stage(stage: str):
    """Lead stage -> webhook event. Baaki stages par event nahi."""
    if stage == "booking_confirmed":
        return "booking.created"
    if stage == "new_enquiry":
        return "lead.created"
    return None


class WebhookDispatcher:
    def __init__(self, data_layer, timeout: float = 3.0, max_attempts: int = 5):
        self.data = data_layer
        self.timeout = timeout
        self.max_attempts = max_attempts

    # -- event entry points ------------------------------------------------
    def on_lead(self, brand_slug: str, record: dict) -> None:
        """data_layer.lead_hook — har naye lead par main.py ise wire
        karta hai. Stage se event banta hai (booking alag, enquiry alag)."""
        event = event_for_lead_stage(record.get("stage", ""))
        if event is None:
            return
        self.fire(brand_slug, event, {"lead": record})

    def fire(self, brand_slug: str, event: str, data: dict) -> int:
        """Ek event ko us brand ke matching subscriptions par bhejo.
        Returns: kitni deliveries create huin."""
        if event not in EVENTS:
            log.warning("unknown webhook event: %s", event)
            return 0
        payload = {"event": event, "brand": brand_slug,
                   "created_at": _iso(_utc_now()), "data": data}
        made = 0
        for sub in self.data.list_webhook_subscriptions(
                brand_slug=brand_slug, active_only=True):
            if event not in (sub.get("events") or []):
                continue
            delivery = {
                "id": uuid.uuid4().hex,
                "subscription_id": sub["id"],
                "brand_slug": brand_slug,
                "event": event,
                "payload": payload,
                "status": "pending",
                "attempts": 0,
                "next_retry_at": None,
                "created_at": _iso(_utc_now()),
            }
            self.data.add_webhook_delivery(delivery)
            self._attempt(sub, delivery)
            made += 1
        return made

    # -- delivery ------------------------------------------------------------
    def _attempt(self, sub: dict, delivery: dict) -> None:
        body = json.dumps(delivery["payload"], ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-AKSynxa-Event": delivery["event"],
            "X-AKSynxa-Delivery": delivery["id"],
            "X-AKSynxa-Signature": sign_body(sub.get("secret", ""), body),
        }
        attempts = int(delivery.get("attempts", 0)) + 1
        try:
            resp = requests.post(sub["url"], data=body, headers=headers,
                                 timeout=self.timeout)
            if 200 <= resp.status_code < 300:
                self.data.update_webhook_delivery(
                    delivery["id"], status="sent", attempts=attempts,
                    next_retry_at=None, last_error="")
                log.info("webhook %s -> %s (%s)", delivery["event"],
                         sub["url"], resp.status_code)
                return
            error = f"HTTP {resp.status_code}"
        except Exception as exc:  # network down, timeout, DNS — sab retryable
            error = f"{type(exc).__name__}: {exc}"
        self._schedule_retry(delivery, attempts, error)

    def _schedule_retry(self, delivery: dict, attempts: int, error: str) -> None:
        if attempts >= self.max_attempts:
            self.data.update_webhook_delivery(
                delivery["id"], status="failed", attempts=attempts,
                next_retry_at=None, last_error=error)
            log.warning("webhook delivery %s FAILED (attempts=%s): %s",
                        delivery["id"], attempts, error)
            return
        backoff = min(30 * (2 ** (attempts - 1)), 1800)  # 30s, 60s, 120s...
        self.data.update_webhook_delivery(
            delivery["id"], status="pending", attempts=attempts,
            next_retry_at=_iso(_utc_now() + timedelta(seconds=backoff)),
            last_error=error)
        log.info("webhook delivery %s retry scheduled (%s)", delivery["id"], error)

    def process_retries(self) -> dict:
        """Due pending deliveries ko dobara try karo. Scheduler loop
        har retry pass me bulata hai (Sheets retry jaisa hi pattern)."""
        due = self.data.list_due_webhook_deliveries(_iso(_utc_now()))
        sent = failed = 0
        for delivery in due:
            sub = self.data.get_webhook_subscription(
                delivery["subscription_id"])
            if sub is None or not sub.get("active"):
                self.data.update_webhook_delivery(
                    delivery["id"], status="failed",
                    last_error="subscription missing/inactive")
                failed += 1
                continue
            before = delivery["attempts"]
            self._attempt(sub, delivery)
            after = self.data.list_webhook_deliveries(
                brand_slug=delivery["brand_slug"], status=None)
            row = next((d for d in after if d["id"] == delivery["id"]), None)
            if row and row["status"] == "sent":
                sent += 1
            elif row and row["status"] == "failed":
                failed += 1
            _ = before
        return {"tried": len(due), "sent": sent, "failed": failed}
