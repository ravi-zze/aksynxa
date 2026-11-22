"""Reminder + lead follow-up engine — jo services hum bechte hain.

Do kaam:
  1. BOOKING REMINDER — booking confirm hote hi ek reminder schedule
     hota hai (brand config `reminders.hours_before`, default 1 ghanta
     pehle). Appointment ka time customer ke free-text se parse hota
     hai ("kal 5 baje", "25 Nov, 11 AM", "aaj 4:30 pm"...).
  2. LEAD FOLLOW-UP — nayi enquiry ke `follow_up.after_hours` ghante
     baad bhi booking nahi hui, to ek polite follow-up message.
     Zyada se zyada EK baar. Booking ho gayi ho to follow-up cancel.

Saare pending kaam V3.1 me DATABASE (reminders table) me rehte hain —
server restart ho jaye to bhi kuch nahi khota. (Data layer na diya ho
to purana `data/reminders.json` fallback.) Ek background loop (main.py
me) har kuch seconds me `run_due()` chalata hai; time aane par message
`send_callback` ke through jata hai (live mode me asli WhatsApp,
demo mode me log + chatlog row).
"""
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover
    IST = timezone(timedelta(hours=5, minutes=30))

log = logging.getLogger("aksynxa.scheduler")

# ---------------------------------------------------------------------------
# Free-text appointment time parsing (Hinglish dost)
# ---------------------------------------------------------------------------
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def parse_appointment(text: str, now: datetime | None = None) -> datetime | None:
    """'kal 5 baje' / '25 Nov, 11 AM' / 'aaj 4:30 pm' -> datetime (IST) ya None."""
    if not text:
        return None
    now = now or datetime.now(IST)
    low = " " + text.lower().strip() + " "

    # ---- din (date) ----
    day = None
    if "parso" in low:
        day = (now + timedelta(days=2)).date()
    elif "kal" in low or "tomorrow" in low:
        day = (now + timedelta(days=1)).date()
    elif "aaj" in low or "today" in low:
        day = now.date()
    else:
        m = re.search(r"(\d{1,2})\s*([a-z]+)", low)
        if m and m.group(2) in _MONTHS:
            d, mo = int(m.group(1)), _MONTHS[m.group(2)]
            try:
                candidate = datetime(now.year, mo, d).date()
                if candidate < now.date():
                    candidate = datetime(now.year + 1, mo, d).date()
                day = candidate
            except ValueError:
                day = None
        if day is None:
            m = re.search(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", low)
            if m:
                d, mo = int(m.group(1)), int(m.group(2))
                yr = int(m.group(3)) if m.group(3) else now.year
                if yr < 100:
                    yr += 2000
                try:
                    candidate = datetime(yr, mo, d).date()
                    if not m.group(3) and candidate < now.date():
                        candidate = datetime(yr + 1, mo, d).date()
                    day = candidate
                except ValueError:
                    day = None

    # ---- samay (time) ----
    hour = minute = None
    m = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)?", low)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if m.group(3) == "pm" and hour < 12:
            hour += 12
        elif m.group(3) == "am" and hour == 12:
            hour = 0
    else:
        m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(baje|am|pm)", low)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2) or 0)
            marker = m.group(3)
            if marker == "pm" and hour < 12:
                hour += 12
            elif marker == "am" and hour == 12:
                hour = 0
            elif marker == "baje":
                # Hinglish 'baje': subah/shaam/raat ke ishare, warna aam
                # appointment hours ka andaza (8-11 => AM, 1-7 => PM)
                if "subah" in low or "morning" in low:
                    pass
                elif "shaam" in low or "raat" in low or "evening" in low or "night" in low:
                    if hour < 12:
                        hour += 12
                elif hour == 12:
                    hour = 12  # dopahar
                elif 1 <= hour <= 7:
                    hour += 12
                # 8..11 baje => AM as-is
    if hour is None:
        return None
    if day is None:
        day = now.date()
        candidate = datetime(day.year, day.month, day.day, hour, minute or 0, tzinfo=IST)
        if candidate <= now:  # aaj ka time nikal gaya -> kal samjho
            day = day + timedelta(days=1)
    return datetime(day.year, day.month, day.day, hour, minute or 0, tzinfo=IST)


# ---------------------------------------------------------------------------
# Persistent schedule store — V3.1: DATABASE-backed (data layer).
# Data layer milne par pending kaam `reminders` table me rehta hai
# (SQLite local / PostgreSQL production) — server restart ho jaye to
# bhi kuch nahi khota, aur har item apne brand (tenant) ke saath
# bandha hai. Data layer na diya ho (purane standalone uses/tests)
# to purana data/reminders.json file-store fallback chalta hai.
# ---------------------------------------------------------------------------
class Scheduler:
    def __init__(self, data_dir: str, data_layer=None):
        self.path = os.path.join(data_dir, "reminders.json")
        os.makedirs(data_dir, exist_ok=True)
        self.data_layer = data_layer

    # -- file fallback (sirf tab jab data layer na ho) -------------------
    def _load_file(self) -> list:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                return data if isinstance(data, list) else []
            except (json.JSONDecodeError, OSError):
                return []
        return []

    def _save_file(self, items: list) -> None:
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(items, fh, ensure_ascii=False, indent=2)

    # -- unified load/save -------------------------------------------------
    def _load(self) -> list:
        if self.data_layer is not None:
            return self.data_layer.list_reminders()
        return self._load_file()

    def _save(self, items: list) -> None:
        # File mode me puri list ek saath likhte hain (purana behaviour).
        # DB mode me _save ka kaam update_reminder_status / add_reminder
        # pehle hi kar chuke hote hain — yahan kuch nahi karna.
        if self.data_layer is None:
            self._save_file(items)

    def _persist_status(self, item: dict) -> None:
        """run_due ke status badlav ko DB me likho (DB mode)."""
        if self.data_layer is not None:
            self.data_layer.update_reminder_status(
                item["id"], item.get("status", "pending"),
                sent_at=item.get("sent_at"), note=item.get("note"))

    def _add(self, item: dict) -> dict:
        item.setdefault("id", uuid.uuid4().hex[:12])
        item.setdefault("status", "pending")
        item.setdefault("created_at", datetime.now(IST).isoformat(timespec="seconds"))
        if self.data_layer is not None:
            self.data_layer.add_reminder(item)
        else:
            items = self._load_file()
            items.append(item)
            self._save_file(items)
        log.info("Scheduled %s for %s (brand %s) due %s",
                 item["type"], item["user_id"], item["brand_slug"], item.get("due_at"))
        return item

    # ------------------------------------------------------------------
    def schedule_booking_reminder(self, brand_slug: str, brand_name: str, user_id: str,
                                  name: str, service: str, datetime_text: str,
                                  hours_before: float = 1) -> dict:
        appt = parse_appointment(datetime_text)
        item = {
            "type": "reminder",
            "brand_slug": brand_slug,
            "brand_name": brand_name,
            "user_id": user_id,
            "name": name,
            "service": service,
            "appointment_text": datetime_text,
            "message": (
                f"Namaste {name} ji! 🙏 *{brand_name}* se reminder: aapki "
                f"*{service}* ki appointment *{datetime_text}* ko hai. "
                "Kripya samay par aayein. Kuch badlav chahiye to yahi reply karein. Dhanyavaad!"
            ),
        }
        if appt is None:
            item["due_at"] = None
            item["status"] = "unparseable"
            item["note"] = "Appointment time parse nahi hua — owner ko manually reminder bhejna hoga."
        else:
            item["appointment_at"] = appt.isoformat(timespec="seconds")
            item["due_at"] = (appt - timedelta(hours=float(hours_before))).isoformat(timespec="seconds")
        return self._add(item)

    def schedule_follow_up(self, brand_slug: str, brand_name: str, user_id: str,
                           after_hours: float, message_template: str) -> dict:
        due = datetime.now(IST) + timedelta(hours=float(after_hours))
        item = {
            "type": "follow_up",
            "brand_slug": brand_slug,
            "brand_name": brand_name,
            "user_id": user_id,
            "name": "",
            "service": "",
            "due_at": due.isoformat(timespec="seconds"),
            "message": message_template,
        }
        return self._add(item)

    # ------------------------------------------------------------------
    def pending(self) -> list:
        return [i for i in self._load() if i.get("status") == "pending"]

    def run_due(self, send_callback, has_booking=None, now: datetime | None = None) -> dict:
        """Due items bhej do. send_callback(item) -> message bhejta hai.
        has_booking(brand_slug, user_id, since_iso) -> booking ho gayi ho
        to follow-up cancel ho jata hai. Returns {sent, cancelled, failed}."""
        now = now or datetime.now(IST)
        items = self._load()
        stats = {"sent": 0, "cancelled": 0, "failed": 0}
        changed = False
        for item in items:
            if item.get("status") != "pending" or not item.get("due_at"):
                continue
            try:
                due = datetime.fromisoformat(item["due_at"])
            except ValueError:
                continue
            if due.tzinfo is None:
                due = due.replace(tzinfo=IST)
            if due > now:
                continue
            if item["type"] == "follow_up" and has_booking is not None:
                if has_booking(item["brand_slug"], item["user_id"], item.get("created_at", "")):
                    item["status"] = "cancelled"
                    item["note"] = "Customer ne booking kar li — follow-up ki zaroorat nahi."
                    stats["cancelled"] += 1
                    changed = True
                    self._persist_status(item)
                    continue
            try:
                send_callback(item)
                item["status"] = "sent"
                item["sent_at"] = now.isoformat(timespec="seconds")
                stats["sent"] += 1
                self._persist_status(item)
            except Exception as exc:  # ek fail ho to baaki na rukein
                log.warning("scheduler send failed for %s: %s", item.get("id"), exc)
                stats["failed"] += 1
            changed = True
        if changed:
            self._save(items)
        return stats
