"""AKSynxa V3.1 — business-user authentication helpers.

Password hashing sirf stdlib se: hashlib.pbkdf2_hmac (SHA-256, per-user
random salt, 200k iterations). Koi nayi dependency nahi.

Format:  pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>

Users app/data_layer.py ki `users` table me rehte hain — har user ek
brand (tenant) se bandha hai, role 'owner' ya 'staff'. Session cookie
main.py me banti hai (admin login jaisi hi HMAC-signed style).
"""
import hashlib
import hmac
import os

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 200_000
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("password khali nahi ho sakta")
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verify. Kharab format par False (kabhi crash nahi)."""
    try:
        algo, iters, salt_hex, hash_hex = (stored or "").split("$")
        if algo != _ALGO:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", (password or "").encode("utf-8"),
            bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def create_business_user(data_layer, brand_slug: str, email: str,
                         password: str, role: str = "owner") -> dict:
    """Naya business user banao (password yahi hash hota hai).
    Duplicate email / galat role par ValueError (data layer se)."""
    return data_layer.create_user(
        brand_slug=brand_slug, email=email,
        password_hash=hash_password(password), role=role)


def authenticate(data_layer, email: str, password: str):
    """Email + password sahi ho to user dict, warna None."""
    user = data_layer.get_user_by_email(email)
    if user is None:
        return None
    if not verify_password(password, user.get("password_hash", "")):
        return None
    return user


def change_password(data_layer, user_id, new_password: str) -> None:
    data_layer.set_user_password(user_id, hash_password(new_password))
