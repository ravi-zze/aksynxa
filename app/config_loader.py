"""Brand config loader for the AKSynxa WhatsApp automation base.

One engine, many brands. Startup par `brands/` folder ki SAARI YAML
files load hoti hain — har client apni file ke saath ek hi server par
chalta hai. `BRAND_CONFIG` env var sirf DEFAULT brand chunne ke liye
hai (backward compatible); na ho to pehli brand default banti hai.
"""
import os

import yaml

DEFAULT_FOLLOW_UP_MESSAGE = (
    "Namaste! 🙏 *{brand}* se hum bas yaad dila rahe the — aapne kuch der pehle "
    "enquiry ki thi. Kya hum aapki kuch madad kar sakte hain? Booking karni ho "
    "to bas 'menu' likh kar option chunein, hum yahi hain! 😊"
)

REQUIRED_TOP_LEVEL = ["brand", "messages", "menu", "services", "faqs"]
REQUIRED_BRAND_FIELDS = ["name"]
REQUIRED_MESSAGE_FIELDS = ["welcome", "fallback", "human_handoff"]


class BrandConfig:
    """Typed, attribute-style access to a brand YAML config."""

    def __init__(self, raw: dict, source_path: str):
        self._raw = raw
        self.source_path = source_path

        brand = raw["brand"]
        self.name = brand["name"]
        self.tagline = brand.get("tagline", "")
        self.language_style = brand.get("language_style", "hinglish")
        self.city = brand.get("city", "")

        # Slug used for per-brand lead files, e.g. "smilecare-dental-clinic"
        base = os.path.splitext(os.path.basename(source_path))[0]
        self.slug = base.lower().replace(" ", "-")

        messages = raw["messages"]
        self.welcome = messages["welcome"].strip()
        self.fallback = messages["fallback"].strip()
        self.human_handoff = messages["human_handoff"].strip()
        self.booking_done = messages.get(
            "booking_done",
            "Dhanyavaad! Aapki booking confirm ho gayi hai. Hum jald hi confirm karne ke liye sampark karenge. 🙏",
        ).strip()

        self.menu = raw["menu"]            # list of {label, action}
        self.services = raw["services"]    # list of {name, price_label, description}
        self.faqs = raw["faqs"]            # list of {question, keywords, answer}

        contact = raw.get("contact", {})
        self.phone = contact.get("phone", "")
        self.address = contact.get("address", "")
        self.hours = contact.get("hours", "")

        self.owner_alert_number = str(raw.get("owner_alert_number", "")).strip()

        booking = raw.get("booking", {})
        self.booking_enabled = bool(booking.get("enabled", True))
        self.booking_intro = booking.get(
            "intro", "Bilkul! Booking ke liye kuch details chahiye hongi."
        )

        webhook = raw.get("webhook", {})
        self.verify_token_fallback = webhook.get("verify_token", "")

        # Multi-brand routing: Meta webhook har message ke saath us number
        # ka phone_number_id bhejta hai jis par message aaya — usi se pata
        # chalta hai ye kis brand ka customer hai. Sample configs me
        # placeholder hai; asli number jodne par asli ID yahan aayegi.
        self.whatsapp_phone_number_id = str(
            raw.get("whatsapp_phone_number_id")
            or webhook.get("phone_number_id")
            or ""
        ).strip()

        # Reminder engine: booking se kitne ghante pehle reminder jaye
        reminders = raw.get("reminders", {}) or {}
        self.reminders_hours_before = float(reminders.get("hours_before", 1))

        # Lead follow-up: enquiry ke kitne ghante baad (booking na hui ho to)
        follow = raw.get("follow_up", {}) or {}
        self.follow_up_after_hours = follow.get("after_hours", None)
        if self.follow_up_after_hours is not None:
            self.follow_up_after_hours = float(self.follow_up_after_hours)
        self.follow_up_message = str(
            follow.get("message") or DEFAULT_FOLLOW_UP_MESSAGE
        ).strip()

        # Optional per-brand Google Sheet webhook (admin page can save it here;
        # the GOOGLE_SHEET_WEBHOOK_URL env var takes priority when set)
        sheet_cfg = raw.get("google_sheet", {}) or {}
        self.google_sheet_webhook_url = str(sheet_cfg.get("webhook_url", "") or "").strip()

    def menu_actions(self):
        return [item.get("action", "") for item in self.menu]

    def render(self, text: str) -> str:
        """Support the {brand} placeholder in messages — renaming the
        business in /admin instantly updates greetings too."""
        return text.replace("{brand}", self.name)


def _validate(raw: dict, source_path: str) -> None:
    errors = []
    if not isinstance(raw, dict):
        raise ValueError(f"Config {source_path} is not a YAML mapping.")
    for key in REQUIRED_TOP_LEVEL:
        if key not in raw:
            errors.append(f"missing top-level key: '{key}'")
    if "brand" in raw:
        for field in REQUIRED_BRAND_FIELDS:
            if field not in raw["brand"]:
                errors.append(f"missing brand field: 'brand.{field}'")
    if "messages" in raw:
        for field in REQUIRED_MESSAGE_FIELDS:
            if field not in raw["messages"]:
                errors.append(f"missing message field: 'messages.{field}'")
    if "menu" in raw and (not isinstance(raw["menu"], list) or not raw["menu"]):
        errors.append("'menu' must be a non-empty list")
    if "services" in raw and (not isinstance(raw["services"], list) or not raw["services"]):
        errors.append("'services' must be a non-empty list")
    if errors:
        raise ValueError(
            f"Invalid brand config {source_path}:\n  - " + "\n  - ".join(errors)
        )


def load_config(path: str | None = None) -> BrandConfig:
    """Load and validate ONE brand config (path ya BRAND_CONFIG env se)."""
    cfg_path = path or os.environ.get("BRAND_CONFIG", "")
    if not cfg_path:
        raise RuntimeError(
            "BRAND_CONFIG env var is not set. Point it to a YAML file, "
            "e.g. BRAND_CONFIG=brands/demo-dental.yaml"
        )
    if not os.path.isabs(cfg_path):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidate = os.path.join(base_dir, cfg_path)
        if os.path.exists(candidate):
            cfg_path = candidate
    if not os.path.exists(cfg_path):
        raise FileNotFoundError(f"Brand config not found: {cfg_path}")
    with open(cfg_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    _validate(raw, cfg_path)
    return BrandConfig(raw, cfg_path)


def brands_dir() -> str:
    """Brands folder: BRANDS_DIR env, warna <project>/brands."""
    env = os.environ.get("BRANDS_DIR", "").strip()
    if env:
        return env
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "brands")


def load_all_configs(directory: str | None = None) -> dict:
    """brands/ ki saari YAML configs load karo -> {slug: BrandConfig}.

    Ek kharab config pure server ko nahi girayegi — use skip karke
    log me warning aayegi, baaki brands chalte rahenge.
    """
    import logging

    log = logging.getLogger("aksynxa.config")
    directory = directory or brands_dir()
    configs: dict[str, BrandConfig] = {}
    if not os.path.isdir(directory):
        raise FileNotFoundError(f"Brands folder not found: {directory}")
    for fname in sorted(os.listdir(directory)):
        if not (fname.endswith(".yaml") or fname.endswith(".yml")):
            continue
        path = os.path.join(directory, fname)
        try:
            cfg = load_config(path)
            configs[cfg.slug] = cfg
            log.info("Brand loaded: %s (%s)", cfg.name, cfg.slug)
        except Exception as exc:
            log.warning("Brand config skip hui %s: %s", fname, exc)
    if not configs:
        raise RuntimeError(f"Ek bhi valid brand config nahi mili: {directory}")
    return configs
