"""Lead storage — V3.1: DATABASE is the source of truth.

Har meaningful event (new enquiry, booking confirmed, human requested)
ek lead record banata hai. Record pehle data layer (SQLite local /
PostgreSQL jab DATABASE_URL set ho) me jata hai — reads (dashboard,
follow-up check) hamesha DB se aate hain. Uske saath purane
leads_<brand-slug>.json / .csv files ek export mirror ke roop me
likhte rehte hain (koi purana tool un par nirbhar ho to na toote).

Har record me brand slug hota hai — data layer har query ko tenant
(brand_slug) se filter karta hai.
"""
import csv
import json
import logging
import os
from datetime import datetime, timezone

from app.store import default_data_dir

log = logging.getLogger("aksynxa.leads")

FIELDS = ["timestamp", "brand", "customer_number", "name", "interest", "stage", "summary"]


class LeadStore:
    def __init__(self, brand_slug: str, brand_name: str, data_dir: str | None = None,
                 data_layer=None):
        if data_dir is None:
            data_dir = default_data_dir()
        os.makedirs(data_dir, exist_ok=True)
        self.brand_slug = brand_slug
        self.brand_name = brand_name
        self.data_layer = data_layer
        self.json_path = os.path.join(data_dir, f"leads_{brand_slug}.json")
        self.csv_path = os.path.join(data_dir, f"leads_{brand_slug}.csv")

    def save_lead(self, customer_number: str, name: str = "", interest: str = "",
                  stage: str = "new_enquiry", summary: str = "") -> dict:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "brand": self.brand_name,
            "customer_number": customer_number,
            "name": name,
            "interest": interest,
            "stage": stage,
            "summary": summary,
        }
        # 1) DATABASE — source of truth (fail ho to chat nahi rukegi,
        #    file mirror neeche backup hai)
        if self.data_layer is not None:
            try:
                self.data_layer.add_lead(self.brand_slug, record)
            except Exception as exc:
                log.warning("lead DB write failed (brand %s): %s",
                            self.brand_slug, exc)
        # 2) File mirror — JSON list + CSV append (purana behaviour)
        try:
            records = []
            if os.path.exists(self.json_path):
                try:
                    with open(self.json_path, "r", encoding="utf-8") as fh:
                        records = json.load(fh)
                except (json.JSONDecodeError, OSError):
                    records = []
            records.append(record)
            with open(self.json_path, "w", encoding="utf-8") as fh:
                json.dump(records, fh, ensure_ascii=False, indent=2)

            write_header = not os.path.exists(self.csv_path)
            with open(self.csv_path, "a", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=FIELDS)
                if write_header:
                    writer.writeheader()
                writer.writerow(record)
        except OSError as exc:
            log.warning("lead file mirror failed (brand %s): %s",
                        self.brand_slug, exc)
        return record

    # ------------------------------------------------------------------
    # Dashboard / scheduler ke liye read helpers — DB se (source of truth)
    # ------------------------------------------------------------------
    def all_leads(self) -> list:
        if self.data_layer is not None:
            try:
                return self.data_layer.list_leads(self.brand_slug)
            except Exception as exc:
                log.warning("lead DB read failed (brand %s): %s",
                            self.brand_slug, exc)
        # Fallback: data layer na ho (purane standalone uses) to file se
        if os.path.exists(self.json_path):
            try:
                with open(self.json_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                return data if isinstance(data, list) else []
            except (json.JSONDecodeError, OSError):
                return []
        return []

    def has_booking_after(self, customer_number: str, since_iso: str = "") -> bool:
        """Is customer ki `since_iso` ke baad ki confirmed booking hai?
        Follow-up engine isse decide karta hai ki follow-up bhejna hai
        ya cancel karna hai. Timestamps alag timezones me ho sakte hain
        (leads UTC, scheduler IST) — isliye parse karke compare karte hain."""
        def _parse(ts):
            try:
                dt = datetime.fromisoformat(ts)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                return None

        since = _parse(since_iso) if since_iso else None
        for lead in self.all_leads():
            if (lead.get("customer_number") == customer_number
                    and lead.get("stage") == "booking_confirmed"):
                ts = _parse(lead.get("timestamp", ""))
                if since is None or (ts is not None and ts >= since):
                    return True
        return False
