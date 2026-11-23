"""AKSynxa V3.2 — Setup Wizard (/setup).

Business owner ke liye step-by-step easy form — ek screen par ek hi
cheez, bade inputs, Hinglish labels:

  Step 1: Business ka naam + type
  Step 2: Services aur prices (rows add/remove)
  Step 3: WhatsApp number + khulne/band hone ka time
  Done:   Draft save + aage kya hoga

HONEST NOTE: brand configs abhi file-based YAML hain (brands/*.yaml),
isliye wizard live config NAHI badalta — ye ek **draft config JSON**
save karta hai `<data_dir>/setup_drafts/<slug>.json` me. AKSynxa team
usi draft se asli brand config banakar bot live karti hai. UI aur ye
docstring dono me ye saaf likha hai — koi fake "live ho gaya" nahi.
"""
import json
import logging
import os
import re
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

log = logging.getLogger("aksynxa.wizard")

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "templates")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "business"


class SetupDraftIn(BaseModel):
    business_name: str
    business_type: str = ""
    owner_name: str = ""
    services: list = []
    whatsapp_number: str = ""
    open_time: str = ""
    close_time: str = ""
    weekly_off: str = ""


def build_wizard_router(data_dir: str) -> APIRouter:
    router = APIRouter()
    drafts_dir = os.path.join(data_dir, "setup_drafts")

    @router.get("/setup", response_class=HTMLResponse,
                tags=["Setup Wizard"],
                summary="Business owner ke liye step-by-step setup form")
    def setup_page():
        with open(os.path.join(TEMPLATES_DIR, "setup.html"),
                  encoding="utf-8") as fh:
            return fh.read()

    @router.post("/setup/draft", tags=["Setup Wizard"],
                 summary="Setup draft save karo (live config nahi — draft JSON)")
    def setup_save_draft(payload: SetupDraftIn):
        name = payload.business_name.strip()
        if len(name) < 2:
            return JSONResponse(
                {"ok": False, "error": "Business ka naam to likho"},
                status_code=400)
        number = "".join(ch for ch in payload.whatsapp_number if ch.isdigit())
        if number and len(number) < 10:
            return JSONResponse(
                {"ok": False,
                 "error": "WhatsApp number sahi nahi lag raha (10+ digits)"},
                status_code=400)
        services = [{"name": str(s.get("name", "")).strip(),
                     "price": str(s.get("price", "")).strip()}
                    for s in payload.services
                    if isinstance(s, dict) and str(s.get("name", "")).strip()]
        slug = _slugify(name)
        draft = {
            "status": "draft",
            "saved_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            "brand_slug_suggestion": slug,
            "business_name": name,
            "business_type": payload.business_type.strip(),
            "owner_name": payload.owner_name.strip(),
            "services": services,
            "whatsapp_number": number,
            "open_time": payload.open_time.strip(),
            "close_time": payload.close_time.strip(),
            "weekly_off": payload.weekly_off.strip(),
            "next_step": ("AKSynxa team is draft se brands/<slug>.yaml "
                          "banakar bot live karegi"),
        }
        os.makedirs(drafts_dir, exist_ok=True)
        path = os.path.join(drafts_dir, f"{slug}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(draft, fh, ensure_ascii=False, indent=2)
        log.info("Setup draft saved: %s", path)
        return {"ok": True, "slug": slug, "draft_file": f"{slug}.json",
                "message": "Draft save ho gaya!"}

    return router
