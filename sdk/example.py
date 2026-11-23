#!/usr/bin/env python3
"""AKSynxa SDK example — apne business ka haal ek script me.

Pehle env set karo:
    export AKSYNXA_BASE_URL="https://aksynxa-api.onrender.com"
    export AKSYNXA_API_KEY="aks_..."      # scripts/create_api_key.py se

Phir:  python sdk/example.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aksynxa_client import AKSynxaAPIError, AKSynxaClient  # noqa: E402


def main() -> int:
    base = os.environ.get("AKSYNXA_BASE_URL", "http://localhost:8000")
    key = os.environ.get("AKSYNXA_API_KEY", "")
    if not key:
        print("❌ AKSYNXA_API_KEY env set karo (scripts/create_api_key.py se banti hai)")
        return 2

    with AKSynxaClient(base, key) as client:
        try:
            stats = client.stats()
            print(f"🏪 {stats['brand_name']} ({stats['brand']})")
            print(f"   Leads: {stats['total_leads']} | Bookings: {stats['bookings']} "
                  f"| Pending reminders: {stats['pending_reminders']}")

            bookings = client.bookings(limit=3)
            print(f"\n📅 Recent bookings ({bookings['count']}):")
            for b in bookings["bookings"]:
                print(f"   - {b.get('name') or '—'}: {b.get('summary', '')}")

            reply = client.simulate("919999999999", "hi")
            print("\n🤖 Bot test ('hi' bheja):")
            for line in reply["replies"]:
                print("   " + line.replace("\n", "\n   "))
        except AKSynxaAPIError as exc:
            print(f"❌ {exc}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
