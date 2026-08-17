#!/usr/bin/env python3
"""
package.py — build a COMPILED, SIGNED, cross-platform release of ACE Studio.

What it produces (works on Windows, macOS and Linux):
  • frontend  → the minified Vite build only (no React/TS source ships).
  • backend   → Python compiled to bytecode (.pyc) with the .py source removed,
                so the logic is not sitting there in plain text.
  • integrity → every shipped file is hashed into MANIFEST.json, and that manifest
                is signed with YOUR Ed25519 private key. A guard verifies the signature
                on every startup, so a copy edited by anyone who does not hold your
                private key refuses to run. Updates are the same: the updater only
                applies a package that carries a valid signature from your key.

Honest limits (read these):
  • Bytecode (.pyc) is obfuscation, not encryption — a determined expert can decompile it.
    For stronger protection, build the backend with Nuitka on each target OS (see --nuitka).
  • The guard runs on the user's machine, so a determined attacker with full control of that
    machine could patch the guard out. Signing makes tampering DETECTABLE and blocks the normal
    path; it is not DRM. True tamper-proofing only exists when the code runs on a server you own.
  • Compiled .pyc is tied to a Python minor version. This build targets Python {PYTAG}: recipients
    must run that same Python 3.x minor. The launcher checks and tells them if it differs.

Usage:
  python package.py keygen                 # once: create your signing keypair (keep the private key safe)
  python package.py build                  # build + sign a release into ./release and zip it
  python package.py build --out DIR        # build into a chosen directory
  python package.py build --keep-source    # (debug) keep .py alongside .pyc
  python package.py verify <dir|zip>       # check a release's signature + file hashes
  python package.py sign <dir>             # re-sign a directory (used by the updater/build)

The private key lives OUTSIDE the repo (default: ~/.ace_studio_keys/ace_ed25519.key) and is
NEVER copied into a release. Only the PUBLIC key ships.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")   # so glyphs don't crash a cp1252 console
except Exception:
    pass

REPO = Path(__file__).resolve().parent
PYTAG = f"{sys.version_info.major}.{sys.version_info.minor}"


def _wt(p: "Path", s: str, nl: str = "\n") -> None:
    """Write text as UTF-8 with explicit newlines — never the Windows locale codec, and LF for
    shell scripts so run.sh works on macOS/Linux."""
    p.write_text(s, encoding="utf-8", newline=nl)
KEY_DIR = Path.home() / ".ace_studio_keys"
PRIV_KEY = KEY_DIR / "ace_ed25519.key"           # 32-byte seed, hex — YOURS, never shipped
PUB_KEY = KEY_DIR / "ace_ed25519.pub"            # 32-byte public key, hex — ships in the release

# Files/dirs that are runtime state (per user) and are excluded from the signed manifest.
RUNTIME_DIRS = {"data", "__pycache__", ".venv", "node_modules", ".git", ".claude"}
# Unsigned config/state: excluded from the manifest so it can be edited or preserved locally.
RUNTIME_FILES = {"MANIFEST.json", "MANIFEST.sig", "session.pkl", "ace.log",
                 ".env", "requirements.lock"}

# Where compiled releases pull updates from. The GitHub "latest release" API works for private
# repos with a token and returns the newest release's assets — so a stable pointer, no per-file id.
DEFAULT_UPDATE_URL = ""  # Automatic remote updates are intentionally disabled.


# ─────────────────────────────────────────────────────────────────────────────
# Ed25519 + manifest helpers. This exact text is ALSO written into the release as
# `_sign.py`, so the guard/updater verify with the same code. Single source of truth.
# ─────────────────────────────────────────────────────────────────────────────
SIGN_MODULE_SRC = r'''"""Ed25519 signing/verification + file hashing for ACE Studio release integrity.

Prefers the fast, audited `cryptography` library and transparently falls back to a compact
pure-Python Ed25519 (public-domain reference implementation) so verification still works even
if that library is missing. Both paths use standard Ed25519, so keys/signatures interoperate.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

RUNTIME_DIRS = {"data", "__pycache__", ".venv", "node_modules", ".git", ".claude"}
RUNTIME_FILES = {"MANIFEST.json", "MANIFEST.sig", "session.pkl", "ace.log",
                 ".env", "requirements.lock"}

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey, Ed25519PublicKey)
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PrivateFormat, PublicFormat, NoEncryption)
    _HAVE = True
except Exception:
    _HAVE = False

# ---- pure-python Ed25519 fallback (djb reference, public domain) -------------
_b = 256
_q = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493


def _H(m): return hashlib.sha512(m).digest()
def _expmod(b, e, m):
    r = 1; b %= m
    while e:
        if e & 1: r = (r * b) % m
        e >>= 1; b = (b * b) % m
    return r
def _inv(x): return _expmod(x, _q - 2, _q)
_d = (-121665 * _inv(121666)) % _q
_I = _expmod(2, (_q - 1) // 4, _q)
def _xrecover(y):
    xx = (y * y - 1) * _inv(_d * y * y + 1)
    x = _expmod(xx, (_q + 3) // 8, _q)
    if (x * x - xx) % _q != 0: x = (x * _I) % _q
    if x % 2 != 0: x = _q - x
    return x
_By = (4 * _inv(5)) % _q
_B = [_xrecover(_By) % _q, _By % _q]
def _edwards(P, Q):
    x1, y1 = P; x2, y2 = Q
    x3 = (x1 * y2 + x2 * y1) * _inv(1 + _d * x1 * x2 * y1 * y2)
    y3 = (y1 * y2 + x1 * x2) * _inv(1 - _d * x1 * x2 * y1 * y2)
    return [x3 % _q, y3 % _q]
def _scalarmult(P, e):
    if e == 0: return [0, 1]
    Q = _scalarmult(P, e // 2); Q = _edwards(Q, Q)
    if e & 1: Q = _edwards(Q, P)
    return Q
def _bit(h, i): return (h[i // 8] >> (i % 8)) & 1
def _encodeint(y): return bytes([(y >> (8 * i)) & 255 for i in range(_b // 8)])
def _encodepoint(P):
    x, y = P
    bits = [(y >> i) & 1 for i in range(_b - 1)] + [x & 1]
    return bytes([sum(bits[i * 8 + j] << j for j in range(8)) for i in range(_b // 8)])
def _Hint(m):
    h = _H(m); return sum(2 ** i * _bit(h, i) for i in range(2 * _b))
def _pp_publickey(seed):
    h = _H(seed); a = 2 ** (_b - 2) + sum(2 ** i * _bit(h, i) for i in range(3, _b - 2))
    return _encodepoint(_scalarmult(_B, a))
def _pp_sign(seed, m, pk):
    h = _H(seed); a = 2 ** (_b - 2) + sum(2 ** i * _bit(h, i) for i in range(3, _b - 2))
    r = _Hint(h[_b // 8:_b // 4] + m); R = _scalarmult(_B, r)
    S = (r + _Hint(_encodepoint(R) + pk + m) * a) % _L
    return _encodepoint(R) + _encodeint(S)
def _decodeint(s): return sum(2 ** i * _bit(s, i) for i in range(0, _b))
def _isoncurve(P):
    x, y = P; return (-x * x + y * y - 1 - _d * x * x * y * y) % _q == 0
def _decodepoint(s):
    y = sum(2 ** i * _bit(s, i) for i in range(0, _b - 1)); x = _xrecover(y)
    if x & 1 != _bit(s, _b - 1): x = _q - x
    P = [x, y]
    if not _isoncurve(P): raise ValueError("point not on curve")
    return P
def _pp_verify(pk, sig, m):
    if len(sig) != 64 or len(pk) != 32: return False
    try:
        R = _decodepoint(sig[:32]); A = _decodepoint(pk); S = _decodeint(sig[32:])
    except Exception:
        return False
    h = _Hint(_encodepoint(R) + pk + m)
    return _scalarmult(_B, S) == _edwards(R, _scalarmult(A, h))


# ---- public API --------------------------------------------------------------
def gen_keypair():
    """Return (seed_32, public_32)."""
    if _HAVE:
        sk = Ed25519PrivateKey.generate()
        seed = sk.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        pub = sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return seed, pub
    seed = os.urandom(32)
    return seed, _pp_publickey(seed)


def public_from_seed(seed: bytes) -> bytes:
    if _HAVE:
        return Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw)
    return _pp_publickey(seed)


def sign(seed: bytes, msg: bytes) -> bytes:
    if _HAVE:
        return Ed25519PrivateKey.from_private_bytes(seed).sign(msg)
    return _pp_sign(seed, msg, _pp_publickey(seed))


def verify(pub: bytes, sig: bytes, msg: bytes) -> bool:
    if _HAVE:
        try:
            Ed25519PublicKey.from_public_bytes(pub).verify(sig, msg)
            return True
        except Exception:
            return False
    return _pp_verify(pub, sig, msg)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_files(root: Path):
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        at_root = Path(dirpath) == root
        # Prune __pycache__ anywhere; prune the big runtime dirs ONLY at the top level, so a
        # nested package that happens to be named "data" (app/modules/data) is NOT dropped.
        dirnames[:] = [d for d in dirnames
                       if d != "__pycache__" and not (at_root and d in RUNTIME_DIRS)]
        for fn in filenames:
            p = Path(dirpath) / fn
            rel = p.relative_to(root).as_posix()
            if fn in RUNTIME_FILES or fn.endswith(".log"):
                continue
            yield rel, p


def build_manifest(root: Path) -> dict:
    return {rel: _sha256(p) for rel, p in sorted(iter_files(root))}


def canonical(manifest: dict) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()


def verify_tree(root: Path):
    """Return (ok, message). Checks the signature over MANIFEST.json and that every file
    matches — no additions, no edits, no deletions."""
    root = Path(root)
    mpath, spath, ppath = root / "MANIFEST.json", root / "MANIFEST.sig", root / "public_key.hex"
    if not (mpath.exists() and spath.exists() and ppath.exists()):
        return False, "release is not signed (missing manifest/signature/public key)"
    manifest = json.loads(mpath.read_text())
    sig = bytes.fromhex(spath.read_text().strip())
    pub = bytes.fromhex(ppath.read_text().strip())
    if not verify(pub, sig, canonical(manifest)):
        return False, "signature INVALID — this release was not produced by the trusted key"
    actual = build_manifest(root)
    manifest.pop("public_key.hex", None); actual.pop("public_key.hex", None)
    if actual != manifest:
        changed = sorted(set(actual) ^ set(manifest)) or \
            [k for k in manifest if actual.get(k) != manifest[k]]
        return False, f"file integrity FAILED — changed/added/removed: {', '.join(changed[:8])}"
    return True, "ok"
'''


# ── load the crypto into THIS process (build-time signing) ───────────────────
_sign_ns: dict = {}
exec(compile(SIGN_MODULE_SRC, "_sign.py", "exec"), _sign_ns)


def _load_seed() -> bytes:
    if not PRIV_KEY.exists():
        sys.exit(f"No signing key at {PRIV_KEY}. Run:  python package.py keygen")
    return bytes.fromhex(PRIV_KEY.read_text().strip())


# ─────────────────────────────────────────────────────────────────────────────
# Release-side files (written verbatim into every build)
# ─────────────────────────────────────────────────────────────────────────────
GUARD_PY = '''"""Startup integrity guard — verifies this install is intact and signed by the trusted key.
Exit non-zero (and print why) if anything was modified. Imported by serve.py before the app."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import _sign  # noqa: E402


def enforce() -> None:
    ok, msg = _sign.verify_tree(ROOT)
    if not ok:
        print("\\n  [FAIL] ACE Studio integrity check failed:\\n    " + msg)
        print("    Refusing to start a modified copy. Reinstall from the official signed release.\\n")
        raise SystemExit(3)


if __name__ == "__main__":
    enforce()
    print("integrity: ok")
'''

SERVE_PY = '''"""ACE Studio launcher — verify integrity, then serve the app (backend + built frontend)
from a single process. Open http://127.0.0.1:8766 in a browser."""
from __future__ import annotations
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NEED = "{PYTAG}"
have = f"{sys.version_info.major}.{sys.version_info.minor}"
if have != NEED:
    print(f"\\n  This build is compiled for Python {NEED}, but you are running {have}.")
    print(f"  Install Python {NEED} and run again (compiled bytecode is version-specific).\\n")
    raise SystemExit(2)

import _guard  # noqa: E402
_guard.enforce()

sys.path.insert(0, str(ROOT / "backend"))
HOST = os.environ.get("ACE_HOST", "127.0.0.1")
PORT = int(os.environ.get("ACE_PORT", "8766"))


def _open():
    time.sleep(2.0)
    try:
        webbrowser.open(f"http://{HOST}:{PORT}/")
    except Exception:
        pass


if __name__ == "__main__":
    import uvicorn
    print(f"\\n  ACE Studio -> http://{HOST}:{PORT}/   (Ctrl-C to stop)\\n")
    threading.Thread(target=_open, daemon=True).start()
    uvicorn.run("app.main:app", host=HOST, port=PORT, log_level="warning")
'''

UPDATE_PY = '''"""ACE Studio remote updater disabled by design."""
print('[update] Automatic GitHub updates are disabled. Your local build will not be overwritten.')
'''

RUN_SH = '''#!/usr/bin/env bash
# ACE Studio — compiled release launcher (macOS / Linux).
set -e
cd "$(dirname "$0")"
NEED="{PYTAG}"
PY=""
for c in "python{PYTAG}" python3 python; do command -v "$c" >/dev/null 2>&1 && PY="$c" && break; done
[ -z "$PY" ] && { echo "Python $NEED not found. Install it from https://www.python.org/downloads/"; exit 1; }
if [ ! -x ".venv/bin/python" ]; then
  echo "[setup] creating virtual environment…"; "$PY" -m venv .venv
fi
VPY=".venv/bin/python"
if ! cmp -s backend/requirements.txt .venv/requirements.lock 2>/dev/null; then
  echo "[setup] installing dependencies…"
  "$VPY" -m pip install --quiet --upgrade pip
  "$VPY" -m pip install --quiet -r backend/requirements.txt
  cp backend/requirements.txt .venv/requirements.lock
fi
exec "$VPY" serve.py
'''

RUN_BAT = '''@echo off
REM ACE Studio - compiled release launcher (Windows).
setlocal
cd /d "%~dp0"
if not exist ".venv\\Scripts\\python.exe" (
  echo [setup] creating virtual environment...
  py -{PYTAG} -m venv ".venv" 2>nul || python -m venv ".venv" || goto :err
)
set "VPY=.venv\\Scripts\\python.exe"
echo [setup] installing dependencies...
"%VPY%" -m pip install --quiet --upgrade pip
"%VPY%" -m pip install --quiet -r backend\\requirements.txt || goto :err
"%VPY%" serve.py
goto :eof
:err
echo [error] setup failed. Install Python {PYTAG} and try again.
pause
exit /b 1
'''

README_REL = '''# ACE Studio (compiled release)

This is a compiled, signed build. The Python logic ships as bytecode (no source), the frontend
as a minified build, and every file is covered by a signature. If any file is modified, the app
refuses to start.

## Run it
- Windows: double-click `run.bat`
- macOS / Linux: `bash run.sh`  (first run creates a virtual environment and installs deps)

Then open http://127.0.0.1:8766 if a browser doesn't open by itself. Log in with your own
WorldQuant BRAIN account and set your own LLM keys in Settings — nothing personal ships in here.

Requires **Python {PYTAG}** (compiled bytecode is tied to that minor version) and Node is NOT
needed (the frontend is prebuilt).

## Updates
Automatic GitHub/remote updates are intentionally disabled. ACE will never overwrite this build
from a remote repository. To install a new version, replace the application with a build you have
reviewed and tested yourself. Release signatures can still be used to verify package integrity.
'''


# ─────────────────────────────────────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────────────────────────────────────
def cmd_keygen(_args):
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    if PRIV_KEY.exists():
        sys.exit(f"A signing key already exists at {PRIV_KEY}. Refusing to overwrite it.\n"
                 f"(Delete it yourself only if you are sure — every existing install trusts it.)")
    seed, pub = _sign_ns["gen_keypair"]()
    _wt(PRIV_KEY, seed.hex()); _wt(PUB_KEY, pub.hex())
    try:
        os.chmod(PRIV_KEY, 0o600)
    except Exception:
        pass
    print(f"OK: wrote signing keys to {KEY_DIR}")
    print(f"  private: {PRIV_KEY}   (KEEP SECRET — back it up; if lost you can't sign updates)")
    print(f"  public:  {PUB_KEY}    (ships inside each release)")


def _run(cmd, cwd):
    print(f"  $ {' '.join(cmd)}  ({cwd})")
    subprocess.run(cmd, cwd=cwd, check=True, shell=(os.name == "nt"))


def cmd_build(args):
    seed = _load_seed()
    pub = _sign_ns["public_from_seed"](seed)
    out = Path(args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # 1) Frontend → minified build
    print("[1/5] building frontend…")
    npm = "npm.cmd" if os.name == "nt" else "npm"
    if not (REPO / "frontend" / "node_modules").exists():
        _run([npm, "install"], REPO / "frontend")
    _run([npm, "run", "build"], REPO / "frontend")
    shutil.copytree(REPO / "frontend" / "dist", out / "frontend" / "dist")

    # 2) Backend source → release (minus caches/venv/tests). NOTE: do NOT exclude by the name
    # "data" here — app/modules/data is a real package. Only strip caches/tests/old bytecode.
    print("[2/5] copying backend…")
    def ignore(_dir, names):
        return [n for n in names if n in {"__pycache__", ".pytest_cache", "tests", "ace.log"}
                or n.endswith((".pyc", ".pyo"))]
    shutil.copytree(REPO / "backend" / "app", out / "backend" / "app", ignore=ignore)
    shutil.copytree(REPO / "backend" / "vendor", out / "backend" / "vendor", ignore=ignore)
    # requirements (+ cryptography for fast signature checks)
    reqs = (REPO / "backend" / "requirements.txt").read_text().splitlines()
    if not any(r.strip().lower().startswith("cryptography") for r in reqs):
        reqs.append("cryptography>=41")
    (out / "backend").mkdir(parents=True, exist_ok=True)
    _wt(out / "backend" / "requirements.txt", "\n".join(reqs) + "\n")

    # 3) Compile backend to bytecode; drop .py (unless --keep-source or --nuitka handled elsewhere)
    print("[3/5] compiling backend to bytecode…")
    import compileall
    compileall.compile_dir(str(out / "backend"), quiet=1, legacy=True, optimize=2, force=True)
    if not args.keep_source:
        removed = kept = 0
        for py in (out / "backend").rglob("*.py"):
            if py.with_suffix(".pyc").exists():
                py.unlink(); removed += 1
            else:
                kept += 1
        print(f"      stripped {removed} .py source files ({kept} kept where compile failed)")

    # 4) Emit launcher / guard / updater / public key / readme
    print("[4/5] writing launcher, guard and updater…")
    _wt(out / "_sign.py", SIGN_MODULE_SRC)
    _wt(out / "_guard.py", GUARD_PY)
    _wt(out / "serve.py", SERVE_PY.replace("{PYTAG}", PYTAG))
    _wt(out / "update.py", UPDATE_PY)
    _wt(out / "run.sh", RUN_SH.replace("{PYTAG}", PYTAG))          # LF
    _wt(out / "run.bat", RUN_BAT.replace("{PYTAG}", PYTAG), nl="\r\n")  # CRLF for cmd
    _wt(out / "run.command", RUN_SH.replace("{PYTAG}", PYTAG))     # macOS double-click (LF)
    _wt(out / "README.md", README_REL.replace("{PYTAG}", PYTAG))
    _wt(out / "public_key.hex", pub.hex())
    _wt(out / "VERSION.txt", f"pytag={PYTAG}\n")

    print("      automatic remote updates: DISABLED")
    try:
        os.chmod(out / "run.sh", 0o755); os.chmod(out / "run.command", 0o755)
    except Exception:
        pass

    # 5) Sign the tree
    print("[5/5] hashing + signing…")
    _sign_tree(out, seed)
    ok, msg = _sign_ns["verify_tree"](out)
    print(f"      self-check: {msg}")
    if not ok:
        sys.exit("Build produced an invalid signature — aborting.")

    zip_path = out.parent / f"ACE-Studio-py{PYTAG}.zip"
    _zip_dir(out, zip_path)
    print(f"\nOK: release ready:\n    folder: {out}\n    zip:    {zip_path}")
    print("  Distribute the zip. Recipients unzip and run run.bat / run.sh.")


def _sign_tree(root: Path, seed: bytes):
    manifest = _sign_ns["build_manifest"](root)
    _wt(root / "MANIFEST.json", __import__("json").dumps(manifest, sort_keys=True, indent=0))
    sig = _sign_ns["sign"](seed, _sign_ns["canonical"](manifest))
    _wt(root / "MANIFEST.sig", sig.hex())


def cmd_sign(args):
    _sign_tree(Path(args.dir).resolve(), _load_seed())
    print("OK: signed", args.dir)


def cmd_verify(args):
    target = Path(args.target).resolve()
    tmp = None
    if target.is_file() and target.suffix == ".zip":
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        with zipfile.ZipFile(target) as z:
            z.extractall(tmp)
        roots = [p.parent for p in tmp.rglob("MANIFEST.json")]
        target = roots[0] if roots else tmp
    ok, msg = _sign_ns["verify_tree"](target)
    print(("OK: " if ok else "FAIL: ") + msg)
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if ok else 1)


def _zip_dir(root: Path, zip_path: Path):
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, p in sorted(_sign_ns["iter_files"](root)):
            z.write(p, Path(root.name) / rel)
        # Signed manifest + unsigned config that must still ship (the update pointer/token).
        for extra in ("MANIFEST.json", "MANIFEST.sig"):
            if (root / extra).exists():
                z.write(root / extra, Path(root.name) / extra)


def main():
    ap = argparse.ArgumentParser(description="Build a compiled, signed ACE Studio release.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen").set_defaults(fn=cmd_keygen)
    b = sub.add_parser("build"); b.set_defaults(fn=cmd_build)
    b.add_argument("--out", default=str(REPO / "release"))
    b.add_argument("--keep-source", action="store_true")
    b.add_argument("--update-url", default="", help="deprecated; remote updates are disabled")
    b.add_argument("--update-token", default="", help="deprecated; tokens are never shipped")
    b.add_argument("--no-ship-token", action="store_true", help="deprecated; tokens are never shipped")
    b.add_argument("--nuitka", action="store_true", help="(reserved) build native per-OS with Nuitka")
    s = sub.add_parser("sign"); s.set_defaults(fn=cmd_sign); s.add_argument("dir")
    v = sub.add_parser("verify"); v.set_defaults(fn=cmd_verify); v.add_argument("target")
    args = ap.parse_args()
    if getattr(args, "nuitka", False):
        print("Note: --nuitka is a placeholder. For native builds run Nuitka on each target OS; "
              "the default bytecode build already works cross-platform.")
    args.fn(args)


if __name__ == "__main__":
    main()
