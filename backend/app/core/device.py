"""Stable device identity — derived from hardware/OS signals the user cannot casually
change. Used two ways: to SEED per-device uniqueness (so no two users get the same
generated ideas), and as the binding target for the donation-ware entitlement licence.

No personal data is collected; the raw signals are hashed to an opaque id.
"""

from __future__ import annotations

import hashlib
import platform
import uuid
from functools import lru_cache


def _machine_guid() -> str:
    # Windows: the immutable MachineGuid from the registry.
    try:
        import winreg  # type: ignore
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
        v, _ = winreg.QueryValueEx(k, "MachineGuid")
        return str(v)
    except Exception:
        return ""


@lru_cache
def device_id() -> str:
    """A stable, opaque 64-hex device id. Combines OS, hostname, the platform machine
    GUID, and the MAC-derived node id, then hashes them."""
    raw = "|".join([platform.system(), platform.machine(), platform.node(),
                    _machine_guid(), str(uuid.getnode())])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def device_seed() -> int:
    """A deterministic per-device integer seed for uniqueness in generation/exploration."""
    return int(device_id()[:12], 16)
