"""Donation-ware entitlement.

Reframed from "premium": the app is free and never limits good research. Users who reach a
high, verified success rate are invited to donate from a small amount; a donating DEVICE
gets a signed licence that unlocks scale/convenience features.

The licence is an Ed25519 signature — issued only with the author's private key — over
{device_id, tier, issued_at}. The app ships only the PUBLIC key and verifies OFFLINE, so a
licence cannot be forged by editing config. Verification is intentionally woven through the
app (not one removable flag), and this module is the shared checker.

Honest note (also shown in the UI): any purely local gate can ultimately be bypassed by a
user who controls the machine. The model therefore leans on goodwill — small ask, only
after verified success — rather than hard DRM.
"""

from __future__ import annotations

import base64
import json
import time
from functools import lru_cache

from app.core.config import get_settings
from app.core.device import device_id

# The author's Ed25519 PUBLIC key (hex). Replace with your real key before distributing;
# until then no licence verifies and every device is on the free tier (which is fine —
# free is fully capable).
PUBLIC_KEY_HEX = "0000000000000000000000000000000000000000000000000000000000000000"

DONATE_URL = "https://ko-fi.com/"      # replace with your donation link
MIN_DONATION_USD = 2

# Features that donating unlocks. These are convenience/scale only — never the ability to
# do good research, which stays free.
PREMIUM_FEATURES = ["large_sweeps", "batch_cross_region", "unlimited_paper_pages", "priority_research"]


def _licence_path():
    return get_settings().data_dir / "licence.json"


def _load_licence():
    try:
        return json.loads(_licence_path().read_text())
    except Exception:
        return None


def _verify(lic: dict, did: str) -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except Exception:
        return False
    if not lic or lic.get("device_id") != did:
        return False
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(PUBLIC_KEY_HEX))
        payload = json.dumps({"device_id": lic["device_id"], "tier": lic.get("tier", "supporter"),
                              "issued_at": lic.get("issued_at", 0)}, sort_keys=True).encode()
        pub.verify(base64.b64decode(lic["signature"]), payload)
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def _placeholder_key() -> bool:
    return PUBLIC_KEY_HEX == "0" * 64


def status() -> dict:
    did = device_id()
    lic = _load_licence()
    valid = bool(lic) and not _placeholder_key() and _verify(lic, did)
    tier = (lic.get("tier", "supporter") if valid else "free")
    return {
        "device_id": did[:16],        # short, opaque — safe to show and share when donating
        "full_device_id": did,
        "tier": tier,
        "is_supporter": tier != "free",
        "features": PREMIUM_FEATURES if tier != "free" else [],
        "donate_url": DONATE_URL,
        "min_donation": MIN_DONATION_USD,
        "checked_at": time.time(),
    }


def activate(licence: dict) -> dict:
    """Redeem a signed licence for THIS device: verify offline, and only if it matches this
    device write it locally so the unlock persists. Returns the fresh status either way."""
    did = device_id()
    ok = bool(licence) and not _placeholder_key() and _verify(licence, did)
    if ok:
        try:
            _licence_path().write_text(json.dumps(licence))
        except Exception:
            pass
    st = status()
    st["activated"] = ok
    if not ok:
        st["reason"] = ("licensing isn't enabled on this build yet"
                        if _placeholder_key() else
                        "that licence doesn't match this device")
    return st


def is_premium() -> bool:
    """Shared gate other modules can consult. Never blocks core research — only
    scale/convenience features should call this."""
    return status()["tier"] != "free"
