#!/usr/bin/env python3
"""AKSynxa V3.1 — business user banao (owner/staff) kisi brand ke liye.

Usage (project root se):
  python scripts/create_user.py --brand demo-dental \
      --email owner@smilecare.example --password 'StrongPass123' --role owner

  # password na do to script khud poochhega (screen par nahi dikhega)
  python scripts/create_user.py --brand aksynxa --email team@aksynxa.in --role staff

Pehla OWNER har brand ke liye isi script se banta hai. Uske baad:
  * Owner login karke POST /dashboard/users se apni team (staff) bana sakta hai.
  * Super-admin (ADMIN_PASSWORD) POST /admin/users se kisi bhi brand ka
    user bana sakta hai.

Env: DATABASE_URL set ho to user PostgreSQL me jayega, warna local
SQLite (AKSYNXA_DATA_DIR / data/aksynxa.db). Wahi env rakho jo server
chalate waqt rakha tha — warna user galat DB me ban jayega!
"""
import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import auth as auth_mod          # noqa: E402
from app.data_layer import create_data_layer  # noqa: E402
from app.store import default_data_dir    # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="AKSynxa business user banao")
    ap.add_argument("--brand", required=True, help="brand slug, e.g. demo-dental")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", default="", help="na do to prompt aayega")
    ap.add_argument("--role", default="owner", choices=["owner", "staff"])
    args = ap.parse_args()

    password = args.password or getpass.getpass("Password (min 8 chars): ")
    if len(password) < 8:
        print("❌ Password kam se kam 8 characters ka hona chahiye.")
        return 2

    data = create_data_layer(os.environ.get("DATABASE_URL", "").strip(),
                             default_data_dir())
    data.ensure_organization(args.brand, "")
    try:
        user = auth_mod.create_business_user(
            data, args.brand, args.email, password, args.role)
    except ValueError as exc:
        print(f"❌ {exc}")
        return 1
    print(f"✅ User ban gaya: id={user['id']} brand={user['brand_slug']} "
          f"email={user['email']} role={user['role']}")
    print("   Login: /business/login (server chalne par)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
