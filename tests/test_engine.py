"""Engine + scheduler + store tests — dono sample brands ke core flows.

Sab kuch isolated tmp folders me chalta hai: asli data/ folder aur
brand YAMLs ko haath nahi lagta. Koi network, koi asli WhatsApp nahi.
"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from zoneinfo import ZoneInfo

from app.config_loader import load_all_configs, load_config
from app.engine import Engine
from app.leads import LeadStore
from app.scheduler import Scheduler, parse_appointment
from app.sheets import ChatLogger
from app.store import Store

IST = ZoneInfo("Asia/Kolkata")
PROJECT = Path(__file__).resolve().parents[1]
USER = "919999900001"


def make_engine(config_file: str, tmp_path, store=None, hooks=None):
    cfg = load_config(str(PROJECT / "brands" / config_file))
    store = store or Store(db_path=str(tmp_path / "sessions.db"))
    leads = LeadStore(cfg.slug, cfg.name, data_dir=str(tmp_path))
    sheets = ChatLogger(cfg, data_dir=str(tmp_path))
    engine = Engine(cfg, leads, sheets, session_store=store, hooks=hooks)
    return engine, cfg, leads


def chat(engine, user, *texts):
    """Kai messages ek ke baad ek; aakhri ka reply return karo."""
    replies, alerts = [], []
    for t in texts:
        replies, alerts = engine.handle(user, t)
    return replies, alerts


# ---------------------------------------------------------------------------
# SmileCare Dental — core flows
# ---------------------------------------------------------------------------
def test_dental_greeting_and_menu(tmp_path):
    engine, cfg, _ = make_engine("demo-dental.yaml", tmp_path)
    replies, _ = chat(engine, USER, "hi")
    assert "SmileCare Dental Clinic" in replies[0]
    assert "Book Appointment" in replies[0]
    assert engine.session_stage(USER) == "menu"
    # Menu ke saath interactive choices bhi aane chahiye (list, 5 options)
    choices = engine.current_choices(USER)
    assert choices and choices["kind"] == "list" and len(choices["options"]) == 5


def test_dental_prices(tmp_path):
    engine, _, _ = make_engine("demo-dental.yaml", tmp_path)
    replies, _ = chat(engine, USER, "hi", "2")
    assert "₹999" in replies[0] and "Dental Cleaning" in replies[0]


def test_dental_faq_timing(tmp_path):
    engine, _, _ = make_engine("demo-dental.yaml", tmp_path)
    replies, _ = chat(engine, USER, "hi", "clinic ki timing kya hai")
    assert "10:00 AM" in replies[0]


def test_dental_booking_full_flow(tmp_path):
    booked = {}
    hooks = {"on_booking": lambda uid, data: booked.update(data)}
    engine, _, leads = make_engine("demo-dental.yaml", tmp_path, hooks=hooks)
    chat(engine, USER, "hi", "3")
    replies, _ = chat(engine, USER, "Rahul Sharma")
    assert "Rahul Sharma" in replies[0]
    chat(engine, USER, "1")
    replies, _ = chat(engine, USER, "kal 5 baje")
    assert "Booking Summary" in replies[0]
    # Confirm stage par Yes/No buttons milne chahiye
    choices = engine.current_choices(USER)
    assert choices and choices["kind"] == "buttons"
    replies, alerts = chat(engine, USER, "yes")
    assert "confirm ho gayi" in replies[0]
    assert alerts and "New Booking" in alerts[0]["text"]
    stages = [l["stage"] for l in leads.all_leads()]
    assert "new_enquiry" in stages and "booking_confirmed" in stages
    lead = [l for l in leads.all_leads() if l["stage"] == "booking_confirmed"][0]
    assert lead["name"] == "Rahul Sharma"
    assert lead["interest"] == "Dental Cleaning & Polishing"
    # Reminder hook fire hua hoga
    assert booked.get("service") == "Dental Cleaning & Polishing"
    assert booked.get("datetime") == "kal 5 baje"


def test_dental_human_handoff_pauses_bot(tmp_path):
    engine, _, leads = make_engine("demo-dental.yaml", tmp_path)
    replies, alerts = chat(engine, USER, "hi", "5")
    assert engine.session_stage(USER) == "human"
    assert alerts  # owner ko alert gaya
    replies2, _ = chat(engine, USER, "hello?")
    assert replies2 == []  # paused — bot chup hai
    assert "human_requested" in [l["stage"] for l in leads.all_leads()]


# ---------------------------------------------------------------------------
# AKSynxa — core flows
# ---------------------------------------------------------------------------
def test_aksynxa_greeting_pricing_pilot(tmp_path):
    engine, _, _ = make_engine("aksynxa.yaml", tmp_path)
    replies, _ = chat(engine, USER, "hi")
    assert "AKSynxa" in replies[0]
    replies, _ = chat(engine, USER, "2")
    assert "₹9,999" in replies[0] and "₹4,999" in replies[0]
    replies, _ = chat(engine, USER, "free pilot kaise milega")
    assert "7 din" in replies[0] and "FREE" in replies[0]


# ---------------------------------------------------------------------------
# Multi-brand — dono ek saath, sessions alag-alag
# ---------------------------------------------------------------------------
def test_multi_brand_simultaneous(tmp_path):
    configs = load_all_configs(str(PROJECT / "brands"))
    assert set(configs) == {"demo-dental", "aksynxa"}
    shared_store = Store(db_path=str(tmp_path / "shared.db"))
    engines = {}
    for slug, cfg in configs.items():
        leads = LeadStore(cfg.slug, cfg.name, data_dir=str(tmp_path))
        sheets = ChatLogger(cfg, data_dir=str(tmp_path))
        engines[slug] = Engine(cfg, leads, sheets, session_store=shared_store)

    user = "919999900777"
    r_dental, _ = engines["demo-dental"].handle(user, "hi")
    r_aks, _ = engines["aksynxa"].handle(user, "hi")
    assert "SmileCare Dental Clinic" in r_dental[0]
    assert "AKSynxa" in r_aks[0] and "SmileCare" not in r_aks[0]

    # Dental par booking aadhi karo — AKSynxa session par asar nahi
    engines["demo-dental"].handle(user, "3")
    assert engines["demo-dental"].session_stage(user) == "booking_name"
    assert engines["aksynxa"].session_stage(user) == "menu"


# ---------------------------------------------------------------------------
# Session persistence — restart ke baad bhi conversation continue
# ---------------------------------------------------------------------------
def test_session_survives_restart(tmp_path):
    db = str(tmp_path / "sessions.db")
    engine1, _, _ = make_engine("demo-dental.yaml", tmp_path,
                                store=Store(db_path=db))
    chat(engine1, USER, "hi", "3", "Priya Verma")
    assert engine1.session_stage(USER) == "booking_service"

    # "Restart": bilkul naya Engine + naya Store, wahi DB file
    engine2, _, leads2 = make_engine("demo-dental.yaml", tmp_path,
                                     store=Store(db_path=db))
    assert engine2.session_stage(USER) == "booking_service"
    assert engine2.session_name(USER) == "Priya Verma"
    replies, _ = chat(engine2, USER, "2", "kal 11 baje", "yes")
    assert "confirm ho gayi" in replies[0]
    assert "booking_confirmed" in [l["stage"] for l in leads2.all_leads()]


# ---------------------------------------------------------------------------
# Store dedupe
# ---------------------------------------------------------------------------
def test_processed_message_dedupe(tmp_path):
    store = Store(db_path=str(tmp_path / "s.db"))
    assert not store.is_processed("wamid.1")
    store.mark_processed("wamid.1", "demo-dental")
    assert store.is_processed("wamid.1")
    assert not store.is_processed("wamid.2")


# ---------------------------------------------------------------------------
# Scheduler — time parsing, reminder, follow-up
# ---------------------------------------------------------------------------
def test_parse_appointment_hinglish():
    now = datetime(2026, 11, 22, 10, 0, tzinfo=IST)  # Sunday
    appt = parse_appointment("kal 5 baje", now)
    assert appt == datetime(2026, 11, 23, 17, 0, tzinfo=IST)
    appt = parse_appointment("25 Nov, 11 AM", now)
    assert appt == datetime(2026, 11, 25, 11, 0, tzinfo=IST)
    appt = parse_appointment("aaj 4:30 pm", now)
    assert appt == datetime(2026, 11, 22, 16, 30, tzinfo=IST)
    assert parse_appointment("kabhi bhi aa jaunga", now) is None


def test_booking_reminder_scheduled_and_sent(tmp_path):
    sched = Scheduler(str(tmp_path))
    item = sched.schedule_booking_reminder(
        brand_slug="demo-dental", brand_name="SmileCare Dental Clinic",
        user_id=USER, name="Rahul Sharma", service="Dental Cleaning & Polishing",
        datetime_text="kal 5 baje", hours_before=1)
    assert item["status"] == "pending"
    appt = datetime.fromisoformat(item["appointment_at"])
    due = datetime.fromisoformat(item["due_at"])
    assert due == appt - timedelta(hours=1)

    sent = []
    stats = sched.run_due(lambda it: sent.append(it),
                          now=due + timedelta(minutes=1))
    assert stats["sent"] == 1 and sent[0]["user_id"] == USER
    assert sched.pending() == []  # bheja hua pending nahi rehta


def test_follow_up_cancelled_when_booked(tmp_path):
    sched = Scheduler(str(tmp_path))
    item = sched.schedule_follow_up(
        brand_slug="demo-dental", brand_name="SmileCare",
        user_id=USER, after_hours=0, message_template="follow up!")
    sent = []
    stats = sched.run_due(lambda it: sent.append(it),
                          has_booking=lambda b, u, s: True,
                          now=datetime.now(IST) + timedelta(minutes=1))
    assert stats["cancelled"] == 1 and sent == []

    # Booking na hui ho to follow-up jata hai — sirf ek baar
    sched2 = Scheduler(str(tmp_path / "b"))
    sched2.schedule_follow_up(brand_slug="demo-dental", brand_name="SmileCare",
                              user_id=USER, after_hours=0, message_template="fu")
    stats = sched2.run_due(lambda it: sent.append(it),
                           has_booking=lambda b, u, s: False,
                           now=datetime.now(IST) + timedelta(minutes=1))
    assert stats["sent"] == 1
    stats = sched2.run_due(lambda it: sent.append(it),
                           has_booking=lambda b, u, s: False,
                           now=datetime.now(IST) + timedelta(hours=2))
    assert stats["sent"] == 0  # dobara nahi gaya
