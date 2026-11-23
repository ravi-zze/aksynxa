"""AKSynxa V3.2 — per-brand API keys (public REST API + embed token).

Key format: `aks_<brand-slug ka hint nahi, sirf random>` — plaintext
SIRF banate waqt ek baar dikhti hai; DB me sirf SHA-256 hash jata hai
(data_layer.api_keys). Key jis brand ki hai, API sirf usi brand ka
data degi — yahi tenant isolation ki chaabi hai.
"""
import hashlib
import hmac
import secrets

KEY_PREFIX = "aks_"


def generate_api_key() -> str:
    """Nayi random API key (plaintext — ek hi baar milegi, sambhalo)."""
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256((plaintext or "").strip().encode()).hexdigest()


def create_api_key(data_layer, brand_slug: str, label: str = "") -> tuple:
    """Key banao aur DB me hash store karo. Returns (row, plaintext).
    Plaintext caller ko SIRF creation ke time dikhana hai."""
    plaintext = generate_api_key()
    row = data_layer.create_api_key_record(
        brand_slug, hash_api_key(plaintext), label)
    return row, plaintext


def verify_api_key(data_layer, plaintext: str):
    """Plaintext key -> api_keys row (revoked na ho to), warna None."""
    if not plaintext:
        return None
    row = data_layer.get_api_key_by_hash(hash_api_key(plaintext))
    if row is None or row.get("revoked"):
        return None
    return row


def key_matches(plaintext: str, key_hash: str) -> bool:
    return hmac.compare_digest(hash_api_key(plaintext), key_hash or "")
