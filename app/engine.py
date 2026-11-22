"""Conversation engine for the AKSynxa WhatsApp automation base.

Flow: welcome -> main menu -> branches (services / prices / booking /
FAQs / talk-to-human). All wording comes from the brand config, so the
same engine serves any business — only the YAML changes.

Session state is PERSISTENT: it lives in SQLite (app/store.py), so a
server restart does NOT reset ongoing conversations. Every first
contact and every confirmed booking / human request creates a lead.

Optional hooks (set by main.py) connect the engine to the scheduler:
  hooks["on_new_enquiry"](user_id)          -> lead follow-up schedule
  hooks["on_booking"](user_id, data_dict)   -> booking reminder schedule
"""
import logging

log = logging.getLogger("aksynxa.engine")

RESET_WORDS = {"hi", "hii", "hello", "hey", "namaste", "start", "menu", "back"}
YES_WORDS = {"yes", "haan", "ha", "confirm", "ok", "okay", "ji", "theek hai"}
NO_WORDS = {"no", "nahi", "cancel", "nope", "band karo"}


class Engine:
    def __init__(self, cfg, lead_store, chat_logger=None, session_store=None, hooks=None):
        self.cfg = cfg
        self.leads = lead_store
        self.chat_logger = chat_logger
        self.store = session_store          # SQLite Store ya None (memory fallback)
        self.hooks = hooks or {}
        self._memory = {}                   # sirf tab jab store na diya ho

    # ------------------------------------------------------------------
    # Session persistence — SQLite ho to wahan, warna memory
    # ------------------------------------------------------------------
    def _new_session(self) -> dict:
        return {"stage": "new", "data": {}, "paused": False, "lead_created": False}

    def _get_session(self, user_id: str) -> dict:
        if self.store is not None:
            saved = self.store.get_session(self.cfg.slug, user_id)
            if saved is not None:
                return saved
            return self._new_session()
        return self._memory.setdefault(user_id, self._new_session())

    def _save_session(self, user_id: str, session: dict) -> None:
        if self.store is not None:
            self.store.save_session(self.cfg.slug, user_id, session)
        else:
            self._memory[user_id] = session

    def session_stage(self, user_id: str) -> str:
        s = self._get_session(user_id)
        return s.get("stage", "new")

    def session_name(self, user_id: str) -> str:
        s = self._get_session(user_id)
        return (s.get("data") or {}).get("name", "")

    # ------------------------------------------------------------------
    # Interactive choices — sender inhe asli WhatsApp buttons/list me
    # badalta hai (live mode); demo mode me numbered text hi fallback hai
    # ------------------------------------------------------------------
    def current_choices(self, user_id: str):
        """Agar current stage par user ko options chunne hain, to unki
        list do: {"kind": "buttons"|"list", "options": [{id, title}]}.
        Buttons zyada se zyada 3 — usse zyada par list message."""
        stage = self.session_stage(user_id)
        if stage == "menu":
            opts = [{"id": str(i + 1), "title": item["label"]}
                    for i, item in enumerate(self.cfg.menu)]
            return {"kind": "buttons" if len(opts) <= 3 else "list", "options": opts}
        if stage == "booking_service":
            opts = [{"id": str(i + 1), "title": s["name"]}
                    for i, s in enumerate(self.cfg.services)]
            return {"kind": "buttons" if len(opts) <= 3 else "list", "options": opts}
        if stage == "booking_confirm":
            return {"kind": "buttons", "options": [
                {"id": "yes", "title": "✅ Yes, Confirm"},
                {"id": "no", "title": "❌ Cancel"},
            ]}
        if stage == "faq":
            opts = [{"id": str(i + 1), "title": f["question"]}
                    for i, f in enumerate(self.cfg.faqs)]
            return {"kind": "buttons" if len(opts) <= 3 else "list", "options": opts}
        return None

    # ------------------------------------------------------------------
    # Small text builders (everything brand-specific comes from config)
    # ------------------------------------------------------------------
    def _menu_text(self) -> str:
        lines = []
        for i, item in enumerate(self.cfg.menu, start=1):
            lines.append(f"{i}. {item['label']}")
        return "\n".join(lines)

    def _welcome(self) -> str:
        return f"{self.cfg.render(self.cfg.welcome)}\n\n{self._menu_text()}"

    def _menu_hint(self) -> str:
        return "\n\n— Menu ke liye number chunein, ya 'menu' likhein."

    def _services_text(self, with_prices: bool) -> str:
        lines = []
        for i, s in enumerate(self.cfg.services, start=1):
            if with_prices:
                lines.append(f"{i}. *{s['name']}* — {s['price_label']}")
            else:
                desc = (s.get("description") or "").strip()
                lines.append(f"{i}. *{s['name']}*" + (f"\n   {desc}" if desc else ""))
        return "\n".join(lines)

    def _faq_list_text(self) -> str:
        lines = ["Aksar puche jaane wale sawal:"]
        for i, f in enumerate(self.cfg.faqs, start=1):
            lines.append(f"{i}. {f['question']}")
        lines.append("\nNumber chunein, ya apna sawal seedha likh bhi sakte hain.")
        return "\n".join(lines)

    def _match_faq(self, low: str):
        for f in self.cfg.faqs:
            for kw in f.get("keywords", []):
                if kw.lower() in low:
                    return f
        return None

    # ------------------------------------------------------------------
    # Lead helpers
    # ------------------------------------------------------------------
    def _save_lead(self, session, user_id, stage, interest="", summary=""):
        record = self.leads.save_lead(
            customer_number=user_id,
            name=session["data"].get("name", ""),
            interest=interest,
            stage=stage,
            summary=summary,
        )
        if self.chat_logger is not None:
            try:
                self.chat_logger.log_lead(record)
            except Exception as exc:  # logging must never break the chat
                log.warning("chat logger lead row failed: %s", exc)
        return record

    def _ensure_lead(self, session, user_id):
        if not session["lead_created"]:
            self._save_lead(
                session, user_id, stage="new_enquiry",
                summary=f"First contact with {self.cfg.name} WhatsApp bot",
            )
            session["lead_created"] = True
            hook = self.hooks.get("on_new_enquiry")
            if hook:
                try:
                    hook(user_id)
                except Exception as exc:
                    log.warning("on_new_enquiry hook failed: %s", exc)

    def _owner_alert(self, text: str):
        if self.cfg.owner_alert_number:
            return [{"to": self.cfg.owner_alert_number, "text": text}]
        return []

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    def handle(self, user_id: str, text: str):
        """Process one incoming message. Returns (replies, alerts)."""
        session = self._get_session(user_id)
        replies, alerts = self._dispatch(session, user_id, text)
        self._save_session(user_id, session)
        return replies, alerts

    def _dispatch(self, session, user_id: str, text: str):
        raw = (text or "").strip()
        low = raw.lower()

        # Bot is paused for this user (human has taken over)
        if session["paused"]:
            return [], []

        stage = session["stage"]

        # Global reset back to the main menu (not while collecting booking details)
        if low in RESET_WORDS and stage in ("new", "menu", "faq", "done"):
            session["stage"] = "menu"
            self._ensure_lead(session, user_id)
            return [self._welcome()], []

        if stage == "new":
            session["stage"] = "menu"
            self._ensure_lead(session, user_id)
            return [self._welcome()], []

        if stage == "menu":
            return self._handle_menu(session, user_id, raw, low)
        if stage == "faq":
            return self._handle_faq(session, user_id, raw, low)
        if stage.startswith("booking_"):
            return self._handle_booking(session, user_id, raw, low)

        # Unknown stage — recover to menu
        session["stage"] = "menu"
        return [self.cfg.render(self.cfg.fallback) + self._menu_hint()], []

    # ------------------------------------------------------------------
    # Menu branch
    # ------------------------------------------------------------------
    def _handle_menu(self, session, user_id, raw, low):
        action = None
        # Selection by number
        if low.isdigit():
            idx = int(low) - 1
            if 0 <= idx < len(self.cfg.menu):
                action = self.cfg.menu[idx].get("action", "")
        # Selection by typing the label
        if action is None:
            for item in self.cfg.menu:
                label = item["label"].lower()
                if low == label or label in low:
                    action = item.get("action", "")
                    break

        if action == "services":
            return [f"*{self.cfg.name} — Services*\n\n{self._services_text(False)}" + self._menu_hint()], []
        if action == "prices":
            return [f"*{self.cfg.name} — Prices*\n\n{self._services_text(True)}" + self._menu_hint()], []
        if action == "book":
            if not self.cfg.booking_enabled:
                return ["Abhi booking available nahi hai. Kripya call karke sampark karein." + self._menu_hint()], []
            session["stage"] = "booking_name"
            return [f"{self.cfg.booking_intro}\n\nSabse pehle, aapka *naam* kya hai?"], []
        if action == "faq":
            session["stage"] = "faq"
            return [self._faq_list_text()], []
        if action == "human":
            session["paused"] = True
            session["stage"] = "human"
            self._save_lead(session, user_id, stage="human_requested",
                            summary="Customer asked to talk to a human/owner")
            alerts = self._owner_alert(
                f"🔔 *{self.cfg.name}* — Human request!\n"
                f"Customer: {user_id}\n"
                f"Unhone bot se baat rok kar aapse baat karne ko kaha hai. Kripya isi number par reply karein."
            )
            return [self.cfg.render(self.cfg.human_handoff)], alerts

        # Not a menu choice — try FAQ keywords, else fallback
        faq = self._match_faq(low)
        if faq:
            return [faq["answer"] + self._menu_hint()], []
        return [self.cfg.render(self.cfg.fallback) + self._menu_hint()], []

    # ------------------------------------------------------------------
    # FAQ branch
    # ------------------------------------------------------------------
    def _handle_faq(self, session, user_id, raw, low):
        if low.isdigit():
            idx = int(low) - 1
            if 0 <= idx < len(self.cfg.faqs):
                session["stage"] = "menu"
                return [self.cfg.faqs[idx]["answer"] + self._menu_hint()], []
        faq = self._match_faq(low)
        if faq:
            session["stage"] = "menu"
            return [faq["answer"] + self._menu_hint()], []
        return ["Is sawal ka jawab mere paas nahi hai. Upar diye gaye sawalon me se number chunein, ya 'menu' likh kar wapas jayein."], []

    # ------------------------------------------------------------------
    # Booking branch: name -> service -> date/time -> confirm
    # ------------------------------------------------------------------
    def _handle_booking(self, session, user_id, raw, low):
        stage = session["stage"]
        data = session["data"]

        if stage == "booking_name":
            if len(raw) < 2:
                return ["Kripya apna sahi naam likhein."], []
            data["name"] = raw.title()
            session["stage"] = "booking_service"
            return [
                f"Dhanyavaad, {data['name']} ji! 🙏\n\nKaunsi service ke liye booking karni hai? Number chunein:\n\n"
                f"{self._services_text(True)}"
            ], []

        if stage == "booking_service":
            chosen = None
            if low.isdigit():
                idx = int(low) - 1
                if 0 <= idx < len(self.cfg.services):
                    chosen = self.cfg.services[idx]
            if chosen is None:
                for s in self.cfg.services:
                    if low in s["name"].lower() or s["name"].lower() in low:
                        chosen = s
                        break
            if chosen is None:
                return ["Samajh nahi aaya. Kripya upar wali list se *number* chunein."], []
            data["service"] = chosen["name"]
            session["stage"] = "booking_datetime"
            return [
                f"Bahut badhiya — *{chosen['name']}* select ho gaya.\n\n"
                "Aap kis *din aur time* par aana chahenge?\n"
                "Example: 'kal 5 baje' ya '25 Nov, 11 AM'"
            ], []

        if stage == "booking_datetime":
            if len(raw) < 3:
                return ["Kripya din aur time likhein, jaise 'kal 5 baje'."], []
            data["datetime"] = raw
            session["stage"] = "booking_confirm"
            return [
                "*Booking Summary*\n"
                f"Naam: {data.get('name', '')}\n"
                f"Service: {data.get('service', '')}\n"
                f"Time: {data['datetime']}\n\n"
                "Confirm karne ke liye *YES* likhein, cancel karne ke liye *NO*."
            ], []

        if stage == "booking_confirm":
            if low in YES_WORDS:
                session["stage"] = "menu"
                summary = (
                    f"Booking: {data.get('service', '')} at {data.get('datetime', '')} "
                    f"for {data.get('name', '')}"
                )
                self._save_lead(session, user_id, stage="booking_confirmed",
                                interest=data.get("service", ""), summary=summary)
                hook = self.hooks.get("on_booking")
                if hook:
                    try:
                        hook(user_id, dict(data))
                    except Exception as exc:
                        log.warning("on_booking hook failed: %s", exc)
                alerts = self._owner_alert(
                    f"📅 *{self.cfg.name}* — New Booking!\n"
                    f"Naam: {data.get('name', '')}\n"
                    f"Service: {data.get('service', '')}\n"
                    f"Time: {data.get('datetime', '')}\n"
                    f"Customer number: {user_id}"
                )
                reply = (
                    f"{self.cfg.render(self.cfg.booking_done)}\n\n"
                    f"*Booking Details*\nNaam: {data.get('name', '')}\n"
                    f"Service: {data.get('service', '')}\nTime: {data.get('datetime', '')}"
                    + self._menu_hint()
                )
                return [reply], alerts
            if low in NO_WORDS:
                session["stage"] = "menu"
                return ["Booking cancel kar di gayi hai. Koi baat nahi!" + self._menu_hint()], []
            return ["Kripya *YES* likh kar confirm karein, ya *NO* likh kar cancel karein."], []

        session["stage"] = "menu"
        return [self.cfg.render(self.cfg.fallback) + self._menu_hint()], []
