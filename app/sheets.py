"""Google Sheets chat logger — EVERY chat turn lands in a Sheet.

One row per message (in + out), brand-wise. Lead events get an extra row
whose Stage column is marked "LEAD: <stage>", so leads are easy to filter.

Connection methods (both optional, both fail-soft):
  A) GOOGLE_SHEET_WEBHOOK_URL — Apps Script Web App URL; each row is
     POSTed as JSON. Simplest, no libraries needed. (Or set
     google_sheet.webhook_url in the brand YAML.)
  B) gspread + service account — GOOGLE_SERVICE_ACCOUNT_JSON (file path
     or raw JSON) + GOOGLE_SHEET_ID. gspread is optional: if it is not
     installed, this method quietly disables itself.

Always-on fallback: every row is also appended to local files
data/chatlog_<brand>.json and .csv, and when no remote method is
configured the logger runs in DRY-RUN mode and prints each row to the
server log. A Sheets failure NEVER breaks the chat.

Reliability: remote send fail ho jaye to row `data/sheets_retry.json`
queue me chali jati hai. Background scheduler (main.py) har 10 minute
(SHEETS_RETRY_SECONDS se badal sakte hain) dobara koshish karta hai.
3 baar fail hone ke baad row queue se hat jati hai — local CSV hi
final record rehta hai (wo hamesha likha jata hai).
"""
import csv
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import requests

from app.store import default_data_dir

try:
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # very old Python without zoneinfo data
    IST = timezone(timedelta(hours=5, minutes=30))

log = logging.getLogger("aksynxa.sheets")

HEADERS = ["Timestamp", "Brand", "Customer Number", "Name", "Direction", "Message", "Stage"]
CSV_FIELDS = ["timestamp", "brand", "customer_number", "name", "direction", "message", "stage"]


class ChatLogger:
    def __init__(self, cfg, data_dir: str | None = None, data_layer=None):
        self.brand_name = cfg.name
        self.slug = cfg.slug
        self.data_layer = data_layer
        # Method A: env wins, brand YAML is the fallback (admin page saves there)
        self.webhook_url = (
            os.environ.get("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
            or getattr(cfg, "google_sheet_webhook_url", "")
        )
        # Method B state (lazy)
        self._gspread_ws = None
        self._gspread_tried = False

        if data_dir is None:
            data_dir = default_data_dir()
        os.makedirs(data_dir, exist_ok=True)
        self.data_dir = data_dir
        self.json_path = os.path.join(data_dir, f"chatlog_{self.slug}.json")
        self.csv_path = os.path.join(data_dir, f"chatlog_{self.slug}.csv")
        # Saari brands ek hi retry queue share karti hain (har row me
        # brand slug hota hai, retry sahi logger se hota hai)
        self.retry_path = os.path.join(data_dir, "sheets_retry.json")

    # ------------------------------------------------------------------
    def status(self) -> dict:
        return {
            "webhook_configured": bool(self.webhook_url),
            "gspread_configured": bool(
                os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
                and os.environ.get("GOOGLE_SHEET_ID")
            ),
            "local_log": self.csv_path,
            "mode": "webhook" if self.webhook_url else "dry-run/local",
        }

    def _now_ist(self) -> str:
        return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")

    # ------------------------------------------------------------------
    # Local always-on log (JSON list + CSV append)
    # ------------------------------------------------------------------
    def _append_local(self, row: dict) -> None:
        records = []
        if os.path.exists(self.json_path):
            try:
                with open(self.json_path, "r", encoding="utf-8") as fh:
                    records = json.load(fh)
            except (json.JSONDecodeError, OSError):
                records = []
        records.append(row)
        with open(self.json_path, "w", encoding="utf-8") as fh:
            json.dump(records, fh, ensure_ascii=False, indent=2)

        write_header = not os.path.exists(self.csv_path)
        with open(self.csv_path, "a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            if write_header:
                writer.writeheader()
            writer.writerow(row)

    # ------------------------------------------------------------------
    # Remote sending (Method A webhook, else Method B gspread)
    # ------------------------------------------------------------------
    def _get_gspread_worksheet(self):
        if self._gspread_tried:
            return self._gspread_ws
        self._gspread_tried = True
        sa = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        sheet_id = os.environ.get("GOOGLE_SHEET_ID", "").strip()
        if not sa or not sheet_id:
            return None
        try:
            import gspread  # optional dependency — guarded on purpose

            if os.path.exists(sa):
                client = gspread.service_account(filename=sa)
            else:
                client = gspread.service_account_from_dict(json.loads(sa))
            self._gspread_ws = client.open_by_key(sheet_id).sheet1
        except Exception as exc:
            log.warning("gspread setup failed (continuing without it): %s", exc)
            self._gspread_ws = None
        return self._gspread_ws

    def _send_remote(self, row: dict) -> str:
        """Returns 'webhook', 'gspread' or 'dry-run'. Raises on remote errors."""
        if self.webhook_url:
            resp = requests.post(self.webhook_url, json=row, timeout=5)
            resp.raise_for_status()
            return "webhook"
        ws = self._get_gspread_worksheet()
        if ws is not None:
            ws.append_row([row[k] for k in CSV_FIELDS], value_input_option="USER_ENTERED")
            return "gspread"
        return "dry-run"

    # ------------------------------------------------------------------
    # Retry queue — fail hui rows yahan, scheduler dobara bhejta hai
    # ------------------------------------------------------------------
    MAX_RETRY_ATTEMPTS = 3

    def _load_retry_queue(self) -> list:
        if os.path.exists(self.retry_path):
            try:
                with open(self.retry_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                return data if isinstance(data, list) else []
            except (json.JSONDecodeError, OSError):
                return []
        return []

    def _save_retry_queue(self, queue: list) -> None:
        with open(self.retry_path, "w", encoding="utf-8") as fh:
            json.dump(queue, fh, ensure_ascii=False, indent=2)

    def _enqueue_retry(self, row: dict) -> None:
        try:
            queue = self._load_retry_queue()
            queue.append({"brand_slug": self.slug, "row": row, "attempts": 0})
            self._save_retry_queue(queue)
            log.info("Sheet row retry queue me gayi (brand %s)", self.slug)
        except OSError as exc:
            log.warning("retry queue likh nahi paye: %s", exc)

    def process_retry_queue(self) -> dict:
        """Is brand ki queued rows dobara bhejne ki koshish karo.
        3 fail ke baad row drop — local CSV final record hai."""
        queue = self._load_retry_queue()
        mine = [q for q in queue if q.get("brand_slug") == self.slug]
        if not mine:
            return {"retried": 0, "sent": 0, "dropped": 0}
        stats = {"retried": 0, "sent": 0, "dropped": 0}
        remaining = [q for q in queue if q.get("brand_slug") != self.slug]
        for entry in mine:
            stats["retried"] += 1
            try:
                method = self._send_remote(entry["row"])
                if method == "dry-run":
                    # Remote ab bhi configure nahi — queue me rehne do
                    remaining.append(entry)
                else:
                    stats["sent"] += 1
            except Exception as exc:
                entry["attempts"] = int(entry.get("attempts", 0)) + 1
                if entry["attempts"] >= self.MAX_RETRY_ATTEMPTS:
                    stats["dropped"] += 1
                    log.warning("Sheet row %d attempts ke baad drop (local CSV final): %s",
                                entry["attempts"], exc)
                else:
                    remaining.append(entry)
        try:
            self._save_retry_queue(remaining)
        except OSError as exc:
            log.warning("retry queue save failed: %s", exc)
        return stats

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def _log_row(self, customer_number, name, direction, message, stage) -> dict:
        row = {
            "timestamp": self._now_ist(),
            "brand": self.brand_name,
            "customer_number": customer_number,
            "name": name or "",
            "direction": direction,
            "message": message,
            "stage": stage,
        }
        # 1) Local file mirror (hamesha — purana behaviour, final fallback)
        try:
            self._append_local(row)
        except OSError as exc:
            log.warning("local chat log failed: %s", exc)
        # 2) DATABASE — V3.1 source of truth (dashboard yahi se padhta hai)
        if self.data_layer is not None:
            try:
                self.data_layer.add_chat_turn(self.slug, row)
            except Exception as exc:
                log.warning("chat turn DB write failed (brand %s): %s",
                            self.slug, exc)
        # 3) Remote Google Sheet (optional, fail-soft + retry queue)
        method = "dry-run"
        try:
            method = self._send_remote(row)
        except Exception as exc:  # Sheets must never break the chat
            log.warning("sheet send failed — row retry queue me: %s", exc)
            self._enqueue_retry(row)
            method = "queued"
        if method == "dry-run":
            log.info("[SHEETS DRY-RUN] %s", json.dumps(row, ensure_ascii=False))
        return {"logged": True, "method": method, "row": row}

    def all_rows(self) -> list:
        """Is brand ki saari chat rows (dashboard ke liye) — V3.1 me
        DATABASE se (source of truth, tenant-filtered). Data layer na
        ho to purani local file se."""
        if self.data_layer is not None:
            try:
                return self.data_layer.list_chat_turns(self.slug)
            except Exception as exc:
                log.warning("chat turns DB read failed (brand %s): %s",
                            self.slug, exc)
        if os.path.exists(self.json_path):
            try:
                with open(self.json_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                return data if isinstance(data, list) else []
            except (json.JSONDecodeError, OSError):
                return []
        return []

    def log_turn(self, customer_number: str, name: str, direction: str,
                 message: str, stage: str) -> dict:
        """Log one conversation turn (direction: 'in' or 'out')."""
        return self._log_row(customer_number, name, direction, message, stage)

    def log_lead(self, lead_record: dict) -> dict:
        """Log a lead event as its own row, marked in the Stage column."""
        stage = f"LEAD: {lead_record.get('stage', '')}"
        message = lead_record.get("summary") or lead_record.get("interest") or "New lead"
        return self._log_row(
            lead_record.get("customer_number", ""),
            lead_record.get("name", ""),
            "lead",
            message,
            stage,
        )
