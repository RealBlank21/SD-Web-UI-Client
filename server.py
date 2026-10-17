"""
Anima — standalone, deployable web app.

A chat interface to an AI art agent (OpenRouter LLM with function calling
driving a Stable Diffusion WebUI server) plus a gallery of generated images.
Designed to run on a home server behind a reverse proxy (e.g. Caddy) and be
installed on a phone as a PWA.

Independent of any other project: the SD client is vendored in sd_client.py,
configuration lives in data/config.json, images in outputs/.

Configuration (all editable in the app's Settings sheet too):
    AGENT_PASSWORD   required — password/PIN to unlock the app
    HOST / PORT      bind address (default 0.0.0.0:8000)

Usage:
    AGENT_PASSWORD=secret python server.py
    python server.py --port 8000
"""

import argparse
import base64
import contextvars
import hashlib
import getpass
import hmac
import ipaddress
import json
import os
import random
import re
import secrets
import shutil
import socket
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty, Queue
import urllib.parse
from urllib.parse import parse_qs, unquote, urlparse

import requests

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from sd_client import (forge_lock, forge_lease,
                                                     # vendored WebUI client
    SDClient,
    SDWebUIError,
    guess_arch,
    is_distilled,
)
from agent_core import (                      # vendored agent brain
    DEFAULT_BASE_PROMPT,
    DEFAULT_LLM,
    LLMError,
    MAX_TOOL_ROUNDS,
    _clean_assistant_msg,
    _is_auth_error,
    default_guide_text,
    effective_system_prompt,
    ensure_tags,
    strip_scene_tags,
    strip_lora_tags,
    apply_lora_tags,
    lora_prompt_block,
    execute_tool,
)
from sd_client import LORA_WEIGHT_BAND, lora_referenceable, lora_tag

APP_DIR = ROOT / "webui"
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "outputs"
#: Hand-curated, world-readable login backdrops. See _serve_login_bg.
LOGIN_BG_DIR = DATA_DIR / "login-bg"
CONFIG_FILE = DATA_DIR / "config.json"
SESSION_FILE = DATA_DIR / "session_secret"
STATE_FILE = DATA_DIR / "chat_state.json"

STATIC_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".webmanifest": "application/manifest+json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".woff2": "font/woff2",
}
IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
SEARCH_LIMIT = 500                   # cap on hits from /api/gallery/search
GEN_TOOLS = {"generate_image", "edit_image"}
PROGRESS_POLL = 1.2
LLM_TIMEOUT = 180

# path-like text the LLM may echo — scrubbed from replies
_PATH_RE = re.compile(
    r"(?:[A-Za-z]:\\[^\s`\"'<>|]+)|(?:/[\w.\-]+(?:/[\w.\-]+)+)")


def scrub_paths(text: str) -> str:
    """Replace absolute paths in LLM text with bare file names."""
    def repl(m):
        name = re.split(r"[\\/]", m.group(0))[-1]
        return name
    return _PATH_RE.sub(repl, text or "")


# ------------------------------------------------------------------ config

DEFAULT_CONFIG = {
    "openrouter_key": "",
    "llm_models": [DEFAULT_LLM],
    "sd_url": "http://0.0.0.0:7860",
    "system_prompt": "",   # custom system message; "" = built-in default
    # custom model guide; "" = the model_guide.txt file on disk. Held in the
    # config rather than written back to the file so a save never dirties a
    # hand-maintained model_guide.txt.
    "model_guide": "",
    "username": "",          # how the AI knows the user
    "persona_id": "",        # active user persona (data/personas.json)
    # per-architecture companion files, e.g.
    # {"qwen": {"text_encoder": "qwen_3_06b_base.safetensors",
    #           "sd_vae": "qwen_image_vae.safetensors",
    #           "low_bits": "float8-e4m3fn"}}
    "sd_components": {},
}


def _normalise_config(cfg: dict) -> dict:
    if isinstance(cfg["llm_models"], str):
        cfg["llm_models"] = [m.strip() for m in cfg["llm_models"].split(",")
                             if m.strip()]
    return cfg


def load_config(uid: str = "") -> dict:
    """A user's settings.

    `data/config.json` is now the *template*, not the live config: it is
    seeded from DEFAULT_CONFIG and read only. Each user's own live copy
    lives in `users/<uid>/config.json`.

    With no uid this still returns the template, which is what the operator
    sees and what a new account starts from.
    """
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            base = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(base, dict):
                cfg.update({k: v for k, v in base.items() if k in cfg})
        except Exception:
            pass
    if uid:
        path = user_state_file(uid, "config.json")
        if path.exists():
            try:
                mine = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(mine, dict):
                    cfg.update({k: v for k, v in mine.items() if k in cfg})
            except Exception:
                pass
        else:
            # First run for this account. Start from the tuned prompts and
            # defaults, but never inherit someone else's credentials — BYOK
            # (MULTIUSER.md decision 2) means the key is the user's own.
            cfg["openrouter_key"] = ""
    return _normalise_config(cfg)


def save_config(cfg: dict, uid: str = "") -> None:
    if uid:
        _write_json_atomic(user_state_file(uid, "config.json"), cfg, indent=2)
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(CONFIG_FILE)


# ------------------------------------------------------------ audit trail
#
# Decision 5 gave the admin role full access to every user's content and
# config. That is fine for a box you own, but "full access" with no record is
# indistinguishable from a bug when someone asks about it later — so every
# admin action against another user, and every privilege-relevant action by a
# normal user, is appended here.
#
# Append-only JSONL: one line per event, never rewritten. Truncated at
# AUDIT_MAX on write rather than rotated, so the file stays greppable and
# never needs a rotation that could lose the tail.

AUDIT_FILE = DATA_DIR / "audit.jsonl"
AUDIT_MAX = 20000
#: Values worth logging. Anything not in here is ordinary use.
AUDITED = {
    # admin reaching into another user's space
    "character.edit.other", "character.delete.other", "character.fav.other",
    "scenario.other", "settings.other", "gallery.other",
    # identity and credentials
    "user.create", "user.passwd", "user.disable", "user.enable",
    "user.delete",
    "login.fail", "login.ok",
    # the privileged surface in general
    "settings.sd_url", "settings.key", "system_prompt.set",
    "model_guide.set", "character.delete",
}


def audit(actor: dict, action: str, target: str = "", detail: str = "") -> None:
    """Append one event. Never raises — an unwritable audit log must not take
    the request down with it, but the failure is worth seeing in stderr."""
    if action not in AUDITED:
        return
    actor_id = str(actor.get("id") or "")
    target_id = str(target or "")
    # An admin acting on their own account is not privilege use — but only
    # the cross-user actions are suppressed. Changing your own key or Forge
    # is exactly the kind of thing the log exists to answer for.
    if (action.endswith(".other") and actor.get("role") == ROLE_ADMIN
            and target_id and target_id == actor_id):
        return
    try:
        rec = {"ts": int(time.time()), "actor": actor_id,
               "who": str(actor.get("name") or ""),
               "admin": actor.get("role") == ROLE_ADMIN,
               "action": action, "target": target_id, "detail": detail[:200]}
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if AUDIT_FILE.stat().st_size > 8 * 1024 * 1024:
            _trim_audit()
    except Exception as e:                             # noqa: BLE001
        print(f"audit: {type(e).__name__}: {e}", file=sys.stderr)


def _trim_audit() -> None:
    """Keep the newest AUDIT_MAX lines. Rewritten atomically so a crash
    mid-trim cannot leave a half-written file that breaks every future write."""
    try:
        lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines(True)
        keep = lines[-AUDIT_MAX:]
        tmp = AUDIT_FILE.with_suffix(".tmp")
        tmp.write_text("".join(keep), encoding="utf-8")
        tmp.replace(AUDIT_FILE)
    except Exception:                                 # noqa: BLE001
        pass


def read_audit(limit: int = 200, admin_only: bool = False) -> list:
    """Newest first. `admin_only` hides the noise of ordinary use so the
    entries that matter are visible without filtering."""
    try:
        lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if admin_only and not rec.get("admin"):
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------- SSRF guard
#
# `sd_url` is a URL the server fetches on a user's behalf, on a box that is
# publicly reachable. Account provisioning gates *who* may set one; this gates
# *what* they may set it to. Probe-before-trust does not help here — it proves
# a Forge answered, not that the address was safe.
#
# Policy (MULTIUSER.md decision 4): allow RFC1918 and Tailscale's CGNAT range,
# because that is where a real Forge lives; block loopback and link-local,
# which is where cloud instance metadata and the box's own services are.
# Admins bypass — the operator owns the machine.

#: Tailscale hands out 100.64.0.0/10 (RFC 6598), which overlaps nothing in
#: RFC1918 but is exactly where a Forge on a tailnet will be.
FORGE_OK_NETS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                 "100.64.0.0/10")
_FORGE_OK = tuple(ipaddress.ip_network(n) for n in FORGE_OK_NETS)
#: 169.254.0.0/16 covers both link-local and the cloud metadata address
#: (169.254.169.254 on AWS/GCP/Azure) — the credential-theft target.
_FORGE_DENY = (ipaddress.ip_network("127.0.0.0/8"),
               ipaddress.ip_network("169.254.0.0/16"),
               ipaddress.ip_network("0.0.0.0/8"))

#: Hostname or literal IP. Deliberately permissive about *which* names exist
#: (the resolution check below decides that) and strict about the shape, so
#: "not a url" and "my forge" are rejected before anything is dialled.
_FORGE_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]{0,251}"
                            r"[A-Za-z0-9])?$")


def _forge_ip_allowed(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        # ::1 and fe80::/10 are loopback and link-local; everything else v6 is
        # refused rather than guessed at, since no current Forge lives there.
        return False
    if any(ip in n for n in _FORGE_DENY):
        return False
    return any(ip in n for n in _FORGE_OK)


def validate_forge_url(url: str, is_admin: bool = False) -> tuple[bool, str]:
    """(ok, reason). `reason` is phrased for the person who typed the URL.

    Resolves the host before allowing it, because a name that looks harmless
    can point anywhere — checking only the literal would make the whole guard
    decoration. The residual DNS-rebinding window (resolve here, connect to a
    different address later) is why accounts are admin-provisioned rather than
    open; fixing it properly needs a pinned-IP connection, which is more than
    this deserves."""
    url = (url or "").strip()
    if not url:
        return False, "no address given"
    if "://" not in url:
        url = "http://" + url
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as e:
        return False, f"that is not a URL: {e}"
    if parts.scheme not in ("http", "https"):
        return False, (f"{parts.scheme or 'that'}:// is not supported — use "
                       "http or https")
    host = parts.hostname
    if not host:
        return False, "no host in that address"
    # A syntax check for *everyone*, admin included. The admin bypass is about
    # the address range, not about accepting "not a url" — without this an
    # operator typo was written straight into a new account's config.
    if not _FORGE_HOST_RE.match(host):
        return False, f"{host!r} is not a hostname or IP"
    if is_admin:
        return True, ""
    try:
        infos = socket.getaddrinfo(host, parts.port or
                                   (443 if parts.scheme == "https" else 7860),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        return False, f"could not resolve {host}: {e.strerror or e}"
    if not infos:
        return False, f"could not resolve {host}"
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr.split("%")[0])
        except ValueError:
            return False, f"{host} resolved to something unusable ({addr})"
        if not _forge_ip_allowed(ip):
            if ip.is_loopback:
                return False, (f"{host} is this machine — point at your own "
                               "Forge, not the app's host")
            if ip in ipaddress.ip_network("169.254.0.0/16"):
                return False, (f"{host} resolves to {ip}, a link-local "
                               "address — that range includes cloud instance "
                               "metadata and is never a Forge")
            return False, (f"{host} resolves to {ip}, which is outside the "
                           "allowed private ranges (10/8, 172.16/12, "
                           "192.168/16, 100.64/10)")
    return True, ""


def load_session_secret() -> bytes:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SESSION_FILE.exists():
        return SESSION_FILE.read_bytes()
    secret = secrets.token_bytes(32)
    SESSION_FILE.write_bytes(secret)
    return secret


# ------------------------------------------------------------------ users
#
# Multi-user identity (MULTIUSER.md, Phase 0). Before this the cookie was a
# constant hmac(secret, b"auth") — a boolean for "anyone holding the password",
# with no principal to key any data by. Now a request resolves to a real user
# record, which is what Phases 1-5 hang per-user state off.

USERS_FILE = DATA_DIR / "users.json"
SESSIONS_FILE = DATA_DIR / "sessions.json"
SESSION_TTL = 30 * 24 * 3600          # 30 days
PBKDF2_ROUNDS = 200_000
ROLE_ADMIN = "admin"
ROLE_USER = "user"
#: Audit actor for actions with no session — the account CLI.
CLI_ACTOR = {"id": "cli", "name": "cli", "role": ROLE_ADMIN}

# A login with no username is only unambiguous when there is exactly one
# account. qa_probe.py and the manual driving recipe in AGENTS.md both post
# {"password": ...} alone, so this keeps them working on a single-user box
# while still refusing to guess on a multi-user one.
SINGLE_USER_UNNAMED = object()


def hash_password(pw: str) -> str:
    """PBKDF2-HMAC-SHA256, stdlib only. `pbkdf2_sha256$rounds$salt$hash`."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, PBKDF2_ROUNDS)
    return "pbkdf2_sha256$%d$%s$%s" % (
        PBKDF2_ROUNDS, salt.hex(), dk.hex())


def verify_password(pw: str, stored: str) -> bool:
    """Constant-time verify. Never raises on a malformed stored value —
    a corrupt record reads as a wrong password, not as a 500."""
    try:
        algo, rounds, salt_hex, hash_hex = str(stored).split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(),
                                 bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def load_users() -> list:
    if not USERS_FILE.exists():
        return []
    try:
        d = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        return [u for u in d if isinstance(u, dict) and u.get("id")] \
            if isinstance(d, list) else []
    except Exception:
        return []


def save_users(users: list) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = USERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(users, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(USERS_FILE)


def load_sessions() -> dict:
    if not SESSIONS_FILE.exists():
        return {}
    try:
        d = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_sessions(sess: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SESSIONS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(sess, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(SESSIONS_FILE)


def _token_key(token: str) -> str:
    """Sessions are keyed by the *hash* of the token, so a readable
    sessions.json does not hand an attacker live sessions."""
    return hashlib.sha256(token.encode()).hexdigest()


def prune_sessions(sess: dict, now: int | None = None) -> dict:
    now = int(time.time()) if now is None else now
    return {k: v for k, v in sess.items()
            if isinstance(v, dict)
            and int(v.get("expires") or 0) > now}


def new_session(sess: dict, uid: str) -> str:
    """Mint a session for `uid`, dropping expired ones on the way.

    Prune *before* mutating. Clearing first and pruning the empty dict would
    look correct and quietly log every other user out on every login."""
    token = secrets.token_urlsafe(32)
    pruned = prune_sessions(sess)
    pruned[_token_key(token)] = {"user": uid,
                                 "expires": int(time.time()) + SESSION_TTL}
    sess.clear()
    sess.update(pruned)
    save_sessions(sess)
    return token


def drop_session(sess: dict, token: str) -> None:
    if sess.pop(_token_key(token), None) is not None:
        save_sessions(sess)


def find_user(users: list, ident: str) -> dict:
    """Look up by id first, then by name (case-insensitive)."""
    ident = (ident or "").strip()
    for u in users:
        if u.get("id") == ident:
            return u
    low = ident.lower()
    for u in users:
        if str(u.get("name") or "").strip().lower() == low:
            return u
    return {}


def resolve_user(users: list, name: str) -> dict:
    """The account a login is for, or {} when it can't be determined.

    An empty name resolves only when there is exactly one account — see
    SINGLE_USER_UNNAMED."""
    if name.strip():
        return find_user(users, name)
    live = [u for u in users if not u.get("disabled")]
    return live[0] if len(live) == 1 else {}


def bootstrap_admin(password: str) -> dict:
    """First boot: AGENT_PASSWORD becomes the first admin. Returns the user.

    Only ever runs with no users.json — a later boot must not be able to
    mint a new admin from the environment."""
    users = load_users()
    if users:
        return {}
    user = {"id": new_user_id(users), "name": "admin",
            "role": ROLE_ADMIN, "pass_hash": hash_password(password),
            "created": int(time.time()), "disabled": False}
    save_users([user])
    return user


def _prompt_pw(prompt: str) -> str:
    """Read a password twice and refuse a mismatch. CLI only."""
    pw = getpass.getpass(prompt)
    if pw != getpass.getpass("confirm: "):
        raise SystemExit("passwords differ")
    return pw


def new_user_id(users: list) -> str:
    used = {str(u.get("id")) for u in users}
    for i in range(1, 1000):
        cand = "u%03d" % i
        if cand not in used:
            return cand
    return "u" + secrets.token_hex(4)


def public_user(u: dict) -> dict:
    """The user as the client is allowed to see it — never the hash."""
    return {"id": str(u.get("id") or ""),
            "name": str(u.get("name") or ""),
            "role": str(u.get("role") or ROLE_USER),
            "admin": u.get("role") == ROLE_ADMIN}


# The account operations live here, not in the CLI, so the admin UI and the
# command line cannot drift apart. The CLI is a thin wrapper over these.

def create_user(name: str, password: str, role: str = ROLE_USER,
                actor: dict | None = None, api_key: str = "",
                sd_url: str = "") -> dict:
    """Create an account. Raises ValueError on a bad or duplicate name.

    `api_key` and `sd_url` are optional and both nullable — an admin can
    provision someone else's BYOK credentials here instead of making them
    paste two secrets into the app themselves. Omitted or empty means "leave
    the template's defaults", which is a working account with no key.

    The URL is validated with the admin bypass, because the operator owns the
    box and may legitimately point someone at a Forge on an address the
    per-user SSRF guard would refuse.
    """
    name = (name or "").strip()
    if not name or len(name) > 40:
        raise ValueError("name must be 1-40 characters")
    if any(ord(c) < 32 for c in name):
        raise ValueError("name has control characters")
    if not password:
        raise ValueError("password required")
    users = load_users()
    if find_user(users, name):
        raise ValueError(f"already exists: {name}")
    u = {"id": new_user_id(users), "name": name,
         "role": ROLE_ADMIN if role == ROLE_ADMIN else ROLE_USER,
         "pass_hash": hash_password(password),
         "created": int(time.time()), "disabled": False}
    sd_url = (sd_url or "").strip().rstrip("/")
    if sd_url:
        ok, why = validate_forge_url(sd_url, is_admin=True)
        if not ok:
            raise ValueError(f"image server: {why}")
    users.append(u)
    save_users(users)
    # Written as a partial config: load_config(uid) merges over the template,
    # so only the fields actually supplied are pinned.
    seeded = {}
    if api_key.strip():
        seeded["openrouter_key"] = api_key.strip()
    if sd_url:
        seeded["sd_url"] = sd_url
    if seeded:
        _write_json_atomic(user_state_file(u["id"], "config.json"), seeded,
                            indent=2)
    audit(actor or CLI_ACTOR, "user.create", u["id"], name)
    if seeded:
        # Log only what was actually seeded. "key left unset" is not an action,
        # and recording it buries the entries that are.
        if api_key.strip():
            audit(actor or CLI_ACTOR, "settings.key", u["id"], "set")
        if sd_url:
            audit(actor or CLI_ACTOR, "settings.sd_url", u["id"], sd_url)
    return u


def set_user_password(uid: str, password: str,
                      actor: dict | None = None) -> dict:
    users = load_users()
    u = find_user(users, (uid or "").strip())
    if not u:
        raise ValueError(f"no such account: {uid}")
    if not password:
        raise ValueError("password required")
    u["pass_hash"] = hash_password(password)
    save_users(users)
    audit(actor or CLI_ACTOR, "user.passwd", str(u.get("id")),
          str(u.get("name") or ""))
    return u


def set_user_disabled(uid: str, disabled: bool,
                     actor: dict | None = None) -> dict:
    users = load_users()
    u = find_user(users, (uid or "").strip())
    if not u:
        raise ValueError(f"no such account: {uid}")
    # An admin who disables the last admin locks everyone out of the admin UI
    # and the account CLI is the only way back, so say so rather than allow it.
    if disabled and u.get("role") == ROLE_ADMIN and not u.get("disabled"):
        others = [x for x in users
                  if x.get("role") == ROLE_ADMIN and not x.get("disabled")
                  and x.get("id") != u.get("id")]
        if not others:
            raise ValueError("cannot disable the last admin")
    u["disabled"] = bool(disabled)
    save_users(users)
    audit(actor or CLI_ACTOR,
          "user.disable" if disabled else "user.enable",
          str(u.get("id")), str(u.get("name") or ""))
    if disabled:
        # Drop live sessions, or the cookie keeps working for the rest of
        # its TTL. Re-read rather than trusting a stale copy.
        sess = prune_sessions(load_sessions())
        sess = {k: v for k, v in sess.items()
                if v.get("user") != u.get("id")}
        save_sessions(sess)
    return u


def delete_user(uid: str, actor: dict | None = None) -> dict:
    """Remove an account and everything it owned."""
    users = load_users()
    u = find_user(users, (uid or "").strip())
    if not u:
        raise ValueError(f"no such account: {uid}")
    if u.get("role") == ROLE_ADMIN and not u.get("disabled"):
        others = [x for x in users if x.get("role") == ROLE_ADMIN
                  and x.get("id") != u.get("id")]
        if not others:
            raise ValueError("cannot delete the last admin")
    tid = str(u.get("id"))
    users = [x for x in users if x.get("id") != tid]
    save_users(users)
    audit(actor or CLI_ACTOR, "user.delete", tid,
          str(u.get("name") or ""))
    # Their state goes too: chats, personas, config, and their gallery. The
    # character store is global, so cards they own are orphaned rather than
    # removed — deliberately, since other users may share them.
    shutil.rmtree(user_dir(tid), ignore_errors=True)
    shutil.rmtree(OUT_DIR / tid, ignore_errors=True)
    sess = prune_sessions(load_sessions())
    save_sessions({k: v for k, v in sess.items() if v.get("user") != tid})
    return u


def user_row(u: dict) -> dict:
    """A user as the admin list shows it: safe fields plus the facts that
    decide whether the account works (key present, Forge reachable-ish)."""
    uid = str(u.get("id") or "")
    cfg = load_config(uid)
    key = str(cfg.get("openrouter_key") or "")
    d = user_dir(uid)
    chats = sum(1 for _ in (d / "chats").glob("*/*.json")) if (
        d / "chats").is_dir() else 0
    # Excludes .thumbs — those are cached jpegs, and counting them made the
    # admin's image count disagree with the gallery by a few hundred.
    root = OUT_DIR / uid
    images = sum(1 for p in root.rglob("*")
                 if p.is_file() and not p.relative_to(root).parts[0].startswith(".")
                 and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")) \
        if root.is_dir() else 0
    return {**public_user(u), "created": int(u.get("created") or 0),
            "disabled": bool(u.get("disabled")),
            "has_key": bool(key),
            "key_masked": (key[:7] + "…" + key[-4:]) if len(key) > 14 else "",
            "sd_url": str(cfg.get("sd_url") or ""),
            "chats": chats, "images": images}


# ------------------------------------------------- OpenRouter model catalogue

OR_MODELS_URL = "https://openrouter.ai/api/v1/models"
OR_MODELS_FILE = DATA_DIR / "or_models.json"
OR_TTL = 24 * 3600         # the catalogue moves slowly; a day is plenty
OR_FETCH_TIMEOUT = 12


def _usd_per_million(value) -> float:
    """OpenRouter quotes USD per token; the picker shows USD per million."""
    try:
        return round(float(value) * 1e6, 4)
    except (TypeError, ValueError):
        return 0.0


def fetch_openrouter_models(key: str = "", force: bool = False) -> dict:
    """OpenRouter's model catalogue for the Settings picker, cached on disk.

    Typing "vendor/model-slug" from memory is how a fallback chain ends up
    pointing at something that does not exist, so the list is queried instead
    and refreshed at most once a day. A failed refresh keeps serving the last
    good list (with an `error` to show): openrouter.ai having a bad minute must
    not empty the picker.
    """
    try:
        cache = json.loads(OR_MODELS_FILE.read_text(encoding="utf-8"))
        if not isinstance(cache, dict):
            cache = {}
    except Exception:                                    # noqa: BLE001
        cache = {}
    ts = float(cache.get("ts") or 0)
    cached = cache.get("models") or []
    if cached and not force and time.time() - ts < OR_TTL:
        return {"models": cached, "ts": ts, "error": ""}

    headers = {"Accept": "application/json"}
    if key:
        # with the user's key, so the list reflects what their account may use
        headers["Authorization"] = "Bearer " + key
    try:
        resp = requests.get(OR_MODELS_URL, headers=headers,
                            timeout=OR_FETCH_TIMEOUT)
        resp.raise_for_status()
        raw = (resp.json() or {}).get("data") or []
    except Exception as e:                               # noqa: BLE001
        return {"models": cached, "ts": ts,
                "error": f"could not reach OpenRouter ({type(e).__name__}) — "
                         f"showing the last known list"}
    if not raw:
        # an empty catalogue would silently empty the picker; treat it as a
        # failed refresh and keep what we have
        return {"models": cached, "ts": ts,
                "error": "OpenRouter returned no models — "
                         "showing the last known list"}

    models = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "").strip()
        if not mid:
            continue
        price = m.get("pricing") if isinstance(m.get("pricing"), dict) else {}
        try:
            ctx = int(m.get("context_length") or 0)
        except (TypeError, ValueError):
            ctx = 0
        models.append({
            "id": mid,
            "name": str(m.get("name") or mid),
            "ctx": ctx,
            # the agent drives images through function calling, so a model
            # without "tools" is a plain-text-only member of the chain
            "tools": "tools" in (m.get("supported_parameters") or []),
            "in": _usd_per_million(price.get("prompt")),
            "out": _usd_per_million(price.get("completion")),
        })
    if not models:
        return {"models": cached, "ts": ts,
                "error": "no usable models in the OpenRouter response"}
    models.sort(key=lambda m: (not m["tools"], m["name"].lower()))
    ts = time.time()
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = OR_MODELS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"ts": ts, "models": models}),
                       encoding="utf-8")
        tmp.replace(OR_MODELS_FILE)
    except OSError:
        pass
    return {"models": models, "ts": ts, "error": ""}


# -------------------------------------------------------------- characters

CHARS_FILE = DATA_DIR / "characters.json"
CHATS_DIR = DATA_DIR / "chats"
AVATAR_DIR = DATA_DIR / "avatars"
COVER_DIR = DATA_DIR / "covers"     # opening images: char + scenario covers
CHAR_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
# cover file keys: "char-<cid>" or "sc-<cid>.<sid>" (dot = char/scen split)
COVER_KEY_RE = re.compile(
    r"^(?:char-[a-z0-9][a-z0-9-]{0,40}"
    r"|sc-[a-z0-9][a-z0-9-]{0,40}\.[a-z0-9][a-z0-9-]{0,48})$")
CHAT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{0,40}$")
DEFAULT_CHAT_FILE = CHATS_DIR / "default.json"
# The plain assistant used to be a special first row ("No character") rather
# than a card. It is an ordinary character now — one with nothing filled in,
# so its prompts read exactly as the free chat did — which means the list has
# no special case at all and this card is created on demand.
ASSISTANT_ID = "assistant"


# ------------------------------------------------------------------- LoRAs

# A library of LoRAs, keyed by the exact name Forge resolves (`<lora:NAME>`),
# holding the user's own notes about each. Cards hold references into it, so a
# character described by ten cards is described once.
#
# Deliberately NOT model_guide.txt. That file is one document edited as a whole;
# this is a catalogue of independent records, browsed and searched. The guide's
# shape does not survive fifty entries, and its cap would become a wall.
LORAS_FILE = DATA_DIR / "loras.json"

#: The app's OWN LoRA root, and the only place it may write. Forge is pointed
#: here with `--lora-dirs` (webui.settings.sh) in addition to its normal
#: models/Lora. This exists because the unit runs with ProtectHome=read-only
#: and ReadWritePaths covering only this repo — models/Lora is not writable
#: from in here, and weakening the unit to reach it is not worth it.
LORA_DIR_FALLBACK = DATA_DIR / "loras"

#: How many LoRAs one card may carry. Two character LoRAs at full weight fight
#: over the same attention layers and the result is neither of them; three is
#: already past the point where a human can predict the outcome.
LORA_PER_CARD = 3

#: Refuse a card whose weights add up past this. The failure is a blended face
#: that belongs to nobody, and it is invisible in the settings UI.
LORA_SUM_WARN = 2.0


def load_lora_library() -> dict:
    """The saved notes, as {name: record}. Missing/corrupt file = empty."""
    try:
        data = json.loads(LORAS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    lib = data.get("loras") if isinstance(data, dict) else None
    return lib if isinstance(lib, dict) else {}


def save_lora_library(lib: dict) -> None:
    tmp = LORAS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"loras": lib}, indent=1,
                               ensure_ascii=False), encoding="utf-8")
    tmp.replace(LORAS_FILE)


def _clean_lora_record(rec: dict, name: str) -> dict:
    """One library record, normalised. Unknown keys are dropped."""
    rec = rec if isinstance(rec, dict) else {}
    try:
        weight = round(float(rec.get("weight", 1.0)), 3)
    except (TypeError, ValueError):
        weight = 1.0
    lo, hi = LORA_WEIGHT_BAND
    return {
        "weight": max(lo, min(hi, weight)),
        "trigger": str(rec.get("trigger") or "").strip()[:120],
        "arch": str(rec.get("arch") or "").strip()[:16],
        "notes": str(rec.get("notes") or "").strip()[:2000],
        "example": str(rec.get("example") or "").strip()[:1000],
        "enabled": bool(rec.get("enabled", True)),
    }


def _arch_mismatch(lora_arch: str, model_arch: str) -> str:
    """Why this LoRA will not do anything on this checkpoint, or "".

    Only a DIFFERENT FAMILY is a problem. Within SDXL the base model is
    irrelevant — measured, an Illustrious-trained LoRA applied cleanly to an
    SDXL checkpoint that shares no training data with it — so this must not
    fire on "xl vs xl" or every character LoRA gets flagged.
    """
    if not lora_arch or not model_arch:
        return ""
    if lora_arch == model_arch:
        return ""
    return (f"built for {lora_arch}, the loaded model is {model_arch}")


def _installed_loras(client) -> dict:
    """{name: row} for every LoRA on the server, or {} if it cannot be asked.

    A dead SD server must not take the agent down with it, so this fails to an
    empty map. The cost is that every reference then reads as "not installed",
    which is visible in the settings list and in the generation notes — far
    better than the alternative, which is silently generating without the
    character because a cache lookup raised inside a broad except.
    """
    try:
        return {r["name"]: r for r in client.list_loras()}
    except Exception:                                  # noqa: BLE001
        return {}


def card_loras(card: dict | None, model_arch: str = "",
               installed: dict | None = None) -> list[dict]:
    """Resolve a card's LoRA references into records ready to use.

    Two separate questions get answered here and it matters which is which:

    - is it ATTACHED? the card says so, and that is the whole feature
    - will it LOAD?  the file has to be on disk, and its family has to match

    A reference with no file is the normal state for a LoRA the user has
    described but not installed, so it stays in the list with `installed: False`
    rather than vanishing — otherwise attaching one and forgetting to download
    it would look like the attachment did nothing.
    """
    if not card:
        return []
    names = card.get("loras")
    if not isinstance(names, (list, tuple)):
        return []
    names = [str(n).strip() for n in names if str(n or "").strip()]
    if not names:
        return []
    lib = load_lora_library()
    installed = installed if installed is not None else {}

    out: list[dict] = []
    for name in names[:LORA_PER_CARD]:
        rec = _clean_lora_record(lib.get(name) or {}, name)
        disk = installed.get(name)
        on_disk = bool(disk)
        eff_arch = rec["arch"] or (disk or {}).get("arch") or ""
        out.append({
            "name": name,
            "label": name,
            "attached": True,
            "installed": on_disk,
            "weight": rec["weight"],
            "trigger": rec["trigger"],
            "notes": rec["notes"],
            "example": rec["example"],
            "arch": eff_arch,
            "model_arch": model_arch,
            "mismatch": _arch_mismatch(eff_arch, model_arch),
            "referenceable": lora_referenceable(name),
        })
    return out


def lora_tag_list(entries: list[dict]) -> tuple[list[str], list[dict], list[str]]:
    """`(tags, applied, why)` for `entries`.

    `tags` is what goes on the prompt, `applied` the entries that produced
    them, `why` a reason per skip. Skipping is silent in the sense that the
    image still generates — so every skip is returned and surfaced in the
    generation's notes rather than logged to nowhere.
    """
    tags: list[str] = []
    applied: list[dict] = []
    why: list[str] = []
    for e in entries or []:
        if not e.get("installed"):
            why.append(f"{e['name']}: not installed (no file in the server's "
                       f"Lora folder)")
            continue
        if e.get("mismatch"):
            why.append(f"{e['name']}: {e['mismatch']} — skipped")
            continue
        if not e.get("referenceable"):
            why.append(f"{e['name']}: name has a ':' or '>' in it, so Forge "
                       f"cannot address it — skipped")
            continue
        tag = lora_tag(e["name"], e.get("weight", 1.0))
        if not tag:
            why.append(f"{e['name']}: could not build a tag — skipped")
            continue
        tags.append(tag)
        applied.append(e)
    return tags, applied, why


# ------------------------------------------------------- Civitai browsing

# The token lives in data/, which is gitignored, and is only ever sent to
# civitai.com. Read per request rather than cached so it can be rotated
# without a restart.
CIVITAI_TOKEN_FILE = DATA_DIR / "civitai_token"
CIVITAI_API = "https://civitai.com/api/v1"
#: Note the missing `v1`. Downloads live at /api/download/models/, NOT under
#: /api/v1/ — pointing v1's base at them 404s on every file.
CIVITAI_DOWNLOAD = "https://civitai.com/api/download/models"

#: Search results change slowly and the endpoint is a public API on someone
#: else's box. Ten minutes is long enough that hammering the ↻ button does not
#: hammer them, and short enough that a LoRA published this morning shows up
#: this afternoon. Keyed by the full query, so a different search is a miss
#: rather than a wrong answer.
CIVITAI_TTL = 600
_civitai_cache: dict[str, tuple[float, dict]] = {}

CIVITAI_SORTS = ("Most Downloaded", "Highest Rated", "Newest")

#: Only meaningful with a popularity sort, so only sent alongside one.
CIVITAI_PERIODS = ("Day", "Week", "Month", "Year")

#: Civitai's base-model vocabulary mapped onto the families this app knows.
#: What matters to us is whether it can load at all, and that is a family
#: question — Illustrious and Pony are both xl, and an xl LoRA runs on any xl
#: checkpoint (measured), so collapsing them is correct rather than lossy.
CIVITAI_ARCH = (
    ("illustrious", "xl"), ("pony", "xl"), ("sdxl", "xl"),
    ("xl 1.0", "xl"), ("sd 1.5", "sd"), ("sd1.5", "sd"),
    ("sd3", "sd3"), ("flux", "flux"), ("qwen", "qwen"),
    ("anima", "anima"), ("krea", "krea"), ("wan", "wan"),
    ("z-image", "zimage"), ("zimage", "zimage"),
)


def civitai_arch(base: str) -> str:
    low = str(base or "").lower()
    for needle, arch in CIVITAI_ARCH:
        if needle in low:
            return arch
    return ""


def _civitai_get(path: str, params: dict) -> dict:
    """GET civitai with the token attached when there is one.

    A token is required for downloads from a creator-gated model and optional
    for search, so a missing one degrades the feature rather than breaking it.
    """
    url = f"{CIVITAI_API}{path}"
    hdrs = {}
    tok = ""
    try:
        tok = CIVITAI_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if tok:
        hdrs["Authorization"] = f"Bearer {tok}"
    resp = requests.get(url, params=params, headers=hdrs, timeout=30)
    if resp.status_code >= 400:
        raise SDWebUIError(f"civitai said {resp.status_code}: "
                           f"{resp.text[:160]}")
    return resp.json()


def civitai_search(query: str, sort: str = "", cursor: str = "",
                   limit: int = 24, installed: set | None = None,
                   tag: str = "", period: str = "") -> dict:
    """Browse LoRAs on Civitai, cached.

    Results come back with everything needed to decide before downloading: the
    per-VERSION base model (not the model's, which lists every base it has a
    release for), whether the box can run it, and the exact file.

    `tag` and `period` are passed through to Civitai. Two caveats worth
    keeping: an unrecognised tag is IGNORED and returns everything, so a typo
    looks like "no matches for my filter" rather than an error; and `period`
    is meaningless without a `sort` that is a popularity measure, so it is only
    sent alongside one.
    """
    query = (query or "").strip()
    tag = (tag or "").strip()
    period = (period or "").strip()
    sort = sort if sort in CIVITAI_SORTS else ""
    cursor = (cursor or "").strip()
    key = f"{query.lower()}|{tag.lower()}|{sort}|{period}|{cursor}|{limit}"
    hit = _civitai_cache.get(key)
    if hit and time.time() - hit[0] < CIVITAI_TTL:
        # copy, so the stored entry keeps `cached: False` — that is what it was
        # when Civitai was actually called, and flagging it True in place would
        # make the next reader report a cache hit on the entry it just built
        return {**hit[1], "cached": True, "age_s": int(time.time() - hit[0])}

    params = {"types": "LORA", "limit": max(1, min(50, int(limit)))}
    if query:
        params["query"] = query
    if tag:
        params["tag"] = tag
    if sort:
        params["sort"] = sort
        if period in CIVITAI_PERIODS:
            params["period"] = period
    if cursor:
        params["cursor"] = cursor
    raw = _civitai_get("/models", params)
    installed = installed if installed is not None else set()

    items = []
    for m in (raw.get("items") or []) if isinstance(raw, dict) else []:
        vers = m.get("modelVersions") or []
        # one entry per version: a model can ship an SD1.5 and an SDXL release
        # and they are not interchangeable on this box
        for v in vers[:4]:
            vfiles = [f for f in (v.get("files") or [])
                      if isinstance(f, dict) and f.get("id")]
            if not vfiles:
                continue
            # Previews hang off the VERSION, not the model — the model object
            # has no `images` key at all, which is why a first pass at this
            # silently produced zero thumbnails for every single result.
            shots = []
            for im in (v.get("images") or [])[:10]:
                if not isinstance(im, dict):
                    continue
                # Civitai hosts animated previews as .mp4 next to the stills.
                # An <img> cannot render one, and autoplaying video in a
                # settings sheet is its own problem — stills only.
                if str(im.get("type") or "image") != "image":
                    continue
                u = _civitai_thumb(str(im.get("url") or ""), 400)
                if u:
                    shots.append({
                        "url": u,
                        "nsfw": bool(im.get("nsfw")),
                        "w": int(im.get("width") or 0),
                        "h": int(im.get("height") or 0),
                    })
            trained = [str(t).strip() for t in (v.get("trainedWords") or [])
                       if str(t).strip()][:12]
            f = vfiles[0]
            name = str(f.get("name") or "")
            # Forge strips the extension for the name a card has to store
            base = name.rsplit(".", 1)[0] if "." in name else name
            bm = str(v.get("baseModel") or "")
            arch = civitai_arch(bm)
            have = base in installed
            items.append({
                "name": base,
                "file": name,
                "version_id": int(v.get("id") or 0),
                "model_id": int(m.get("id") or 0),
                "model_name": str(m.get("name") or ""),
                "version_name": str(v.get("name") or ""),
                "civitai_base": bm,
                "arch": arch,
                "size_mb": round(float(f.get("sizeKB") or 0) / 1024, 1),
                "sha256": str((f.get("hashes") or {}).get("SHA256") or ""),
                "downloads": int((m.get("stats") or {}).get("downloadCount") or 0),
                "rating": int((m.get("stats") or {}).get("thumbsUpCount") or 0),
                "nsfw": bool(m.get("nsfw")),
                "installed": have,
                "mismatch": _arch_mismatch(arch, "") if not arch else "",
                "images": shots,
                "trained": trained,
                "desc": _plain(v.get("description") or m.get("description") or ""),
                "creator": str((m.get("creator") or {}).get("username") or ""),
            })
    out = {"items": items,
           "next_cursor": str((raw.get("metadata") or {}).get("nextCursor") or ""),
           "query": query, "sort": sort,
           "cached": False, "age_s": 0}
    _civitai_cache[key] = (time.time(), out)
    return out


def _civitai_thumb(url: str, width: int = 400) -> str:
    """Civitai serves one image at many sizes by editing the path.

    Two spellings exist in the wild: `/…/original=true/x.jpeg` (what the API
    hands out) and `/…/width=525/x.jpeg` (what the site links use). Measured on
    one 2048x3072 source: original is a 3.6 MB PNG, width=400 is an 87 KB JPEG.
    A popup gallery on a phone wants the second one.
    """
    if not url:
        return ""
    return re.sub(r"/(?:original=true|width=\d+)/", f"/width={width}/",
                  url, count=1)


def _plain(html: str, limit: int = 900) -> str:
    """Civitai descriptions are HTML written for a browser.

    The popup renders textContent, so tags would show as literal angle
    brackets. This is a readable strip, not a parser — it is for a human
    skimming what a LoRA does, not for reading someone's recipe.
    """
    txt = str(html or "")
    txt = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", txt)
    txt = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h[1-6]>", "\n", txt)
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = (txt.replace("&amp;", "&").replace("&lt;", "<")
              .replace("&gt;", ">").replace("&quot;", '"')
              .replace("&#39;", "'").replace("&nbsp;", " "))
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    if len(txt) > limit:
        txt = txt[:limit].rsplit(" ", 1)[0] + "…"
    return txt


def download_lora(version_id: int, dest_dir: Path) -> dict:
    """Fetch one LoRA into Forge's Lora folder and verify it.

    The id is a **modelVersionId**, never a file id. `/api/download/models/{id}`
    resolves the version, and a file id there does not error — it silently
    serves some other model's file. Doing that hands the user a valid LoRA
    that is not the one they asked for, which is how this went wrong once
    already: a request for the Mitya LoRA returned "ViviAmano_epoch_10".
    """
    version_id = int(version_id)
    if version_id <= 0:
        raise ValueError("bad version id")
    ver = _civitai_get(f"/model-versions/{version_id}", {})
    files = [f for f in (ver.get("files") or []) if isinstance(f, dict)
             and f.get("id")]
    if not files:
        raise ValueError("that version has no downloadable file")
    f = files[0]
    name = str(f.get("name") or "")
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"unusable file name: {name!r}")
    want = str((f.get("hashes") or {}).get("SHA256") or "").lower()
    size = int(f.get("sizeKB") or 0)

    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / name
    tmp = dest_dir / (name + ".part")

    hdrs = {}
    tok = ""
    try:
        tok = CIVITAI_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if tok:
        hdrs["Authorization"] = f"Bearer {tok}"

    got = ""
    with requests.get(f"{CIVITAI_DOWNLOAD}/{version_id}",
                      headers=hdrs, stream=True, timeout=(20, 300)) as r:
        if r.status_code >= 400:
            raise ValueError(f"civitai said {r.status_code} for that download")
        h = hashlib.sha256()
        n = 0
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                if not chunk:
                    continue
                fh.write(chunk)
                h.update(chunk)
                n += len(chunk)
    got = h.hexdigest()

    if want and got != want:
        # civitai's advertised hash and the bytes it serves disagree on some
        # uploads. Refuse either way: a wrong LoRA is worse than none, and a
        # partial one is worse than both.
        tmp.unlink(missing_ok=True)
        raise ValueError(
            f"checksum mismatch for {name}\n"
            f"  expected {want}\n  got      {got}\n"
            f"Civitai's metadata and the file it serves disagree. Refusing to "
            f"install — try again, or download it by hand.")
    if size and abs(n - size * 1024) > 4096:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"size mismatch for {name}: got {n} bytes, "
                         f"expected about {int(size * 1024)}")

    tmp.replace(final)
    return {"file": name, "name": name.rsplit(".", 1)[0], "bytes": n,
            "sha256": got, "path": str(final),
            "base": str(ver.get("baseModel") or ""),
            "arch": civitai_arch(ver.get("baseModel"))}


def new_chat_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def chat_preview(payload: dict, limit: int = 120) -> str:
    """The line under a character's name in the history list: the user's own
    first message. There is no generated title — the raw message is both more
    recognisable than a two-word summary and free.

    Reads `timeline` when there is one and falls back to `messages` for chats
    saved before the timeline existed.
    """
    for evt in payload.get("timeline") or []:
        if evt.get("type") == "user":
            return (evt.get("text") or "").strip()[:limit]
    for m in payload.get("messages") or []:
        if isinstance(m, dict) and m.get("role") == "user":
            return str(m.get("content") or "").strip()[:limit]
    return ""


def _last_reply(agent: "Agent") -> str:
    """The newest assistant line on the timeline — a snippet of what was on
    screen when the user gave their verdict."""
    for evt in reversed(agent.timeline):
        if evt.get("type") == "reply":
            return str(evt.get("text") or "")[:300]
    return ""


def _clean_feedback(raw) -> dict:
    """A stored verdict, or {} when there isn't a usable one."""
    if not isinstance(raw, dict):
        return {}
    rating = str(raw.get("rating") or "").strip().lower()
    if rating not in ("like", "dislike"):
        return {}
    try:
        at = int(raw.get("at") or 0)
    except (TypeError, ValueError):
        at = 0
    return {"rating": rating, "at": at,
            "msgs": int(raw.get("msgs") or 0) if str(
                raw.get("msgs") or "0").lstrip("-").isdigit() else 0,
            "reply": str(raw.get("reply") or "")[:300]}


# ------------------------------------------------------------------ users
#
# Per-user storage (MULTIUSER.md, Phase 1). Chats, chat state, personas and
# config go under data/users/<uid>/, so backup and delete are per-user
# operations rather than per-file archaeology. The Agent is still a
# process-wide singleton until Phase 2 — only its *storage* is per-user.
#
# Characters are the exception and stay in ONE global file. They are
# shareable (MULTIUSER.md decision 6), and sharing is inherently cross-user:
# a card living in A's private directory cannot be offered to B. Splitting
# them per-user and adding a separate shared pool would be strictly more code
# for the same behaviour. So characters.json carries `owner` + `shared` and
# every read goes through visible_characters(). Decisions 6 and 7 genuinely
# conflict here; this is the resolution, and it means decision 7 holds for
# everything that can actually be isolated.


def user_dir(uid: str) -> Path:
    return DATA_DIR / "users" / (uid or "u001")


def user_state_file(uid: str, name: str) -> Path:
    return user_dir(uid) / name


def _write_json_atomic(path: Path, payload, indent=None) -> None:
    """tmp + replace. Every state write goes through this: a bare
    write_text truncated by a crash mid-write loses the whole file, and
    under multi-user that is one user's chat, not a scratch file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=indent, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(path)


def _read_json_list(path: Path) -> list:
    if not path.exists():
        return []
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def load_characters() -> list:
    return _read_json_list(CHARS_FILE)


def save_characters(chars: list) -> None:
    _write_json_atomic(CHARS_FILE, chars, indent=2)


def card_owner(card: dict) -> str:
    return str(card.get("owner") or "")


def visible_characters(uid: str, is_admin: bool = False) -> list:
    """Cards `uid` may see: their own, plus any marked shared.

    Shared is the normal case — the cast is common to everyone and ownership
    governs who may *edit* a card. Private is the opt-out, set from the
    character's Share toggle.

    Admins see everything (MULTIUSER.md decision 5 — full access), which is
    why `is_admin` is a parameter and not a lookup here: this module has no
    request context.

    The filter lives in one place rather than at every call site, because
    forgetting it in one place is a cross-tenant read."""
    out = []
    for c in load_characters():
        if not isinstance(c, dict) or not c.get("id"):
            continue
        if is_admin:
            out.append(c)
            continue
        owner = card_owner(c)
        # A card with no `shared` key predates sharing and was never private,
        # so it counts as shared rather than disappearing from everyone's cast.
        if not owner or owner == uid or c.get("shared", True):
            out.append(c)
    return out


def owned_by(uid: str) -> list:
    """Just `uid`'s own cards — what the edit/delete endpoints act on."""
    return [c for c in load_characters()
            if isinstance(c, dict) and c.get("id") and card_owner(c) == uid]


def can_edit_card(uid: str, card: dict, is_admin: bool = False) -> bool:
    """Owned cards are editable; a shared card is read-only for everyone but
    its owner. Admins bypass (MULTIUSER.md decision 5: full access)."""
    if is_admin:
        return True
    return card_owner(card) == uid


def ensure_assistant(chars: list, uid: str = "") -> list:
    """Guarantee the plain assistant card exists, appending it if missing.

    Empty persona / appearance / greeting on purpose: `_sys_msg` skips the
    character block entirely for a card with nothing in it, so this chats
    exactly like the free chat it replaced — no roleplay, no injected tags.
    Editable like any other card; deleting it just brings it back.

    `shared` is set on purpose. Characters live in one global file with an
    owner, and there is only one assistant *id*, so per-user assistant cards
    are impossible. Without `shared` the plain chat would be visible only to
    whoever happened to create it, and every other user would open the app to
    an empty cast. Consequence to know about: it is owned by the first admin,
    so editing it is owner-only until Phase 2. The clean fix is per-user
    character storage, which is what Phase 1 traded away to make sharing work.
    """
    for c in chars:
        if c.get("id") == ASSISTANT_ID:
            if not c.get("shared"):
                c["shared"] = True          # late adopters still see it
                try:
                    save_characters(chars)
                except OSError:
                    pass
            return chars
    chars.append({"id": ASSISTANT_ID, "created": int(time.time()),
                  "name": "Assistant", "system": True, "shared": True,
                  "owner": uid,
                  "appearance": "", "persona": "", "greeting": "",
                  "checkpoint": "", "size": [], "temp": None,
                  "max_tokens": None, "loras": [], "tags": []})
    try:
        save_characters(chars)
    except OSError:
        pass
    return chars


def _fav_at(card: dict) -> float:
    """When a card was pinned, for the favourites-first ordering. Cards
    pinned before this existed (or hand-edited) sort as oldest."""
    try:
        return float(card.get("fav_at") or 0)
    except (TypeError, ValueError):
        return 0.0


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:40] or "char"


# ------------------------------------------------------------------- covers

def cover_file(key: str) -> Path | None:
    """Existing cover image for a key: covers/<key>.<ext>."""
    if not COVER_KEY_RE.match(key or ""):
        return None
    try:
        for f in COVER_DIR.glob(key + ".*"):
            return f
    except OSError:
        pass
    return None


def cover_url(key: str) -> str:
    """Public URL of a cover (mtime-versioned), or '' when there is none."""
    f = cover_file(key)
    if f and f.is_file():
        try:
            return ("/api/cover/" + key + "?v="
                    + str(int(f.stat().st_mtime)))
        except OSError:
            pass
    return ""


def write_cover(key: str, dataurl: str) -> bool:
    """Decode a data:image URL into covers/<key>.<ext>, replacing any
    previous cover for the key (the extension may change between saves)."""
    m = re.match(r"data:image/(png|jpe?g|webp);base64,(.+)", dataurl, re.S)
    if not m or len(dataurl) > 12_000_000:
        return False
    ext = "jpg" if m.group(1).startswith("jp") else m.group(1)
    try:
        data = base64.b64decode(m.group(2))
        COVER_DIR.mkdir(parents=True, exist_ok=True)
        for old in COVER_DIR.glob(key + ".*"):
            old.unlink()
        (COVER_DIR / (key + "." + ext)).write_bytes(data)
        return True
    except Exception:
        return False


def remove_cover(key: str) -> None:
    try:
        for old in COVER_DIR.glob(key + ".*"):
            old.unlink()
    except OSError:
        pass


# Meta prompt for the character form's AI assistant. Deliberately independent
# of the roleplay system prompt.
#
# This is a CONVERSATION, not a one-shot form filler. The user describes a
# character, the model drafts the card, and then the user keeps talking to it
# to adjust one field at a time - including hand-edits made in the form
# between turns, which the model sees as the live card state.
#
# The response is therefore a PATCH: only the keys the model actually changed.
# A key it omits is left alone. That is the whole contract - the client used
# to assign `d.persona || ""` into the form, so an omitted key ERASED the
# field, which turned any partial or verbose reply into silent data loss.
CHAR_MAKER_PROMPT = (
    "You help build a roleplay character card for a chat app that also "
    "generates images with Stable Diffusion. This is a conversation: the user "
    "describes a character, you draft the card, and they then keep talking to "
    "you to adjust it. Reply with ONLY a JSON object - no markdown, no "
    "commentary - shaped like this:\n"
    '"reply": one short sentence saying what you changed, or asking the one '
    "question you need answered\n"
    "plus ONLY the keys you actually changed this turn, which are any of:\n"
    '"name": the character\'s name (1-4 words)\n'
    '"appearance": Stable Diffusion booru tags for their PERMANENT physical '
    "look - hair, eyes, skin, build, age, species, permanent markings. "
    "Comma-separated, 8-16 tags, and stop as soon as the identity is "
    "complete rather than padding to the count.\n"
    "  This one field is force-injected into EVERY image of the character "
    "for the life of the card, so it must describe the person and never a "
    "moment. Do NOT include: backgrounds or settings (bedroom, dungeon, "
    "forest, stone wall); props or restraints (chains, shackles, rope, "
    "collar); temporary condition (dirty, wounded, wet, clothed or not, "
    "torn); pose, action or expression. You will want to describe the "
    "opening scene - put it in the greeting, which is the only field "
    "where a scene belongs.\n"
    '"persona": how they think, speak and behave - 3-6 sentences written as '
    "directions for a roleplay system prompt\n"
    '"greeting": their first message in a new chat, in character, with '
    "*actions in asterisks*, 2-5 sentences\n"
    "\n"
    "HOW TO PATCH:\n"
    "- Return ONLY the fields you changed. Any key you omit is left EXACTLY "
    "as it is, including edits the user made by hand since your last turn.\n"
    "- NEVER return an empty string for a field. An empty value erases what "
    "is there; if you have nothing to say about a field, omit the key.\n"
    "- Most turns change ONE field. 'make the first message shorter' returns "
    "only greeting. 'give her a scar' returns only appearance. Do not "
    "rewrite the other three to 'refresh' them - that discards the user's "
    "edits and reads as the assistant ignoring them.\n"
    "- The user may ask you to undo: 'revert', 'undo that', 'that sucked', "
    "'put it back'. Reply {\"revert\": true, \"reply\": \"...\"} and no "
    "field keys at all.\n"
    "- If the request is a question, or too vague to act on, return only "
    '"reply" and no field keys.\n'
    'First turn example: {"reply": "Here\'s a starting point.", "name": '
    '"...", "appearance": "...", "persona": "...", "greeting": "..."}\n'
    'Revision example: {"reply": "Shortened and plainer.", "greeting": '
    '"..."}')


# ------------------------------------------------------------------ personas

PERSONAS_FILE = DATA_DIR / "personas.json"


def load_personas(uid: str) -> list:
    return _read_json_list(user_state_file(uid, "personas.json"))


def save_personas(uid: str, lst: list) -> None:
    _write_json_atomic(user_state_file(uid, "personas.json"), lst)


def adopt_legacy_state(uid: str) -> None:
    """One-time Phase 1 move of pre-multi-user state into a user's directory.

    The existing chat history, personas and restart mirror predate ownership,
    so they belong to the first admin. Runs every boot but is a no-op once
    the per-user files exist, and it refuses to overwrite anything."""
    d = user_dir(uid)
    d.mkdir(parents=True, exist_ok=True)
    moved = []
    for legacy, dest in ((STATE_FILE, "chat_state.json"),
                         (PERSONAS_FILE, "personas.json")):
        if legacy.exists() and not (d / dest).exists():
            legacy.replace(d / dest)
            moved.append(dest)
    # chats/ is the whole directory, so it moves as one unit.
    legacy_chats = CHATS_DIR
    # Phase 3: the live config becomes the owner's own, key and all. It stays
    # where it is as the template new accounts start from — but that means a
    # fresh account must not inherit the key, which load_config(uid) enforces
    # by blanking it when the per-user file does not yet exist.
    if CONFIG_FILE.exists() and not (d / "config.json").exists():
        shutil.copy2(CONFIG_FILE, d / "config.json")
        moved.append("config.json")
    # Phase 4: images predate ownership, so everything loose in outputs/ belongs
    # to the owner — the files *and* the character folders, which are where
    # most of the library actually lives. Runs once: any entry already under
    # outputs/<uid>/ was written by a per-user generation and is left alone.
    out_owner = OUT_DIR / uid
    img_exts = (".png", ".jpg", ".jpeg", ".webp")
    loose = [p for p in OUT_DIR.iterdir()
             if p.name != uid                       # never move the owner in
             and not p.name.startswith(".")         # .thumbs etc stay put
             and ((p.is_file() and p.suffix.lower() in img_exts)
                  or p.is_dir())]
    if loose:
        out_owner.mkdir(parents=True, exist_ok=True)
        n_files = 0
        for p in loose:
            # One stubborn entry must not abort the rest: a partial move is
            # re-runnable (the merge branch below), an exception is not.
            try:
                dest = out_owner / p.name
                if dest.exists() and p.is_dir():
                    for sub in p.rglob("*"):
                        if sub.is_file():
                            d = dest / sub.relative_to(p)
                            d.parent.mkdir(parents=True, exist_ok=True)
                            if not d.exists():
                                sub.replace(d)
                                n_files += 1
                    continue
                if dest.exists():       # same-named file: keep both
                    stem, dot, ext = p.name.rpartition(".")
                    dest = out_owner / f"{stem}-moved{dot}"
                p.replace(dest)
                n_files += 1
            except OSError:
                continue
        moved.append(f"{n_files} images")

    # Now that the live copy exists, the template should carry no secret.
    # load_config(uid) already blanks the key for a user with no file yet;
    # this is the belt to that braces — a template that still contains the
    # operator's key is one bug away from handing it to the next signup.
    if CONFIG_FILE.exists():
        try:
            tmpl = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(tmpl, dict) and tmpl.get("openrouter_key"):
                owner_cfg = d / "config.json"
                if owner_cfg.exists():
                    tmpl["openrouter_key"] = ""
                    _write_json_atomic(CONFIG_FILE, tmpl, indent=2)
                    moved.append("config.json key stripped from template")
        except Exception:                                 # noqa: BLE001
            pass
    if legacy_chats.is_dir() and not (d / "chats").exists():
        legacy_chats.replace(d / "chats")
        moved.append("chats/")
    # Character cards stay global (see the note above user_dir), but they
    # gain an owner the first time so the visibility filter has something
    # to match on.
    cards = load_characters()
    if cards and any(not card_owner(c) for c in cards if isinstance(c, dict)):
        for c in cards:
            if isinstance(c, dict) and not card_owner(c):
                c["owner"] = uid
        save_characters(cards)
        moved.append("characters.json owners")
    # Sharing is the default, so a card written before the flag was never
    # private — it just has no `shared` key. Set it explicitly so the stored
    # file says what the behaviour already was.
    if cards and any("shared" not in c for c in cards if isinstance(c, dict)):
        for c in cards:
            if isinstance(c, dict):
                c.setdefault("shared", True)
        save_characters(cards)
        moved.append("characters.json shared")
    if moved:
        print(f"  phase 1: adopted {', '.join(moved)} into users/{uid}")


def migrate_flat_chats(uid: str) -> None:
    """Phase A → B: move single-file chats/<char>.json into per-chat files."""
    chats_dir = user_dir(uid) / "chats"
    default_file = chats_dir / "default.json"
    try:
        for f in chats_dir.glob("*.json"):
            if f == default_file:
                continue                     # legacy free chat, not a card
            cid = f.stem
            if not CHAR_ID_RE.match(cid):
                continue
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            cid_dir = chats_dir / cid
            cid_dir.mkdir(parents=True, exist_ok=True)
            chat_id = new_chat_id()
            payload = {
                "id": chat_id,
                "updated": int(f.stat().st_mtime),
                "messages": d.get("messages") or [],
                "timeline": d.get("timeline") or [],
            }
            (cid_dir / f"{chat_id}.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            f.unlink()
    except Exception:
        pass


# ------------------------------------------------------------------ agent

class Agent:
    """Conversation + SD client, driven by user-supplied config."""

    def __init__(self, cfg: dict, uid: str = ""):
        self.cfg = cfg
        # Whose chat tree this Agent persists to. Phase 1: still a single
        # process-wide Agent, bound to the first admin's id. Phase 2 gives
        # each user their own instance.
        self.uid = uid or "u001"
        self.client = SDClient(base_url=cfg["sd_url"])
        self.char = None                 # active character card or None
        self.chat_id = ""                # active chat id (bound chats)
        self.scenario_id = ""            # scenario bound to the active chat
        self.auto_images = True          # per-chat: may the model use image tools?
        self.feedback = {}               # verdict on the conversation as it
        self.feedback_log = []           # stands; verdicts it has moved past
        self._home = self.default_chat_file()  # where the active chat persists
        self.messages = [self._sys_msg()]
        self.timeline = []
        self.lock = threading.Lock()     # one turn at a time
        self.stop_flag = threading.Event()  # set by /api/abort
        self.status_cb = None            # set while a turn streams
        self._save_lock = threading.Lock()   # chat-file writes
        self._load_state()
        self._rehydrate_char()

    # ------------------------------------------------------------ config

    def persona(self) -> dict:
        """The active user persona entity, or {} when none is selected."""
        pid = (self.cfg.get("persona_id") or "").strip()
        if not pid:
            return {}
        return next((p for p in load_personas(self.uid)
                     if p.get("id") == pid), {})

    def persona_appearance(self) -> str:
        """The user's booru-tag look, auto-injected when a shot draws them."""
        return str(self.persona().get("appearance") or "")

    def _sys_msg(self) -> dict:
        """Opening system message: global override (or default) + user
        persona (or plain username) + the active character's block."""
        base = effective_system_prompt(self.cfg.get("system_prompt"),
                                       self.cfg.get("model_guide"))
        persona = self.persona()
        if persona:
            nm = str(persona.get("name") or "").strip()[:80]
            ds = str(persona.get("description") or "").strip()[:4000]
            ap = str(persona.get("appearance") or "").strip()[:4000]
            block = "\n\nUser Persona:"
            if nm:
                block += f"\nName: {nm}"
            if ds:
                block += f"\nDescription: {ds}"
            if ap:
                block += ("\nAppearance — these tags are authoritative and the "
                          "app injects them automatically into every image that "
                          "draws the user, so you do not need to write them "
                          "yourself:\n" + ap)
            base += block
        else:
            user = (self.cfg.get("username") or "").strip()
            if user:
                base += f"\n\nThe user's name is {user[:60]}."
        c = self.char
        if c and any(str(c.get(k) or "").strip()
                     for k in ("persona", "appearance", "greeting")):
            # a card with nothing filled in (the plain assistant) contributes
            # no block at all — there is no one to roleplay as
            persona = str(c.get("persona") or "").strip()
            appearance = str(c.get("appearance") or "").strip()
            parts = ["\n\nYou are roleplaying as "
                     + (str(c.get("name") or "?").strip() or "?") + "."]
            if persona:
                parts.append("Personality: " + persona)
            if c.get("greeting"):
                parts.append("Your greeting (the chat's first message) was: "
                             + c["greeting"].strip())
            if appearance:
                parts.append("Your appearance — reflect these tags in every "
                             "image prompt that shows you. If your list opens "
                             "with a count tag (1boy / 1girl) that tag counts "
                             "only YOU: when anyone else is in the shot, DELETE "
                             "it and write the total instead — two males are "
                             "`duo, 2boys`, two females `duo, 2girls`, mixed "
                             "`duo, 1boy, 1girl`. Never leave your own `1boy` "
                             "sitting next to `2boys`: the two counts are taken "
                             "as three people and the model resolves that by "
                             "merging the pair into one, so the character comes "
                             "out wearing the user's hair, eyes and glasses while "
                             "the user is left as a stranger in the background.\n"
                             + appearance)
            base += "\n".join(parts)
        # LoRA notes ride the card, so they are scoped to this conversation and
        # cost nothing anywhere else. Deliberately outside the persona guard
        # above: a card can have a LoRA and no written appearance yet, and the
        # trigger word is exactly what the model needs in that case. The plain
        # assistant has no LoRAs, so it still contributes nothing.
        if c:
            base += lora_prompt_block(self.lora_entries())
        sc = None
        if self.scenario_id and c:
            sc = next((s for s in (c.get("scenarios") or [])
                       if s.get("id") == self.scenario_id), None)
        if sc:
            base += ("\n\nScenario Context:\nScenario: "
                     + (sc.get("name") or "").strip()[:80]
                     + "\n" + (sc.get("description") or "").strip()[:4000])
        if not self.auto_images:
            # the image tools are withheld for this chat, so say so up front —
            # otherwise the model apologises for "having no tools" every turn
            base += ("\n\nImage generation is currently OFF in this chat: you "
                     "have no image tools. Never claim to have drawn, generated "
                     "or attached anything, and never promise an image later — "
                     "just roleplay in text.")
        return {"role": "system", "content": base}

    def char_tags(self) -> str:
        return (self.char or {}).get("appearance", "") or ""

    def lora_entries(self) -> list[dict]:
        """The active card's LoRAs, resolved against what is on disk.

        Safe to call before Forge answers: `_sys_msg` runs at startup, and a
        dead SD server must not stop the agent from booting with a system
        message. A LoRA that cannot be resolved yet still comes back, marked
        not-installed, so the character block is not silently shorter than it
        will be once the server is up.
        """
        try:
            arch = self.client.current_arch()
        except Exception:                              # noqa: BLE001
            arch = ""
        try:
            return card_loras(self.char, arch,
                              _installed_loras(self.client))
        except Exception:                              # noqa: BLE001
            return []

    def saved_components(self, arch: str) -> dict:
        """Companion files configured for an architecture in Settings.

        Filled into a tool call's arguments before it runs, so switching to a
        Qwen checkpoint re-attaches the Qwen text encoder and VAE in the same
        options write as the switch itself (one model load, not two).
        """
        comps = (self.cfg.get("sd_components") or {}).get(
            (arch or "").lower())
        return dict(comps) if isinstance(comps, dict) else {}

    def set_auto_images(self, on: bool) -> bool:
        """Turn the model's image tools on/off for the active chat.

        Per chat, so each archived chat remembers its own setting. Takes
        effect on the next turn; the system message is rebuilt so the model
        knows the tools are gone.
        """
        self.auto_images = bool(on)
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0] = self._sys_msg()
        self._save_state()
        return self.auto_images

    def apply_config(self, cfg: dict):
        """Hot-apply new settings (LLM chain / key / SD URL / system msg)."""
        self.cfg = cfg
        if self.client.base_url != cfg["sd_url"]:
            self.client = SDClient(base_url=cfg["sd_url"])
            # the cached checkpoint list, architecture and capability lists
            # describe the server we just left — keeping them would show this
            # one checkpoints it does not have, which is why a switch used to
            # look like the model list had simply stopped working
            state = getattr(self, "sd_state", None)
            if state:
                state.reset()
        # keep the running conversation on the new system message
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0] = self._sys_msg()

    @property
    def llm_models(self) -> list[str]:
        return self.cfg["llm_models"]

    def has_key(self) -> bool:
        return bool(self.cfg["openrouter_key"])

    # ------------------------------------------------------------ events

    def emit_status(self, text: str):
        if self.status_cb:
            try:
                self.status_cb({"type": "status", "text": text})
            except Exception:
                pass

    # ------------------------------------------------------------ stopping

    def request_stop(self):
        """Ask the running turn (or image job) to stop as soon as it can."""
        self.stop_flag.set()
        self.emit_status("stopping…")
        try:
            self.client.interrupt()        # cuts an in-flight SD job short
        except Exception:
            pass

    def stopped(self) -> bool:
        return self.stop_flag.is_set()

    # ----------------------------------------------------------- timeline

    def push(self, evt: dict, emit=None) -> dict:
        """Append to the timeline and stream it with its true index.

        The index is what /api/delete_event and /api/edit_event address, so
        the client must never have to count on its own — streamed-only events
        (tool_start, progress) would desync a client-side counter. The stored
        event stays index-free: array position is the truth after a deletion.
        """
        self.timeline.append(evt)
        if emit is not None:
            emit(dict(evt, idx=len(self.timeline) - 1))
        return evt

    # -------------------------------------------------------------- LLM

    def llm_complete(self, messages: list, temperature: float | None = None,
                     max_tokens: int | None = None) -> dict:
        """LLMRouter logic inline: try models in order, retry rate limits,
        stick with whichever model last worked.

        When the chat's auto-images toggle is off the image tools are withheld
        from the request, so the turn is plain text — the model has no way to
        generate even if it decides to."""
        models = self.llm_models
        order = models + models[:1] * 0            # copy
        tools: list = [] if not self.auto_images else None
        last_err: LLMError | None = None
        for model in order:
            for attempt in range(2):               # one retry per model
                try:
                    self.emit_status(f"thinking · {model.split('/')[-1]}")
                    return chat_completion(self.cfg["openrouter_key"],
                                           model, messages,
                                           temperature=temperature,
                                           max_tokens=max_tokens,
                                           tools=tools)
                except LLMError as e:
                    last_err = e
                    if _is_auth_error(e):          # bad key: don't retry
                        raise LLMError(
                            "OpenRouter rejected the API key "
                            "(auth error). Check it in Settings.") from e
                    if attempt == 0:
                        self.emit_status(
                            f"{model.split('/')[-1]} hiccup — retrying…")
                    time.sleep(5 * (attempt + 1))
        raise LLMError(
            f"all LLM models failed; last error: {last_err}. "
            "Free models are rate-limited — try again shortly or use a paid "
            "model in Settings.")

    # -------------------------------------------------------------- turn

    # --------------------------------------------------------- characters

    @property
    def chats_dir(self) -> Path:
        """This user's chat tree. The layout inside it is unchanged
        (`chats/<char>/<chat_id>.json`) — the user directory supplies the
        isolation, so Phase 1 does not have to reshape every chat path."""
        return user_dir(self.uid) / "chats"

    @property
    def state_file(self) -> Path:
        """The restart mirror. Per-user: it records who was last looking at
        which chat, which is meaningless across users."""
        return user_dir(self.uid) / "chat_state.json"

    def default_chat_file(self) -> Path:
        return self.chats_dir / "default.json"

    def _chat_file(self, cid: str, chat_id: str = "") -> Path:
        """Phase B: per-chat files under chats/<char>/<chat_id>.json.
        Without a chat id this resolves the legacy single-file location."""
        if chat_id:
            return self.chats_dir / cid / f"{chat_id}.json"
        return self.chats_dir / f"{cid}.json"

    def _char_dir(self, cid: str) -> Path:
        return self.chats_dir / cid

    def _list_chats(self, cid: str) -> list[Path]:
        """Chat files of a character, newest first."""
        try:
            files = [p for p in self._char_dir(cid).glob("*.json")
                     if CHAT_ID_RE.match(p.stem)]
        except OSError:
            return []
        try:
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return []
        return files

    @staticmethod
    def _load_chat_payload(path: Path) -> dict:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _apply_chat_payload(self, d: dict):
        self.scenario_id = str(d.get("scenario_id") or "")
        # absent = written before the toggle existed -> default to on
        self.auto_images = d.get("auto_images", True) is not False
        self.feedback = _clean_feedback(d.get("feedback"))
        log = d.get("feedback_log")
        self.feedback_log = [f for f in (log or []) if isinstance(f, dict)] \
            if isinstance(log, list) else []
        msgs = d.get("messages")
        self.messages = [self._sys_msg()] + \
            (msgs if isinstance(msgs, list) else [])
        self.timeline = d.get("timeline") or []

    def _fresh_chat(self, scenario_id: str = ""):
        self.scenario_id = scenario_id
        self.auto_images = True          # a fresh chat starts with images on
        self.feedback = {}
        self.feedback_log = []
        self.messages = [self._sys_msg()]
        self.timeline = []
        self._seed_greeting()

    # ------------------------------------------------------------ feedback

    def set_feedback(self, rating: str) -> dict:
        """Rate the conversation as it stands: "like", "dislike", or "" to
        clear. The same verdict twice clears it, the opposite one replaces
        it — there is one rating per chat, not a per-message scoreboard.

        Deliberately captured and not acted on. The point is to collect what
        the user thought of a conversation while it is in front of them.
        """
        rating = (rating or "").strip().lower()
        if rating not in ("like", "dislike"):
            self.feedback = {}
        elif self.feedback.get("rating") == rating:
            self.feedback = {}
        else:
            self.feedback = {"rating": rating, "at": int(time.time()),
                             "msgs": len(self.messages),
                             "reply": _last_reply(self)}
        self._save_state()
        return self.feedback

    def clear_feedback(self) -> None:
        """The conversation moved on, so the verdict no longer describes it.

        The old one is kept: archived in `feedback_log` with the message count
        it was given at, which is what makes the archive analysable later.
        Saves here rather than trusting the caller — the turn paths all do
        save afterwards, but the archive should not depend on that.
        """
        if not self.feedback:
            return                         # nothing rated, nothing to write
        self.feedback_log.append(dict(self.feedback))
        del self.feedback_log[:-200]        # keep a chat's archive bounded
        self.feedback = {}
        self._save_state()

    def _seed_greeting(self):
        """Opening events of a fresh chat: a cover image (the bound
        scenario's first image, else the character's) and the first AI
        message — the scenario's first message if there is one, else the
        character's greeting."""
        cov = ""
        g = ""
        if self.scenario_id and self.char:
            sc = next((s for s in (self.char.get("scenarios") or [])
                       if s.get("id") == self.scenario_id), None)
            if sc:
                g = (sc.get("first_message") or "").strip()
                cov = cover_url("sc-" + self.char["id"] + "."
                                + self.scenario_id)
        if not g:
            g = (self.char or {}).get("greeting", "").strip()
        if not cov and self.char:
            cov = cover_url("char-" + self.char["id"])
        if cov:
            self.timeline.append({"type": "cover", "src": cov})
        if g:
            self.messages.append({"role": "assistant", "content": g})
            self.timeline.append({"type": "reply", "text": g})

    def set_character(self, card: dict | None) -> None:
        """Switch to a character (their newest chat). None means no character
        at all — the assistant card covers the plain chat, this is only the
        fallback. Caller must hold the agent lock."""
        self._save_state()                       # flush current chat home
        self.char = card
        if card:
            cid = card["id"]
            files = self._list_chats(cid)
            if files:
                self.chat_id = files[0].stem
                self._home = files[0]
                self._apply_chat_payload(self._load_chat_payload(files[0]))
            else:
                self.chat_id = new_chat_id()
                self._home = self._chat_file(cid, self.chat_id)
                self._fresh_chat()
        else:
            self.chat_id = ""
            self._home = self.default_chat_file()
            self._apply_chat_payload(self._load_chat_payload(
                self.default_chat_file()))
        self._save_state()

    def bind_active_chat(self, card: dict) -> None:
        """Attach the current (unbound, non-empty) chat to a new character:
        it becomes that character's first chat file."""
        self.char = card
        self.chat_id = new_chat_id()
        self._home = self._chat_file(card["id"], self.chat_id)
        self._save_state()

    def delete_character(self, cid: str) -> None:
        """Remove a character's chat directory. Caller must hold the agent
        lock when the character is active."""
        try:
            shutil.rmtree(self._char_dir(cid), ignore_errors=True)
        except OSError:
            pass

    def new_chat(self, scenario_id: str = "") -> None:
        """Start a fresh chat for the active character — the old one stays
        archived. With no character at all (the fallback above) it resets
        in place instead."""
        if not self.char:
            self.scenario_id = ""
            self.auto_images = True
            self.messages = [self._sys_msg()]
            self.timeline = []
            self._save_state()
            return
        self._save_state()
        self.chat_id = new_chat_id()
        self._home = self._chat_file(self.char["id"], self.chat_id)
        self._fresh_chat(scenario_id)
        self._save_state()

    def select_chat(self, cid: str, chat_id: str) -> None:
        """Open one chat, switching character first if it is not the active
        one. The history list spans every character, so the chat the user
        picked is regularly not the current character's."""
        if not (self.char and self.char["id"] == cid):
            card = next((c for c in load_characters()
                         if c.get("id") == cid), None)
            if not card:
                raise ValueError("character not found")
            self.set_character(card)      # flushes home, opens their newest
        path = self._chat_file(cid, chat_id)
        if not path.exists():
            raise ValueError("chat not found")
        self.chat_id = chat_id
        self._home = path
        self._apply_chat_payload(self._load_chat_payload(path))
        self._save_state()

    def delete_chat(self, cid: str, chat_id: str) -> None:
        """Delete a chat; if it was the active one, fall back to the
        character's newest remaining chat (or a fresh one)."""
        active = bool(self.char and self.char["id"] == cid
                      and self.chat_id == chat_id)
        try:
            self._chat_file(cid, chat_id).unlink(missing_ok=True)
        except OSError:
            pass
        if active and self.char:
            cid = self.char["id"]
            files = self._list_chats(cid)
            if files:
                self.chat_id = files[0].stem
                self._home = files[0]
                self._apply_chat_payload(self._load_chat_payload(files[0]))
            else:
                self.chat_id = new_chat_id()
                self._home = self._chat_file(cid, self.chat_id)
                self._fresh_chat()
            self._save_state()

    def _adopt_free_chat(self, card: dict, active: bool) -> None:
        """The unbound chat (chats/default.json) is the assistant character's
        now, so its file moves into their chat directory and its history
        survives the change.

        `active` says the free chat is the chat that was open, which is what
        makes it the assistant's *active* chat as well. A legacy file can
        outlive that — someone may have been chatting as a character for
        weeks — so the file migration runs either way, but it must not
        disturb the live state of whoever is active.
        """
        cid = card["id"]

        def drop_legacy():
            try:
                self.default_chat_file().unlink(missing_ok=True)
            except OSError:
                pass

        if active:
            files = self._list_chats(cid)
            self.char = card
            if files:
                self.chat_id = files[0].stem
                self._home = files[0]
                self._apply_chat_payload(self._load_chat_payload(files[0]))
                self._save_state()
                return
            self.chat_id = new_chat_id()
            self._home = self._chat_file(cid, self.chat_id)
            self._apply_chat_payload(self._load_chat_payload(self.default_chat_file()))
            self._save_state()        # writes chats/<cid>/<chat_id>.json
            drop_legacy()
            return

        if self._list_chats(cid) or not self.default_chat_file().exists():
            return                     # already migrated, or nothing to move
        payload = self._load_chat_payload(self.default_chat_file())
        if not (payload.get("messages") or payload.get("timeline")):
            return
        chat_id = new_chat_id()
        home = self._chat_file(cid, chat_id)
        payload = dict(payload, id=chat_id, character_id=cid)
        home.parent.mkdir(parents=True, exist_ok=True)
        home.write_text(json.dumps(payload, ensure_ascii=False),
                        encoding="utf-8")
        drop_legacy()

    def _rehydrate_char(self):
        """Restore the active character (and their active chat) at startup."""
        cid = getattr(self, "_state_char_id", "")
        self._state_char_id = ""
        cards = load_characters()
        assistant = next((c for c in cards if c.get("id") == ASSISTANT_ID), None)
        if not cid:
            # no character was active — that used to be a separate mode and
            # is the assistant character now
            if assistant:
                self._adopt_free_chat(assistant, active=True)
            return
        if assistant:
            self._adopt_free_chat(assistant, active=False)
        card = next((c for c in cards if c.get("id") == cid), None)
        if card:
            self.char = card
            if self.messages and self.messages[0].get("role") == "system":
                self.messages[0] = self._sys_msg()
            if self.chat_id and CHAT_ID_RE.match(self.chat_id) \
                    and self._chat_file(cid, self.chat_id).exists():
                self._home = self._chat_file(cid, self.chat_id)
                return
            # mirror predates Phase B (or chat vanished) — adopt the newest
            files = self._list_chats(cid)
            if files:
                self.chat_id = files[0].stem
                self._home = files[0]
                self._apply_chat_payload(self._load_chat_payload(files[0]))
                return
            self.chat_id = new_chat_id()
            self._home = self._chat_file(cid, self.chat_id)
            self._fresh_chat()
            self._save_state()
        else:
            # mirror references a character that no longer exists — unbind
            self._home = self.default_chat_file()
            self.chat_id = ""

    # -------------------------------------------------------- persistence

    def _save_state(self):
        """Persist the active chat to its home file + the restart mirror."""
        try:
            with self._save_lock:
                self._save_state_locked()
        except Exception:
            pass

    def _save_state_locked(self):
        """_save_state's body. Kept separate so a caller that already holds
        _save_lock can write without deadlocking on it."""
        now = int(time.time())
        mirror = {
            "character_id": self.char["id"] if self.char else "",
            "chat_id": self.chat_id if self.char else "",
            "scenario_id": self.scenario_id,
            "auto_images": self.auto_images,
            "feedback": self.feedback,
            "messages": self.messages[1:],
            "timeline": self.timeline,
        }
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if self.char:
            cid = self.char["id"]
            if CHAT_ID_RE.match(self.chat_id):
                self._char_dir(cid).mkdir(parents=True, exist_ok=True)
                payload = {
                    "id": self.chat_id,
                    "character_id": cid,
                    "updated": now,
                    "scenario_id": self.scenario_id,
                    "auto_images": self.auto_images,
                    "feedback": self.feedback,
                    "feedback_log": self.feedback_log,
                    "messages": mirror["messages"],
                    "timeline": self.timeline,
                }
                self._home.write_text(
                    json.dumps(payload, ensure_ascii=False),
                    encoding="utf-8")
        else:
            payload = {"messages": mirror["messages"],
                       "auto_images": self.auto_images,
                       "feedback": self.feedback,
                       "timeline": self.timeline}
            self.chats_dir.mkdir(parents=True, exist_ok=True)
            _write_json_atomic(self.default_chat_file(), payload)
        _write_json_atomic(self.state_file, mirror)

    def _load_state(self):
        """Startup: restore the active chat from the restart mirror."""
        self.chat_id = ""
        self.auto_images = True
        if not self.state_file.exists():
            self._home = self.default_chat_file()
            return
        try:
            d = json.loads(self.state_file.read_text(encoding="utf-8"))
            self._state_char_id = d.get("character_id", "")
            self.chat_id = d.get("chat_id", "")
            self.scenario_id = str(d.get("scenario_id") or "")
            self.auto_images = d.get("auto_images", True) is not False
            self.feedback = _clean_feedback(d.get("feedback"))
            if isinstance(d.get("messages"), list):
                self.messages = [self._sys_msg()] + d["messages"]
            self.timeline = d.get("timeline") or []
        except Exception:
            self._state_char_id = ""
        if self._state_char_id and CHAT_ID_RE.match(self.chat_id):
            self._home = self._chat_file(self._state_char_id, self.chat_id)
        elif self._state_char_id:
            self._home = None            # resolved by _rehydrate_char
        else:
            self._home = self.default_chat_file()

    # -------------------------------------------------------------- turn

    def run_turn(self, user_text: str, emit) -> None:
        self.messages.append({"role": "user", "content": user_text})
        self.push({"type": "user", "text": user_text}, emit)
        self._complete(self.messages, emit)

    def resume_turn(self, messages: list, emit) -> bool:
        """Re-ask the model on `messages`, a working copy of the context.

        Nothing on the agent is touched until the caller commits, so a stop
        (or any failure) leaves the conversation exactly as it was.
        Returns True when a reply actually landed."""
        return self._complete(messages, emit)

    def _complete(self, messages: list, emit) -> bool:
        reply = None
        char_temp = None
        char_mt = None
        if self.char:
            try:
                char_temp = float(self.char["temp"])  # type: ignore[arg-type]
            except (KeyError, TypeError, ValueError):
                char_temp = None
            try:
                char_mt = int(self.char["max_tokens"])  # type: ignore[arg-type]
            except (KeyError, TypeError, ValueError):
                char_mt = None
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                if self.stopped():
                    reply = None
                    break
                data = self.llm_complete(messages,
                                         temperature=char_temp,
                                         max_tokens=char_mt)
                msg = data["choices"][0]["message"]
                messages.append(_clean_assistant_msg(msg))

                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    # an empty final message is normal now that the model
                    # isn't asked to describe the result — just show the card
                    reply = scrub_paths(msg.get("content") or "").strip() \
                        or None
                    break

                for tc in tool_calls:
                    if self.stopped():
                        break
                    name = tc["function"]["name"]
                    try:
                        args = json.loads(
                            tc["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = self._run_tool(name, args, emit)
                    # keep the context lean: the "gen" snapshot is UI-only,
                    # but the prompt it held is NOT — the next turn has to
                    # know which prompt actually produced the image on screen
                    content = {k: v for k, v in result.items() if k != "gen"}
                    gen = result.get("gen") or {}
                    if gen.get("prompt"):
                        content["prompt"] = gen["prompt"]
                    files = content.get("saved_files") or []
                    if files:
                        # stable lineage id: survives every regeneration
                        content["series"] = files[0]
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "name": name,
                        "content": json.dumps(content, ensure_ascii=False),
                    })
            else:
                reply = "(stopped: too many tool rounds)"
        except LLMError as e:
            evt = {"type": "error", "text": f"✗ {e}"}
            self.push(evt, emit)
            return False

        if self.stopped():
            # the assistant message we just appended stays in context but gets
            # no bubble — the user asked to stop, not to read a half answer
            emit({"type": "stopped"})
            return False

        if reply:                                 # empty final reply: no bubble
            # "mi" = messages length after appending this assistant message,
            # so a later edit/regenerate can rewind the context exactly
            evt = {"type": "reply", "text": reply, "mi": len(messages)}
            self.push(evt, emit)
            # a verdict rated the conversation *before* this reply, so it no
            # longer describes it — archive it and start the chat unrated
            self.clear_feedback()
            return True
        return False

    def reply_context(self, idx: int) -> list:
        """The message list the model should see to re-answer timeline `idx`.

        A slice, never a mutation — if the regeneration is stopped or fails,
        the old reply and everything after it must survive untouched.
        """
        mi = self.timeline[idx].get("mi")
        if isinstance(mi, int) and 1 < mi <= len(self.messages):
            return self.messages[:mi - 1]
        # older saves have no "mi" — drop the trailing assistant run
        out = list(self.messages)
        while len(out) > 1 and out[-1].get("role") == "assistant":
            out.pop()
        return out

    def commit_reply(self, idx: int, messages: list, before: int) -> None:
        """Adopt a regenerated reply.

        Keeps what came before the old reply, drops the old reply and
        whatever followed it, and keeps what the re-answer itself produced
        (a freshly generated image, say) — `before` marks where that starts.
        """
        self.timeline = self.timeline[:idx] + self.timeline[before:]
        self.messages = messages

    def rollback_reply(self, before: int) -> None:
        """Drop the events a failed regeneration left on the timeline.

        Their context was discarded, so they must not linger as cards the
        user cannot explain — but a trailing error is kept, because "nothing
        happened" without a reason is worse than a red bubble.
        """
        keep = [e for e in self.timeline[before:] if e.get("type") == "error"]
        self.timeline = self.timeline[:before] + keep

    def sync_gen_context(self, series: str, rel: str, gen: dict | None) -> bool:
        """Point the LLM's tool result at the newest variant of an image.

        Regenerating rewrites the picture but not the conversation, so the
        model would otherwise keep describing the original. Repoint the tool
        message that produced the file at the one now on screen, and give it
        the prompt that actually ran.

        `series` identifies the lineage and `rel` is the new file. The match
        accepts either the current file or the lineage's original, because a
        second regeneration searches from a different variant than the first.
        """
        base = Path(series).name
        for m in reversed(self.messages):
            if m.get("role") != "tool":
                continue
            try:
                content = json.loads(m.get("content") or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(content, dict):
                continue
            files = [Path(str(f)).name for f in (content.get("saved_files")
                                                 or [])]
            root = Path(str(content.get("series") or "")).name
            if base not in files and base != root:
                continue
            content["saved_files"] = [rel]
            content["series"] = root or series
            content.pop("count", None)
            if gen and gen.get("prompt"):
                content["prompt"] = gen["prompt"]
            m["content"] = json.dumps(content, ensure_ascii=False)
            return True
        return False

    def _run_tool(self, name: str, args: dict, emit) -> dict:
        """Run one tool call in a worker thread; stream SD progress while
        generation tools run."""
        if name in GEN_TOOLS and not self.auto_images:
            # fail closed: the tools are withheld from the request when the
            # chat's auto-images toggle is off, so this only trips if a model
            # hallucinates a tool call anyway
            result = {"error": "image generation is off for this chat"}
            self.push({"type": "tool_error", "name": name,
                       "error": result["error"]}, emit)
            return result
        is_gen = name in GEN_TOOLS
        if is_gen and self.char:
            # identity tags are authoritative — add them if the model forgot.
            # The persona's look only joins a shot that actually draws the
            # user; a lone "another's hand" or a "thighs framing viewer" tag
            # must not pull their whole body into frame.
            args["prompt"] = ensure_tags(args.get("prompt", ""),
                                         self.char_tags(),
                                         self.persona_appearance(),
                                         args.get("negative_prompt", ""))
            # preferred checkpoint applies when the model didn't pick one
            cp = (self.char.get("checkpoint") or "").strip()
            if cp and not args.get("model"):
                args["model"] = cp
            # a checkpoint switch carries its architecture's saved companion
            # files, so a modular model never loads without its text encoder
            if args.get("model"):
                arch = self.client.arch_for(args["model"])
                for k, v in self.saved_components(arch).items():
                    args.setdefault(k, v)
            size = self.char.get("size") or []
            if name == "generate_image" and len(size) == 2:
                args.setdefault("width", size[0])
                args.setdefault("height", size[1])
            # LoRA tags go on AFTER ensure_tags: that function rebuilds the
            # prompt from a comma split, so a tag added beforehand could be
            # reordered or dropped by the count reconciliation. The model's
            # arch is resolved here, after any card checkpoint switch, because
            # it decides which LoRAs can load at all.
            larch = self.client.arch_for(args["model"]) \
                if args.get("model") else self.client.current_arch()
            entries = card_loras(self.char, larch,
                                _installed_loras(self.client))
            tags, applied, skipped = lora_tag_list(entries)
            # Strip whatever the model wrote either way. It is never told a
            # LoRA exists, so any tag in its output is invented — and an
            # invented name fails silently in Forge, producing an image that
            # looks fine and is missing the character.
            args["prompt"], _ = apply_lora_tags(args.get("prompt", ""), tags)
            args["_loras"] = [{"name": e["name"], "weight": e["weight"]}
                              for e in applied]
            notes_l = list(skipped)
            total = sum(e["weight"] for e in applied)
            if total > LORA_SUM_WARN:
                notes_l.append(f"LoRA weights total {total:.2f} — above "
                               f"{LORA_SUM_WARN}, expect a blended look")
            args["_lora_notes"] = notes_l
        emit({"type": "tool_start", "name": name})
        out = {"result": None, "error": None}

        def work():
            try:
                out["result"] = execute_tool(self.client, name, args,
                                             user_out_dir(self.uid))
            except Exception as e:                     # noqa: BLE001 — report all
                out["error"] = f"{type(e).__name__}: {e}"

        th = threading.Thread(target=work, daemon=True)
        th.start()
        while th.is_alive():
            th.join(PROGRESS_POLL)
            if is_gen and th.is_alive():
                try:
                    p = self.client.progress()
                    emit({"type": "progress",
                          "progress": p.get("progress") or 0,
                          "eta": p.get("eta_relative")})
                except Exception:
                    pass

        if out["error"]:
            evt = {"type": "tool_error", "name": name,
                   "error": scrub_paths(out["error"])}
            self.push(evt, emit)
            return {"error": out["error"]}

        result = out["result"]
        # file PATHS -> rel paths (privacy: no server layout leaks)
        result["saved_files"] = [rel_of(Path(f)) for f
                                 in result.get("saved_files", [])]
        if result.get("gen"):
            result["gen"]["prompt"] = result["gen"].get("prompt", "")
            evt = {
                "type": "generation",
                "files": ["/outputs/" + f for f in result["saved_files"]],
                "count": result.get("count", 0),
                "seed": result.get("seed_used"),
                "gen": result.get("gen"),
            }
            self.push(evt, emit)
        elif "models" in result:
            evt = {"type": "models",
                   "current_model": result.get("current_model"),
                   "models": result.get("models", [])}
            self.push(evt, emit)
        return result


def chat_completion(api_key: str, llm: str, messages: list,
                    temperature: float | None = None,
                    max_tokens: int | None = None,
                    tools: list | None = None) -> dict:
    """One OpenRouter chat completion with tool definitions.

    `tools` defaults to the full tool set; pass an empty list to withhold the
    image tools (the chat's auto-images toggle is off).
    """
    from agent_core import TOOLS
    if tools is None:
        tools = TOOLS
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "Anima",
    }
    body: dict = {"model": llm, "messages": messages,
                  "temperature": temperature if temperature is not None
                  else 0.7}
    if tools:
        # omitted entirely when empty — an empty array is rejected by some
        # providers and tells the model nothing a missing key wouldn't
        body["tools"] = tools
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    try:
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions",
                             headers=headers, json=body, timeout=LLM_TIMEOUT)
    except requests.RequestException as e:
        raise LLMError(f"OpenRouter unreachable: {e}") from e
    if resp.status_code in (401, 403):
        raise LLMError(f"OpenRouter auth error {resp.status_code}: "
                       f"{resp.text[:300]}")
    if resp.status_code != 200:
        raise LLMError(f"OpenRouter error {resp.status_code}: "
                       f"{resp.text[:300]}")
    data = resp.json()
    # some providers return HTTP 200 with an error inside the body
    if data.get("error") or not data.get("choices"):
        raise LLMError("OpenRouter provider error: "
                       f"{json.dumps(data.get('error') or data)[:300]}")
    return data


def run_chat_turn(agent: Agent, user_text: str):
    """Acquire the agent lock, run the turn, stream events through a queue.
    Returns the queue, or None when the agent is already busy."""
    if not agent.has_key():
        return ("nokey", None)
    if not agent.lock.acquire(blocking=False):
        return ("busy", None)
    q = Queue()
    agent.stop_flag.clear()

    def emit(evt):
        q.put(evt)

    def worker():
        try:
            agent.status_cb = emit
            agent.run_turn(user_text, emit)
        except Exception as e:                         # noqa: BLE001 — last resort
            evt = {"type": "error",
                   "text": f"✗ server error — {type(e).__name__}: {e}"}
            agent.push(evt, emit)
        finally:
            agent.status_cb = None
            agent._save_state()
            agent.lock.release()
            q.put({"type": "done"})

    # A new thread starts with an empty context, so the outputs scope (and
    # anything else request-scoped) would be lost mid-turn and images would
    # land in the unscoped root. Copy it across explicitly.
    ctx = contextvars.copy_context()
    threading.Thread(target=ctx.run, args=(worker,), daemon=True).start()
    return ("ok", q)


def run_reply_regen(agent: Agent, idx: int):
    """Re-ask the model for a reply already on screen.

    Returns (status, queue); status is ok/busy/nokey/bad_index.

    The conversation is NOT rewound up front: the model answers against a
    working copy, and the rewind is committed only once a new reply actually
    exists. Stopping (or failing) therefore leaves the chat as it was, instead
    of deleting the answer the user was trying to replace.
    """
    tl = agent.timeline
    # validate the target before anything else — a stale index is the common
    # case (the chat moved on) and shouldn't be reported as a missing key
    if not (0 <= idx < len(tl)) or tl[idx].get("type") not in ("reply",
                                                               "error"):
        return ("bad_index", None)
    if not agent.has_key():
        return ("nokey", None)
    if not agent.lock.acquire(blocking=False):
        return ("busy", None)
    ctx = agent.reply_context(idx)
    before = len(tl)
    q = Queue()
    agent.stop_flag.clear()

    def emit(evt):
        q.put(evt)

    def worker():
        try:
            agent.status_cb = emit
            if agent.resume_turn(ctx, emit):
                agent.commit_reply(idx, ctx, before)
            else:
                agent.rollback_reply(before)
        except Exception as e:                         # noqa: BLE001 — last resort
            agent.rollback_reply(before)
            agent.push({"type": "error",
                        "text": f"✗ server error — {type(e).__name__}: {e}"},
                       emit)
        finally:
            agent.status_cb = None
            agent._save_state()
            agent.lock.release()
            q.put({"type": "done"})

    # A new thread starts with an empty context, so the outputs scope (and
    # anything else request-scoped) would be lost mid-turn and images would
    # land in the unscoped root. Copy it across explicitly.
    ctx = contextvars.copy_context()
    threading.Thread(target=ctx.run, args=(worker,), daemon=True).start()
    return ("ok", q)


# ------------------------------------------------- direct regeneration

# everything the regen sheet may edit; anything else the caller sends is
# dropped so a bad client can't push arbitrary payload keys into SD
_REGEN_KEYS = ("prompt", "negative_prompt", "width", "height", "steps",
               "cfg_scale", "sampler_name", "scheduler", "clip_skip", "seed",
               "batch_size", "denoising_strength", "distilled_cfg_scale")

# companion-component fields: these change the loaded pipeline rather than the
# request, so they ride the options route and are applied with the checkpoint
_COMPONENT_KEYS = ("text_encoder", "sd_vae", "low_bits")

#: legacy monolithic defaults, only used when the WebUI cannot be reached.
#: No clip_skip here on purpose — an unreachable server cannot be told what it
#: is already set to, and a filler value would override it.
_REGEN_DEFAULTS = {"width": 1024, "height": 1024, "steps": 25,
                   "cfg_scale": 7.0, "sampler_name": "Euler a",
                   "scheduler": "automatic", "batch_size": 1}


def _sd_capabilities(client, force: bool = False) -> dict:
    """Samplers, schedule types, companion files and precision options.

    Every list comes from the WebUI itself, so the pickers never offer a value
    the server would reject. Called from SDState's worker thread only — never
    from a request handler, because this is the slow part (it reads
    openapi.json plus six other endpoints).
    """
    try:
        if not client.options():
            return {}
        client.capabilities(force=force)
        files = client.companion_files()
        return {"arch": client.current_arch(),
                "samplers": client.list_samplers(),
                "schedulers": client.list_schedulers(),
                "low_bits": client.list_low_bits(),
                "text_encoders": files["text_encoder"],
                "vaes": client.list_vaes(),
                "architectures": client.architecture_summary(),
                "video": client.video_supported(),
                "video_endpoint": client.video_endpoint() or ""}
    except Exception:                                 # noqa: BLE001
        return {}


# ------------------------------------------------------ image server state

SD_TTL = 60.0             # how long a healthy snapshot stays fresh
SD_RETRY = 15.0           # re-probe this soon after a failure
SD_PROBE_TIMEOUT = 6      # budget for checking a URL the user just typed
SD_POLL_TIMEOUT = 15      # patient enough to ride out a checkpoint load


class SDState:
    """Cached, off-thread view of the connected image server.

    Everything the UI shows about the WebUI comes from here, and /api/status
    only ever *reads* it. That matters because every client polls status every
    30 s and asking the WebUI inline was the source of the "switching the image
    server breaks the app" behaviour: a server that has just been pointed at a
    cold start — or one busy loading a checkpoint — answers nothing at all, so
    one status request held a thread for the sum of every probe timeouts
    (options 20 s + models 25 s + the whole capability sweep, up to ~2 min).
    The browser's per-host connection pool then filled with those stuck
    requests and *every* other call the app makes stalled behind them: models,
    components and settings all looked broken, and only recovered when the
    requests finally timed out.

    So: one worker refreshes the snapshot in the background, readers never
    wait, and a failure is cached with its reason too — an unreachable server
    costs one short probe per SD_RETRY, not a chain of long ones per poll.
    """

    def __init__(self, agent: "Agent"):
        self.agent = agent
        self.lock = threading.Lock()
        self.worker: threading.Thread | None = None
        self.data: dict = {
            "url": agent.client.base_url, "ts": 0.0, "sd_ok": False,
            "current_model": "", "current_arch": "",
            "models": [], "detail": [], "caps": {}, "error": "",
        }

    # ---------------------------------------------------------- read side

    def snapshot(self, force: bool = False) -> dict:
        """Cached state, plus a kick to refresh it in the background."""
        with self.lock:
            d = self.data
            age = time.time() - d["ts"]
            stale = force or d["url"] != self.agent.client.base_url \
                or not d["ts"] or age > (SD_TTL if d["sd_ok"] else SD_RETRY)
            if stale and not self.worker:
                self.worker = threading.Thread(
                    target=self._refresh, args=(force,), daemon=True)
                self.worker.start()
            out = dict(d)
            out["pending"] = stale or bool(self.worker)
        return out

    def invalidate(self):
        """Refetch on the next read, keeping the current lists visible."""
        with self.lock:
            self.data["ts"] = 0.0

    def reset(self):
        """Forget everything — those lists belong to a server we just left."""
        with self.lock:
            d = self.data
            d.update({"url": self.agent.client.base_url, "ts": 0.0,
                      "sd_ok": False, "current_model": "", "current_arch": "",
                      "models": [], "detail": [], "caps": {}, "error": ""})

    # --------------------------------------------------------- write side

    def _refresh(self, force: bool = False):
        client = self.agent.client
        d = {"url": client.base_url, "ts": time.time(), "sd_ok": False,
             "current_model": "", "current_arch": "", "models": [],
             "detail": [], "caps": {}, "error": ""}
        try:
            # probe() gives a reason worth showing ("connection refused…",
            # "busy loading a checkpoint") and warms the options cache, so the
            # reads below cost nothing extra
            ok, detail = client.probe(timeout=SD_POLL_TIMEOUT)
            if not ok:
                raise SDWebUIError(detail)
            d["sd_ok"] = True
            if force:                                 # ask SD to rescan its dir
                try:
                    client.refresh_models()
                except Exception:                    # noqa: BLE001
                    pass
            models = client.list_models(force=force)
            d["models"] = [m["title"] for m in models]
            d["detail"] = models
            d["current_model"] = client.current_model()
            d["current_arch"] = client.current_arch()
            d["caps"] = _sd_capabilities(client, force=force)
        except Exception as e:                           # noqa: BLE001
            d["error"] = (str(e) or type(e).__name__)[:200]
        with self.lock:
            # a URL switch that landed mid-fetch wins: this data describes a
            # server we are no longer talking to, and the snapshot will refetch
            if d["url"] == self.agent.client.base_url:
                self.data = d
            self.worker = None


def _public_profile(client, arch: str) -> dict:
    """Architecture profile in the shape the UI wants (JSON-safe)."""
    try:
        p = client.arch_profile(arch)
    except Exception:                                 # noqa: BLE001
        return {}
    legacy = arch in ("sd", "xl")
    return {"arch": p.get("arch", arch), "label": p.get("label", arch),
            "cfg": p.get("cfg"), "steps": p.get("steps"),
            "sampler": p.get("sampler"), "scheduler": p.get("scheduler"),
            # None for sd/xl: they advertise a value, but an ordinary sampler
            # never reads it and the payload builder does not send it
            "distilled_cfg_scale": None if legacy else p.get("dcfg"),
            "distilled": bool(p.get("distilled")),
            "video": bool(p.get("video")),
            "cfg_range": p.get("cfg_range"), "step_range": p.get("step_range"),
            "enforce": bool(p.get("enforce"))}


def regen_defaults(client, model: str = "") -> dict:
    """Blank-field defaults for the regen sheet, from the architecture.

    A sheet opened on a Flux image must not offer SDXL's 1024/CFG 7/25 steps:
    those are exactly the values that ruin a distilled model. When the WebUI
    is unreachable this falls back to the legacy numbers.
    """
    try:
        model = model or client.current_model()
        prof = client.arch_profile(client.arch_for(model))
    except Exception:                                 # noqa: BLE001
        return dict(_REGEN_DEFAULTS)
    size = int(prof.get("size") or 1024)
    steps = int(prof["steps"])
    lo, hi = prof.get("step_range") or [1, 150]
    if is_distilled(model) and not (lo <= steps <= hi):
        # a Turbo/Schnell variant of a family whose default suits the full
        # model — offer the same low default the payload builder would pick
        steps = int(lo + (hi - lo) // 3)
    out = {"width": size, "height": size, "steps": steps,
           "cfg_scale": float(prof["cfg"]),
           "sampler_name": prof["sampler"],
           "scheduler": prof["scheduler"], "batch_size": 1}
    # Clip skip is a real option on Neo, and a blank recipe must NOT send a
    # filler value: that would override whatever the server is set to (2 for
    # most anime SDXL checkpoints) and quietly degrade the image. Offer the
    # current value so the sheet shows what will be used.
    try:
        cur = client.options().get("CLIP_stop_at_last_layers")
        if cur:
            out["clip_skip"] = int(float(cur))
    except Exception:                                 # noqa: BLE001
        pass
    return out


def _find_gen_args(agent: Agent, rel: str) -> dict | None:
    """The 'gen' snapshot of the timeline generation that produced `rel`.

    Newest wins: regenerations are appended after the original, and the card's
    carousel is sitting on the newest variant — so that is the recipe the
    regen sheet should open with.
    """
    base = Path(rel).name
    for evt in reversed(agent.timeline):
        if evt.get("type") != "generation":
            continue
        files = [rel_from_url(f) for f in evt.get("files", [])]
        if rel in files or (base and base in [Path(f).name for f in files]):
            g = evt.get("gen") or {}
            if g.get("prompt"):
                return g
    return None


def gen_args_for(agent: Agent, rel: str) -> tuple[dict, dict]:
    """(args, source) — the full generation recipe for `rel`.

    Timeline snapshot first (it holds the model's checkpoint and prompt
    exactly), PNG parameters chunk as the fallback for images that came from
    outside this chat. `source` is 'chat' or 'png' so the sheet can say so.
    """
    src = out_file(rel)
    g = _find_gen_args(agent, rel)
    if g:
        args = {k: g.get(k) for k in _REGEN_KEYS if g.get(k) is not None}
        args["model"] = g.get("model") or None
        return args, {"source": "chat", "arch": g.get("arch", ""),
                      "notes": g.get("notes") or []}
    if src is None:
        return {}, {"source": "none"}
    args = _args_from_png(src)
    return args, {"source": "png",
                  "arch": guess_arch(str(args.get("model") or ""))}


def _args_from_png(path: Path) -> dict:
    """Recover generation args from the image's PNG parameters chunk.

    Forge Neo writes a few extra fields an old parser never saw — the schedule
    type and the distilled CFG of a modern model — and they are the difference
    between reproducing an image and merely resembling it.
    """
    info = parse_png_info(path)
    args: dict = {"prompt": info.get("prompt", ""),
                  "negative_prompt": info.get("negative", "")}
    p = info.get("params", "")

    def grab(key: str):
        m = re.search(re.escape(key) + r":\s*([^,]+)", p)
        return m.group(1).strip() if m else None

    def grab_num(key: str, cast):
        raw = grab(key)
        if raw is None:
            return None
        try:
            return cast(raw)
        except ValueError:
            return None

    steps = grab_num("Steps", int)
    if steps:
        args["steps"] = steps
    cfg = grab_num("CFG scale", float)
    if cfg is not None:
        args["cfg_scale"] = cfg
    dcfg = grab_num("Distilled CFG Scale", float)
    if dcfg is not None:
        args["distilled_cfg_scale"] = dcfg
    size = grab("Size")
    if size:
        m = re.match(r"(\d+)x(\d+)", size)
        if m:
            args["width"], args["height"] = int(m.group(1)), int(m.group(2))
    sampler = grab("Sampler")
    if sampler:
        args["sampler_name"] = sampler
    sched = grab("Schedule type")
    if sched:
        args["scheduler"] = sched
    clip = grab_num("Clip skip", int)
    if clip:
        args["clip_skip"] = clip
    model = grab("Model")
    if model:
        args["model"] = model
    vae = grab("VAE")
    if vae and vae.lower() not in ("none", "automatic"):
        args["sd_vae"] = vae
    return args


def _clean_gen_args(args: dict, client=None, model: str = "") -> dict:
    """Clamp the sheet's fields into sane SD ranges, dropping blanks.

    The ranges that matter come from the checkpoint's architecture (a distilled
    model gets 1-2 CFG and 4-15 steps whether or not the sheet's numbers say
    so); everything else is just bounded to something the WebUI accepts.
    """
    out: dict = {}
    p = str(args.get("prompt") or "").strip()
    if p:
        out["prompt"] = p[:4000]
    out["negative_prompt"] = str(args.get("negative_prompt") or "").strip()[:2000]

    defaults = regen_defaults(client, model) if client is not None \
        else dict(_REGEN_DEFAULTS)

    def num(key, lo, hi, default, cast=int):
        v = args.get(key)
        if v is None or v == "":
            return cast(default)
        try:
            return max(lo, min(hi, cast(v)))
        except (TypeError, ValueError):
            return cast(default)

    def dim(key, default):
        """A1111 only takes multiples of 8 — snap to the nearest one."""
        return max(64, min(4096, (num(key, 64, 4096, default) + 4) // 8 * 8))

    out["width"] = dim("width", defaults["width"])
    out["height"] = dim("height", defaults["height"])
    out["steps"] = num("steps", 1, 150, defaults["steps"])
    out["cfg_scale"] = num("cfg_scale", 1.0, 30.0, defaults["cfg_scale"],
                           float)
    out["batch_size"] = num("batch_size", 1, 8, defaults["batch_size"])
    # Clip skip is only sent when the caller actually asked for it. A blank
    # sheet field means "whatever the server is set to" — and since Neo turns
    # clip skip into a real per-request override, a filler value here would
    # silently override the server's setting (2 for most anime SDXL models)
    # and degrade every regeneration. `defaults` carries the current value
    # purely so the sheet can show it.
    if args.get("clip_skip") not in (None, ""):
        out["clip_skip"] = num("clip_skip", 1, 4,
                               defaults.get("clip_skip", 1))
    s = str(args.get("sampler_name") or "").strip()
    if s:
        out["sampler_name"] = s[:80]
    sched = str(args.get("scheduler") or "").strip()
    if sched:
        out["scheduler"] = sched[:40]
    dcfg = args.get("distilled_cfg_scale")
    if dcfg is not None and str(dcfg).strip() != "":
        # 0 would switch the real guidance off entirely — treat it as unset
        val = num("distilled_cfg_scale", 0.0, 30.0, 0.0, float)
        if val > 0:
            out["distilled_cfg_scale"] = val
    sd = args.get("seed")
    if sd is not None and str(sd).strip() not in ("", "-1"):
        out["seed"] = num("seed", 0, 2 ** 53 - 1, -1)
    d = args.get("denoising_strength")
    if d is not None and str(d).strip() != "":
        out["denoising_strength"] = num("denoising_strength", 0.01, 1.0, 0.65,
                                        float)
    return out


def run_regeneration(agent: Agent, rel: str, instruction: str, emit,
                     overrides: dict | None = None) -> dict:
    """Re-create an image directly — no LLM turn, no chat message.

    Starts from the image's own recipe (newest chat snapshot, else the PNG
    parameters chunk) and lets the caller override every field the regen
    sheet exposes: both prompts, resolution, steps, CFG, sampler, clip skip,
    batch, seed and checkpoint.

    Mode is explicit: `mode="img2img"` reworks the original image (denoise
    decides how loosely the instruction may bend it); `mode="txt2img"` draws
    a fresh variation from the prompt alone. A non-empty instruction with no
    explicit mode means img2img, which is what it always used to do.

    The new variant is saved next to the original, so regenerating an image
    that lives in a folder keeps it there.
    Returns a timeline-ready 'generation' event with 'src' lineage."""
    from sd_client import save_images

    src = out_file(rel)
    if src is None:
        raise ValueError("image not found")

    base, origin = gen_args_for(agent, rel)
    model = base.get("model") or None
    args = {k: base[k] for k in _REGEN_KEYS if base.get(k) is not None}
    components = {k: str((overrides or {}).get(k) or "").strip()
                  for k in _COMPONENT_KEYS}

    mode = str((overrides or {}).get("mode") or "").strip().lower()
    if mode not in ("txt2img", "img2img"):
        mode = "img2img" if instruction else "txt2img"
    if mode == "txt2img":
        args.pop("denoising_strength", None)
        # a fresh variation rolls a new seed — unless the sheet pins one
        if not str((overrides or {}).get("seed", "")).strip():
            args.pop("seed", None)

    # the sheet's own values win; anything blank keeps the original recipe
    for k, v in (overrides or {}).items():
        if k in ("mode", "instruction") or v is None or v == "":
            continue
        args[k] = v
    if instruction:
        args["prompt"] = (args.get("prompt", "") + ", "
                          + instruction).strip(" ,")
    if mode == "img2img":
        args.setdefault("denoising_strength", 0.65)

    ckpt = str((overrides or {}).get("model") or model or "").strip()
    if ckpt and ckpt != "(unknown)":
        arch = agent.client.arch_for(ckpt)
        args = _clean_gen_args(args, agent.client, ckpt)
        if not args.get("prompt"):
            raise ValueError("no original prompt found for this image")
        # one options write for checkpoint + companions: separate writes mean
        # separate model reloads, and the second one can fail on a tight card
        emit({"type": "status", "text": f"loading {arch} checkpoint…"})
        model = ckpt
    else:
        args = _clean_gen_args(args, agent.client, "")
        if not args.get("prompt"):
            raise ValueError("no original prompt found for this image")

    # One lease across "switch the dial" and "run". Forge's dial is global, so
    # a regen that switched models without it could be overtaken mid-flight
    # by another user on the same WebUI and produce the wrong checkpoint.
    with forge_lease(agent.client, ckpt or None,
                     {k: v for k, v in components.items() if v},
                     arch=arch if ckpt else None) as running:
        model = ckpt or model or running
        if mode == "img2img":
            emit({"type": "status", "text": "applying change…"})
            prepared = agent.client.prepare_args(args, model=model,
                                                 kind="img2img")
            prepared["payload"]["init_images"] = [
                base64.b64encode(src.read_bytes()).decode()]
            result = agent.client.submit(prepared["payload"], img2img=True)
        else:
            emit({"type": "status", "text": "generating…"})
            prepared = agent.client.prepare_args(args, model=model,
                                                 kind="txt2img")
            result = agent.client.submit(prepared["payload"])

    saved = save_images(result, out_dir=src.parent, name_prefix="ai")
    if not saved:
        raise ValueError("the generator returned no image")
    try:
        seed = json.loads(result.get("info", "{}")).get("seed")
    except json.JSONDecodeError:
        seed = None
    # snapshot what was actually sent, not what the sheet asked for — the
    # architecture fills in and clamps values, and the recipe must replay
    gen = {k: args.get(k) for k in
           ("prompt", "negative_prompt", "width", "height", "steps",
            "cfg_scale", "sampler_name", "clip_skip", "batch_size",
            "denoising_strength")}
    gen.update({
        "model": model or "(unknown)",
        "arch": prepared["arch"],
        "distilled": prepared["distilled"],
        "sampler_name": prepared["sampler"],
        "scheduler": prepared["scheduler"] or "",
        "steps": prepared["steps"],
        "cfg_scale": prepared["cfg_scale"],
        "clip_skip": prepared["clip_skip"],
        "distilled_cfg_scale": prepared["distilled_cfg_scale"],
        # the seed the generator ACTUALLY used, not args["seed"]: a rolled
        # seed was popped from args, so recording from there left every
        # regenerated variant with seed null and its recipe unreplayable —
        # long-pressing one again rolled another seed and the composition
        # drifted further on every pass
        "seed": seed,
    })
    if prepared.get("notes"):
        gen["notes"] = prepared["notes"]
    if any(components.values()):
        gen.update({k: v for k, v in components.items() if v})
    return {"type": "generation",
            "files": ["/outputs/" + rel_of(f) for f in saved],
            "count": len(saved),
            "seed": seed,
            "gen": gen,
            "src": rel,
            "origin": origin.get("source", "chat")}


# ------------------------------------------------------------- gallery
#
# Images are real files under outputs/ and folders are real subdirectories of
# it. An image is addressed by its "rel" path relative to outputs/:
# "ai_20260101-120000_42_0.png" at the root, "Anime/portrait.png" inside a
# folder. The root of outputs/ doubles as the unfiled inbox — new generations
# always land there, so the root view shows everything not filed away yet.

MAX_FOLDER_DEPTH = 8


def _safe_name(name: str) -> str | None:
    """One safe path segment: no separators, no traversal, no leading dot."""
    if not name or "/" in name or "\\" in name or ".." in name \
            or name.startswith("."):
        return None
    return name


def _safe_rel(rel: str) -> str | None:
    """A safe path relative to outputs/ ("a/b/c.png"); "" is the root.
    None means the input was unsafe."""
    if not rel:
        return ""
    parts = str(rel).replace("\\", "/").split("/")
    if len(parts) > MAX_FOLDER_DEPTH:
        return None
    for p in parts:
        if _safe_name(p) is None:
            return None
    return "/".join(parts)


def _out_root() -> Path:
    """The outputs tree this request is scoped to.

    Phase 4. Every user gets their own gallery under `outputs/<uid>/`, so one
    person's delete or rename cannot touch another's images. An admin reading
    their *own* gallery is scoped to their own uid like anyone else — the
    `~<uid>/` prefix is what lets them reach another user's tree, which keeps
    the common case looking exactly as it did pre-multi-user.

    Reads go through ContextVars rather than a `uid` parameter threaded
    through ~50 call sites, which would be a large diff for no gain and easy
    to miss one — and a missed one is a cross-user read. Writes do not go
    through here at all: they always target `user_out_dir(uid)`."""
    uid = _scope_target()
    return ((OUT_DIR / uid) if uid else OUT_DIR).resolve()


def user_out_dir(uid: str) -> Path:
    """Where a user's generations are written. Always `outputs/<uid>/`, for
    admins too — an admin's own images then appear in the gallery as their own
    folder rather than loose in the root."""
    d = OUT_DIR / (uid or "u001")
    d.mkdir(parents=True, exist_ok=True)
    return d


#: uid -> is_admin, rebuilt whenever users.json changes on disk. The outputs
#: scoping needs it, and it sits in a module-level function with no request
#: context to ask.
_ADMIN_CACHE: dict[str, bool] = {}
_OUT_UID = contextvars.ContextVar("out_uid", default="")
_AS_UID = contextvars.ContextVar("as_uid", default="")


def _is_admin_uid(uid: str) -> bool:
    return _ADMIN_CACHE.get(uid, False)


def _refresh_admins() -> None:
    _ADMIN_CACHE.clear()
    for u in load_users():
        if u.get("role") == ROLE_ADMIN and not u.get("disabled"):
            _ADMIN_CACHE[str(u.get("id"))] = True


def scope_uid(uid: str) -> None:
    """Point this request's outputs tree at `uid` and refresh the admin
    cache. Called once per request from the handler."""
    _refresh_admins()
    _OUT_UID.set(uid or "")
    _AS_UID.set("")


def scope_as(uid: str, target: str, is_admin: bool) -> bool:
    """Admin-only: view another user's gallery as `target`.

    `~<uid>/...` in a rel path means "that user's tree", so an admin can
    browse and mutate any user's gallery through the existing endpoints
    without a parallel set of routes. Everyone else is refused — without
    this, `~u001/secret.png` would be an ordinary folder name in their own
    tree, which is harmless, but being explicit is cheaper to reason about.
    """
    if not is_admin or not target or target == uid:
        return False
    if not find_user(load_users(), target):
        return False
    _AS_UID.set(target)
    return True


def _scope_target() -> str:
    return _AS_UID.get() or _OUT_UID.get()


def split_scope(rel: str) -> tuple[str, str]:
    """`~u002/folder/x.png` -> ("u002", "folder/x.png"), and a bare `~u002`
    -> ("u002", "") meaning that user's gallery root. Anything else keeps the
    request's own scope and an empty override."""
    if rel and rel.startswith("~"):
        uid, _, tail = rel[1:].partition("/")
        if uid:
            return uid, tail
    return "", rel or ""


def _resolve(rel: str) -> Path | None:
    """Resolve a validated rel path to an existing file inside outputs/,
    refusing anything that escapes the tree (traversal, absolute paths,
    symlinks)."""
    rel = _safe_rel(rel)
    if rel is None or rel == "":
        return None
    p = (_out_root() / rel).resolve()
    try:
        p.relative_to(_out_root())
    except ValueError:
        return None
    return p if p.is_file() else None


def out_file(rel: str) -> Path | None:
    """Existing image file for a rel path, or None."""
    return _resolve(rel)


def out_dir(rel: str) -> Path | None:
    """Folder for a rel path ('' = the gallery root), or None if it would
    escape outputs/."""
    rel = _safe_rel(rel if rel is not None else "")
    if rel is None:
        return None
    p = (_out_root() / rel).resolve()
    try:
        p.relative_to(_out_root())
    except ValueError:
        return None
    return p


def rel_of(path: Path) -> str:
    """Path -> its rel path under the scoped outputs root."""
    return path.resolve().relative_to(_out_root()).as_posix()


def join_rel(parent: str, name: str) -> str:
    """Rel path of `name` inside `parent` ('' parent = the gallery root)."""
    return f"{parent}/{name}" if parent else name


def rel_from_url(url: str) -> str:
    """'/outputs/<rel>' (or a bare name, from older saves) -> '<rel>'."""
    s = str(url or "")
    if s.startswith("/outputs/"):
        s = s[len("/outputs/"):]
    return _safe_rel(unquote(s)) or ""


def clean_folder_name(raw: str) -> str | None:
    """Sanitise a user-typed folder name. None when it is unusable."""
    name = " ".join(str(raw or "").split())        # collapse whitespace
    name = name.rstrip(". ")                       # Win32 drops these too
    if not name or len(name) > 60:
        return None
    if "/" in name or "\\" in name or "\x00" in name:
        return None
    if name.startswith("."):
        return None
    if any(ord(c) < 32 for c in name):
        return None
    return name


def unique_name(folder: Path, name: str) -> str:
    """A free file name in `folder`. The counter goes in the prefix segment
    ('ai' -> 'ai-2') so the '<prefix>_<stamp>_<seed>_<n>.png' pattern — and
    with it _seed_from_name() and the thumbnail stem — stays intact."""
    def free(nm):
        return not (folder / nm).exists()

    if free(name):
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    suffix = ("." + ext) if ext else ""
    head, sep, tail = stem.partition("_")
    for n in range(2, 500):
        cand = f"{head}-{n}{sep}{tail}" if sep else f"{head}-{n}"
        cand += suffix
        if free(cand):
            return cand
    return stem + "-" + secrets.token_hex(2) + suffix


def _seed_from_name(name: str) -> str | None:
    m = re.search(r"_\d{8}-\d{6}_(\d+)_\d+\.", Path(name).name)
    return m.group(1) if m else None


def thumbnail(path: Path) -> Path | None:
    tdir = _out_root() / ".thumbs"
    thumb = tdir / (path.stem + ".jpg")
    try:
        if thumb.exists() and thumb.stat().st_mtime >= path.stat().st_mtime:
            return thumb
    except OSError:
        pass
    try:
        from PIL import Image                          # optional
        im = Image.open(path).convert("RGB")
        im.thumbnail((480, 720))
        tdir.mkdir(exist_ok=True)
        im.save(thumb, "JPEG", quality=86)
        return thumb
    except Exception:
        return None


def gallery_listing(rel: str = "") -> dict:
    """One folder level: its subfolders plus the images directly inside it."""
    folder = out_dir(rel)
    out = {"folder": _safe_rel(rel) or "", "images": [], "folders": [],
           "total": gallery_total()}
    if folder is None or not folder.is_dir():
        return out
    folders, images = [], []
    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return out
    for p in entries:
        if p.name.startswith("."):
            continue                          # .thumbs and friends
        try:
            if p.is_dir():
                folders.append({"name": p.name,
                                "count": _count_images(p)})
                continue
            if p.suffix.lower() not in IMG_EXTS:
                continue
            st = p.stat()
        except OSError:
            continue
        images.append({"rel": rel_of(p), "name": p.name, "bytes": st.st_size,
                       "mtime": int(st.st_mtime),
                       "seed": _seed_from_name(p.name)})
    images.sort(key=lambda x: x["mtime"], reverse=True)
    folders.sort(key=lambda f: f["name"].lower())
    out["images"], out["folders"] = images, folders
    return out


def _count_images(folder: Path) -> int:
    """Images anywhere below `folder` (subfolders included, dot dirs skipped)."""
    n = 0
    for _root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        n += sum(1 for f in files if Path(f).suffix.lower() in IMG_EXTS)
    return n


def gallery_total() -> int:
    """Every image in the gallery, folders included."""
    return _count_images(_out_root())


def gallery_tree() -> list[dict]:
    """Every folder in the gallery, sorted for the move-target picker.

    Sorted by path segments so a folder always comes immediately before its
    own children (os.walk emits them a level at a time, which detaches them).
    """
    out = []
    for root, dirs, _files in os.walk(_out_root()):
        depth = len(Path(root).relative_to(_out_root()).parts)
        for d in dirs:
            if d.startswith("."):
                continue
            out.append({"rel": rel_of(Path(root) / d), "name": d,
                        "depth": depth})
    out.sort(key=lambda f: f["rel"].split("/"))
    return out


def reindex_timeline(agent: Agent, mapping: dict, gone: set) -> bool:
    """Point the chat timeline at images after files moved or vanished, so
    generation cards keep resolving. `mapping` is old rel -> new rel; `gone`
    is a set of rels that no longer exist. The events themselves stay put —
    their indices are what /api/delete_event addresses."""
    def fix(url: str) -> str | None:
        rel = rel_from_url(url)
        if not rel:
            return url                       # cover cards, foreign urls
        if rel in gone:
            return None
        return "/outputs/" + mapping[rel] if rel in mapping else url

    touched = False
    for evt in agent.timeline:
        if evt.get("type") != "generation":
            continue
        files = evt.get("files") or []
        fixed = [fix(f) for f in files]
        if fixed != files:
            touched = True
        evt["files"] = [f for f in fixed if f]
        if evt.get("src"):
            src = fix(evt["src"])
            if src is None:
                src = ""
            elif src != evt["src"]:
                touched = True
            evt["src"] = src
    if touched:
        agent._save_state()
    return touched


def move_images(rel_folder: str, rels: list) -> tuple[list, list]:
    """Move images into a folder ("" = the gallery root). Returns
    (mapping old rel -> new rel, rel paths that could not be moved)."""
    dest = out_dir(rel_folder)
    if dest is None:
        return [], list(rels)
    mapping, failed = [], []
    for old_rel in rels:
        src = out_file(old_rel)
        if src is None:
            failed.append(old_rel)
            continue
        if src.parent == dest:
            continue                            # already there — no-op
        try:
            dest.mkdir(parents=True, exist_ok=True)
            new_name = unique_name(dest, src.name)
            shutil.move(str(src), str(dest / new_name))
        except OSError:
            failed.append(old_rel)
            continue
        new_dir = rel_of(dest)
        mapping.append((old_rel, f"{new_dir}/{new_name}" if new_dir
                        else new_name))
    return mapping, failed


def _positive_prompts(timeline: list) -> dict:
    """rel path -> positive prompt, from the chat timeline (no file reads)."""
    out: dict = {}
    for evt in timeline or []:
        if evt.get("type") != "generation":
            continue
        prompt = (evt.get("gen") or {}).get("prompt")
        if not prompt:
            continue
        urls = list(evt.get("files") or [])
        if evt.get("src"):
            urls.append(evt["src"])
        for u in urls:
            rel = rel_from_url(u)
            if rel:
                out[rel] = prompt
    return out


# rel -> ((mtime_ns, size), positive, negative). A recursive search over a
# few thousand files would otherwise re-parse every PNG chunk on each query.
_prompt_cache: dict = {}


def _prompts_of(path: Path, rel: str) -> tuple:
    """(positive, negative) for an image, memoised on size+mtime so repeated
    searches only pay for each PNG once."""
    try:
        st = path.stat()
    except OSError:
        return "", ""
    key = (st.st_mtime_ns, st.st_size)
    hit = _prompt_cache.get(rel)
    if hit is not None and hit[0] == key:
        return hit[1], hit[2]
    info = parse_png_info(path)
    got = (info.get("prompt", "").strip(), info.get("negative", "").strip())
    if len(_prompt_cache) > 8000:              # bounded; a plain clear is fine
        _prompt_cache.clear()
    _prompt_cache[rel] = (key, got[0], got[1])
    return got


def images_matching(parent: str, needle: str, timeline: list) -> dict:
    """Images sitting directly in folder `parent` whose POSITIVE prompt
    contains `needle` (case-insensitive substring). Negative prompts never
    match. The PNG's own parameters chunk wins — it records what was really
    generated — and the timeline's gen snapshots cover images whose chunk is
    missing. Returns {rels, checked, nometa, total}: `checked` had a readable
    positive prompt, `nometa` had none at all, so the caller can tell "no
    match" apart from "nothing to match against"."""
    folder = out_dir(parent)
    rels, checked, nometa, total = [], 0, 0, 0
    if folder is None or not folder.is_dir():
        return {"rels": rels, "checked": 0, "nometa": 0, "total": 0}
    known = _positive_prompts(timeline)
    low = needle.casefold()
    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return {"rels": rels, "checked": 0, "nometa": 0, "total": 0}
    for p in entries:
        if p.name.startswith("."):
            continue                          # .thumbs and friends
        try:
            if p.is_dir() or p.suffix.lower() not in IMG_EXTS:
                continue
        except OSError:
            continue
        total += 1
        rel = join_rel(parent, p.name)
        prompt, _neg = _prompts_of(p, rel)
        if not prompt:
            prompt = (known.get(rel) or "").strip()
        if not prompt:
            nometa += 1
            continue
        checked += 1
        if low in prompt.casefold():
            rels.append(rel)
    return {"rels": rels, "checked": checked, "nometa": nometa, "total": total}


def search_images(parent: str, query: str, timeline: list) -> dict:
    """Recursive prompt search under `parent` ("" = the whole gallery).

    `query` is a comma-separated list of keywords; a file matches when its
    POSITIVE prompt contains every one of them (case-insensitive substring).
    Scoping follows the folder you're browsing: a search inside "Venti" walks
    "Venti" and everything below it, never the gallery root. Returns
    {folder, q, keywords, images, scanned, total, truncated} — `total` is the
    number of hits before the cap."""
    folder = out_dir(parent)
    keywords = [k.strip().casefold() for k in str(query or "").split(",")]
    keywords = [k for k in keywords if k]
    out = {"folder": _safe_rel(parent) or "", "q": str(query or "").strip(),
           "keywords": keywords, "images": [], "scanned": 0,
           "total": 0, "truncated": False}
    if folder is None or not folder.is_dir() or not keywords:
        return out
    known = _positive_prompts(timeline)
    hits = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.startswith(".") or Path(name).suffix.lower() not in IMG_EXTS:
                continue
            p = Path(root) / name
            rel = rel_of(p)
            prompt, _neg = _prompts_of(p, rel)
            if not prompt:
                prompt = (known.get(rel) or "").strip()
            if not prompt:
                continue
            out["scanned"] += 1
            low = prompt.casefold()
            if not all(k in low for k in keywords):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            hits.append({"rel": rel, "name": name,
                         "folder": rel.rsplit("/", 1)[0] if "/" in rel else "",
                         "bytes": st.st_size, "mtime": int(st.st_mtime),
                         "seed": _seed_from_name(name)})
    hits.sort(key=lambda h: h["mtime"], reverse=True)
    out["total"] = len(hits)
    if len(hits) > SEARCH_LIMIT:
        hits = hits[:SEARCH_LIMIT]
        out["truncated"] = True
    out["images"] = hits
    return out


def parse_png_info(path: Path) -> dict:
    """Read SD WebUI's 'parameters' PNG chunk for the lightbox details."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            text = (im.info or {}).get("parameters") or ""
    except Exception:
        return {}
    out = {"prompt": text, "negative": "", "params": ""}
    if "\nNegative prompt:" in text:
        prompt, rest = text.split("\nNegative prompt:", 1)
        out["prompt"] = prompt.strip()
        if "\nSteps:" in rest:
            neg, params = rest.split("\nSteps:", 1)
            out["negative"] = neg.strip()
            out["params"] = ("Steps:" + params).strip()
        else:
            out["negative"] = rest.strip()
    elif "\nSteps:" in text:
        prompt, params = text.split("\nSteps:", 1)
        out["prompt"] = prompt.strip()
        out["params"] = ("Steps:" + params).strip()
    return out


def advertise_urls(port: int) -> list[str]:
    ips = {"127.0.0.1"}
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            ips.add(ip)
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return [f"http://{ip}:{port}" for ip in sorted(ips)]


# ---------------------------------------------------------------- server

COOKIE = "sdagent_session"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SDAgent/1.0"

    # injected at startup (module-level on the server object)
    @property
    def app(self):
        return self.server.app

    # -------------------------------------------------------- lifecycle

    def handle_one_request(self):
        """Swallow connection resets (phone reloads) quietly."""
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError,
                BrokenPipeError):
            self.close_connection = True

    def log_message(self, fmt, *args):
        pass

    # ---------------------------------------------------------- helpers

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return {}

    def _cookies(self) -> dict:
        out = {}
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def _authed(self) -> bool:
        return bool(self._user())

    def _user(self) -> dict:
        """The account making this request, or {} when there isn't one.

        Phase 0 resolves identity only — the Agent is still process-wide.
        Phases 1-2 key per-user state off this."""
        return self.app.user_for_token(self._cookies().get(COOKIE, ""))

    def _uid(self) -> str:
        """Id of the account making this request.

        Phase 1 caveat: per-user *storage* is live, but the Agent is still a
        single process-wide instance, so a second user's requests read and
        write the first user's chat. Character reads are already filtered by
        owner. Phase 2 removes this by giving each user their own Agent."""
        return str(self._user().get("id") or "u001")

    def _is_admin(self) -> bool:
        return self.app.is_admin(self._user())

    @property
    def agent(self) -> Agent:
        """This request's Agent — the conversation of the signed-in user.

        Every `self.agent` in the handler became this. The alternative
        (a per-user attribute set at auth time) is the same lookup with more
        ways to be stale."""
        return self.app.agent_for(self._uid())

    @property
    def sd_state(self) -> SDState:
        return self.agent.sd_state

    def _json(self, obj, code: int = 200, cache: str | None = None,
              headers: list | None = None):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache or "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for h in headers or []:
            self.send_header(*h)
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path: Path, cache: str | None = None,
              mime: str | None = None,
              extra: list[tuple[str, str]] | None = None):
        try:
            data = path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type",
                         mime or STATIC_MIME.get(path.suffix.lower(),
                                                 "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache or "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _serve_login_bg(self, path: str):
        """GET /login-bg — a randomly chosen login backdrop, unauthenticated.

        Deliberately separate from the gallery. This host is public, so anything
        served here is readable by anyone and crawlable; only images safe to show
        a stranger belong in data/login-bg/, and the owner curates that by hand.

        Picks at random per request but sends `no-cache` with an ETag, so the
        browser revalidates each load (giving a new image) yet a repeat pick
        costs a 304 rather than re-downloading the bytes.
        """
        allp = sorted(LOGIN_BG_DIR.glob("*.jpg")) if LOGIN_BG_DIR.is_dir() else []
        # ?orient=portrait|landscape — the gate's CSS orientation media query
        # picks the URL, so the browser decides and no JS is involved. Falls
        # back to the full pool when a user has only one orientation.
        want = (parse_qs(urlparse(self.path).query).get("orient") or [""])[0]
        picks = ([p for p in allp if f"-{want}." in p.name]
                 if want in ("portrait", "landscape") else allp) or allp
        if not picks:
            # No curated images yet — 204 lets the gate keep its gradient instead
            # of showing a broken background.
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        name = ""
        if path.startswith("/login-bg/"):
            want = _safe_name(path[len("/login-bg/"):])
            chosen = next((p for p in picks if p.stem == want), None)
            # An unknown or traversing name is a 404, not a silent fall back to
            # a random pick — answering 200 to "../etc/passwd" hides probing and
            # makes the response look like it meant something.
            if not chosen:
                self.send_error(404)
                return
            name = chosen.name
        else:
            name = random.choice(picks).name
        f = LOGIN_BG_DIR / name
        try:
            etag = f'"{int(f.stat().st_mtime)}-{f.stat().st_size}"'
        except OSError:
            self.send_error(404)
            return
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        self._file(f, cache="no-cache", extra=[("ETag", etag)])

    def _denied(self):
        self._json({"error": "unauthorized"}, 401)

    # --------------------------------------------------------------- GET

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        # Outputs scope for this request. Before the auth gate it is unset,
        # so any gallery code reached on a public path reads the unscoped
        # root — which nothing public serves.
        scope_uid("")

        # ---- public (no auth): app shell + manifest + service worker ----
        if path in ("/", "/index.html", "/app", "/app/"):
            self._file(APP_DIR / "index.html")
            return
        if path in ("/manifest.webmanifest", "/sw.js", "/offline.html"):
            self._file(APP_DIR / path.lstrip("/"))
            return
        if path.startswith("/static/"):
            name = _safe_name(path[len("/static/"):])
            f = APP_DIR / "static" / name if name else None
            if f and f.is_file():
                self._file(f)
            else:
                self.send_error(404)
            return
        if path == "/api/auth":
            u = self._user()
            self._json({"authed": bool(u),
                        "user": public_user(u) if u else None})
            return
        if path.startswith("/login-bg"):
            self._serve_login_bg(path)
            return
        if path == "/favicon.ico":
            f = APP_DIR / "static" / "icon-192.png"
            if f.is_file():
                self._file(f, cache="public, max-age=604800")
            else:
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
            return

        # ---- everything below requires the session cookie ----
        if not self._authed():
            self._denied()
            return
        scope_uid(self._uid())     # gallery reads are per-user from here on

        if path.startswith("/outputs/") or path.startswith("/thumb/"):
            prefix = "/outputs/" if path.startswith("/outputs/") else "/thumb/"
            as_uid, rel = split_scope(path[len(prefix):])
            if as_uid:
                # An admin browsing another user's gallery. A non-admin
                # asking for `~u001/...` is simply looking for a folder that
                # does not exist in their own tree.
                scope_as(self._uid(), as_uid, self._is_admin())
            f = out_file(rel)
            if not f:
                self.send_error(404)
                return
            if prefix == "/thumb/":
                t = thumbnail(f)
                self._file(t if t else f, cache="private, max-age=86400",
                           mime="image/jpeg" if t else None)
            else:
                # /outputs/ is the original file, full resolution — chat
                # cards, the lightbox and downloads all use it.
                self._file(f, cache="private, max-age=86400")
        elif path == "/api/status":
            self.api_status()
        elif path == "/api/llm/models":
            self.api_llm_models()
        elif path == "/api/gallery":
            qs = parse_qs(urlparse(self.path).query)
            as_uid, raw_folder = split_scope((qs.get("folder") or [""])[0])
            if as_uid and scope_as(self._uid(), as_uid, self._is_admin()):
                audit(self._user(), "gallery.other", as_uid,
                      raw_folder or "(root)")
            folder = _safe_rel(raw_folder)
            if folder is None:
                self._json({"error": "bad folder"}, 400)
                return
            if (qs.get("q") or [""])[0].strip():
                self._json(search_images(folder,
                                         (qs.get("q") or [""])[0],
                                         self.agent.timeline))
                return
            data = gallery_listing(folder)
            if (qs.get("tree") or [""])[0]:
                data["tree"] = gallery_tree()
            self._json(data)
        elif path == "/api/history":
            self._json({"timeline": self.agent.timeline,
                        "busy": self.agent.lock.locked(),
                        "auto_images": self.agent.auto_images,
                        "feedback": self.agent.feedback})
        elif path == "/api/settings":
            self.api_settings()
        elif path == "/api/audit":
            self.api_audit()
        elif path == "/api/admin/users":
            self.api_admin_users()
        elif path == "/api/characters":
            self.api_characters_list()
        elif path == "/api/chats":
            self.api_chats_list()
        elif path == "/api/personas":
            self.api_personas_list()
        elif path == "/api/loras":
            self.api_loras()
        elif path == "/api/loras/search":
            self.api_loras_search()
        elif path.startswith("/api/avatar/"):
            self.api_avatar(path[len("/api/avatar/"):])
        elif path.startswith("/api/cover/"):
            self.api_cover(path[len("/api/cover/"):])
        elif path == "/api/image_info":
            self.api_image_info()
        elif path == "/api/regen_meta":
            qs = parse_qs(urlparse(self.path).query)
            rel = _safe_rel((qs.get("name") or [""])[0])
            if not rel or not out_file(rel):
                self._json({"error": "image not found"}, 404)
                return
            agent = self.agent
            args, origin = gen_args_for(agent, rel)
            args.setdefault("prompt", "")
            # the sheet needs the architecture to pre-fill blank fields with
            # that family's own defaults instead of SDXL's
            model = str(args.get("model") or "")
            arch = origin.get("arch") or agent.client.arch_for(model)
            if not model:
                arch = agent.client.current_arch()
                model = agent.client.current_model()
            sd = self.sd_state.snapshot()
            self._json({"gen": args, "origin": origin.get("source", "png"),
                        "arch": arch, "model": model,
                        "profile": _public_profile(agent.client, arch),
                        "defaults": regen_defaults(agent.client, model),
                        "models": sd["models"],
                        "capabilities": sd["caps"]})
        else:
            self.send_error(404)

    # -------------------------------------------------------------- POST

    def do_POST(self):
        path = urlparse(self.path).path
        scope_uid("")

        # ---- public ----
        if path == "/api/login":
            body = self._body()
            name = str(body.get("name") or body.get("user") or "")
            pw = str(body.get("password", ""))
            self.app._sync_disk()
            user = resolve_user(self.app.users, name)
            # A disabled account must fail the same way a wrong password
            # does, or the error becomes an account-enumeration oracle.
            ok = bool(user) and not user.get("disabled") and \
                verify_password(pw, str(user.get("pass_hash") or ""))
            if ok:
                with self.app.sessions_lock:
                    token = new_session(self.app.sessions,
                                        str(user.get("id")))
                self._json({"ok": True, "user": public_user(user)},
                           headers=[(
                               "Set-Cookie",
                               f"{COOKIE}={token}; Path=/; HttpOnly; "
                               f"SameSite=Lax; Max-Age={SESSION_TTL}")])
            else:
                time.sleep(0.6)                       # slow brute force
                msg = "wrong password" if user else \
                    "unknown user" if name.strip() else \
                    "several accounts — enter your username"
                audit({}, "login.fail", str(user.get("id") or ""),
                      f"named {name.strip()!r}" if name.strip() else "no name")
                self._json({"error": msg}, 401)
            return
        if path == "/api/logout":
            token = self._cookies().get(COOKIE, "")
            if token:
                with self.app.sessions_lock:
                    drop_session(self.app.sessions, token)
            self._json({"ok": True}, headers=[(
                "Set-Cookie",
                f"{COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")])
            return

        # ---- authed ----
        if not self._authed():
            self._denied()
            return
        scope_uid(self._uid())     # gallery reads are per-user from here on

        if path == "/api/chat":
            self.api_chat()
        elif path == "/api/regenerate":
            self.api_regenerate()
        elif path == "/api/chat/new":
            self._body()          # drain request body (keep-alive framing)
            agent = self.agent
            if not agent.lock.acquire(blocking=False):
                self._json({"error": "busy — a turn is already running"},
                           409)
                return
            try:
                agent.new_chat()
            finally:
                agent.lock.release()
            self._json({"ok": True, "timeline": agent.timeline,
                        "auto_images": agent.auto_images,
                        "feedback": agent.feedback})
        elif path == "/api/chat/select":
            self.api_chat_select()
        elif path == "/api/chat/delete":
            self.api_chat_delete()
        elif path == "/api/chat/auto_images":
            self.api_chat_auto_images()
        elif path == "/api/chat/feedback":
            self.api_chat_feedback()
        elif path == "/api/characters":
            self.api_characters_save()
        elif path == "/api/character/select":
            self.api_character_select()
        elif path == "/api/character/delete":
            self.api_character_delete()
        elif path == "/api/character/favourite":
            self.api_character_favourite()
        elif path == "/api/personas":
            self.api_personas_save()
        elif path == "/api/loras":
            self.api_loras()
        elif path == "/api/loras/search":
            self.api_loras_search()
        elif path == "/api/persona/select":
            self.api_persona_select()
        elif path == "/api/persona/delete":
            self.api_persona_delete()
        elif path == "/api/scenario":
            self.api_scenario_save()
        elif path == "/api/scenario/delete":
            self.api_scenario_delete()
        elif path == "/api/chat/scenario":
            self.api_chat_scenario()
        elif path == "/api/char/generate":
            self.api_char_generate()
        elif path == "/api/image/generate":
            self.api_image_generate()
        elif path == "/api/settings":
            self.api_settings()
        elif path == "/api/admin/users":
            self.api_admin_users()
        elif path == "/api/model":
            self.api_model()
        elif path == "/api/model/components":
            self.api_model_components()
            self.api_model()
        elif path == "/api/delete":
            self.api_delete_images()
        elif path == "/api/gallery/folder":
            self.api_gallery_folder()
        elif path == "/api/delete_event":
            body = self._body()
            tl = self.agent.timeline
            if isinstance(body.get("indices"), list):
                idxs = set()
                for v in body["indices"][:200]:
                    try:
                        i = int(v)
                    except (TypeError, ValueError):
                        continue
                    if 0 <= i < len(tl):
                        idxs.add(i)
                if not idxs:
                    self._json({"error": "bad index"}, 400)
                    return
                self.agent.timeline = [e for i, e in enumerate(tl)
                                           if i not in idxs]
                self.agent.clear_feedback()   # it rated what just went
                self.agent._save_state()
                # the caller re-reads /api/history — every later index moved
                self._json({"ok": True, "removed": len(idxs),
                            "timeline": self.agent.timeline,
                            "feedback": self.agent.feedback})
                return
            try:
                idx = int(body.get("index", -1))
            except (TypeError, ValueError):
                idx = -1
            if not (0 <= idx < len(tl)):
                self._json({"error": "bad index"}, 400)
                return
            evt = tl.pop(idx)
            self.agent.clear_feedback()       # it rated what just went
            self.agent._save_state()
            self._json({"ok": True, "removed": evt.get("type"),
                        "timeline": self.agent.timeline,
                        "feedback": self.agent.feedback})
        elif path == "/api/edit_event":
            self.api_edit_event()
        elif path == "/api/regen_reply":
            self.api_regen_reply()
        elif path == "/api/abort":
            self.api_abort()
        else:
            self.send_error(404)

    # -------------------------------------------------------------- APIs

    def api_edit_event(self):
        """Rewrite the text of a reply/error bubble in the active chat.

        Both the display timeline and the LLM message list are patched, so
        what the user reads is what the model will see next.
        """
        body = self._body()
        agent = self.agent
        tl = agent.timeline
        try:
            idx = int(body.get("index", -1))
        except (TypeError, ValueError):
            idx = -1
        if not (0 <= idx < len(tl)):
            self._json({"error": "bad index"}, 400)
            return
        evt = tl[idx]
        if evt.get("type") not in ("reply", "error"):
            self._json({"error": "that message can't be edited"}, 400)
            return
        text = str(body.get("text", ""))[:8000].strip()
        if not text:
            self._json({"error": "empty message"}, 400)
            return
        if not agent.lock.acquire(blocking=False):
            self._json({"error": "busy — a turn is already running"}, 409)
            return
        try:
            text = scrub_paths(text)
            evt["text"] = text
            mi = evt.get("mi")
            if isinstance(mi, int) and 1 <= mi <= len(agent.messages) \
                    and agent.messages[mi - 1].get("role") == "assistant":
                agent.messages[mi - 1]["content"] = text
            agent.clear_feedback()     # it rated the old wording
            agent._save_state()
            self._json({"ok": True, "text": text, "timeline": agent.timeline,
                        "feedback": agent.feedback})
        finally:
            agent.lock.release()

    def api_regen_reply(self):
        """Re-ask the model for a reply already on screen.

        The model answers against a working copy of the context and the
        rewind is committed only once a new reply exists, so stopping or
        failing leaves the chat untouched. Anything the old answer had after
        it in the conversation goes with it (branching, not stacking).
        """
        body = self._body()
        try:
            idx = int(body.get("index", -1))
        except (TypeError, ValueError):
            idx = -1
        status, q = run_reply_regen(self.agent, idx)
        if status == "nokey":
            self._json({"error": "no_api_key",
                        "message": "Add your OpenRouter API key in Settings "
                                   "before regenerating."}, 400)
            return
        if status == "busy":
            self._json({"error": "busy — a turn is already running"}, 409)
            return
        if status == "bad_index":
            self._json({"error": "bad index"}, 400)
            return
        self._sse_stream(q)

    def api_abort(self):
        """Stop the running turn and/or image job. Always safe to call."""
        agent = self.agent
        agent.request_stop()
        self._json({"ok": True})

    def _sse_stream(self, q: Queue):
        """Write queue events as an SSE stream until a 'done' event."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")       # delimits the stream
        self.send_header("X-Accel-Buffering", "no")   # nginx-style proxies
        self.end_headers()
        try:
            while True:
                try:
                    evt = q.get(timeout=10)
                except Empty:
                    self.wfile.write(b": ping\n\n")   # keepalive
                    self.wfile.flush()
                    continue
                line = f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()
                if evt.get("type") == "done":
                    break
        except (ConnectionAbortedError, BrokenPipeError, OSError):
            pass                                  # client left; stream ends

    def api_regenerate(self):
        """Re-generate an image directly — no chat message, no LLM turn.
        Streams SSE: progress events, then regen_done / regen_error."""
        body = self._body()
        rel = _safe_rel(str(body.get("name", "")))
        instr = str(body.get("instruction", "")).strip()[:1000]
        over = body.get("overrides")
        overrides = over if isinstance(over, dict) else None
        if not rel or not out_file(rel):
            self._json({"error": "image not found"}, 404)
            return
        agent = self.agent
        if not agent.lock.acquire(blocking=False):
            self._json({"error": "busy — a turn is already running"}, 409)
            return
        q = Queue()
        agent.stop_flag.clear()

        def worker():
            out = {"evt": None, "error": None}

            def work():
                try:
                    out["evt"] = run_regeneration(
                        agent, rel, instr, lambda e: q.put(e), overrides)
                except Exception as e:            # noqa: BLE001 — report all
                    out["error"] = f"{type(e).__name__}: {e}"

            th = threading.Thread(target=work, daemon=True)
            th.start()
            try:
                while th.is_alive():
                    th.join(PROGRESS_POLL)
                    if th.is_alive():
                        try:
                            p = agent.client.progress()
                            q.put({"type": "progress",
                                   "progress": p.get("progress") or 0,
                                   "eta": p.get("eta_relative")})
                        except Exception:
                            pass
                if agent.stopped():
                    q.put({"type": "regen_stopped"})
                elif out["error"]:
                    q.put({"type": "regen_error",
                           "error": scrub_paths(out["error"])})
                else:
                    evt = out["evt"]
                    agent.push(evt)
                    saved = [rel_from_url(f) for f in evt["files"]]
                    new_rel = saved[0]
                    # the picture changed — the conversation must follow it,
                    # or the next turn keeps describing the old one
                    agent.sync_gen_context(rel, new_rel, evt.get("gen"))
                    agent._save_state()
                    q.put({"type": "regen_done",
                           "file": new_rel,
                           # a batch saved every file under one event — the
                           # carousel needs all of them, not just the first
                           "files": saved,
                           "src": rel,
                           "seed": evt.get("seed"),
                           "idx": len(agent.timeline) - 1})
            finally:
                agent.lock.release()
                q.put({"type": "done"})

        threading.Thread(target=worker, daemon=True).start()
        self._sse_stream(q)

    def api_chat(self):
        body = self._body()
        text = str(body.get("message", "")).strip()
        if not text:
            self._json({"error": "empty message"}, 400)
            return
        status, q = run_chat_turn(self.agent, text)
        if status == "nokey":
            self._json({"error": "no_api_key",
                        "message": "Add your OpenRouter API key in Settings "
                                   "before chatting."}, 400)
            return
        if status == "busy":
            self._json({"error": "busy — a turn is already running"}, 409)
            return

        self._sse_stream(q)

    def api_chats_list(self):
        """Every chat of every character, most recently touched first.

        The list is how the user gets back into a conversation, so it spans
        the whole cast: each row carries the character's name and the chat's
        first user message (`chat_preview`) rather than a stored title.
        `?char=<id>` narrows it to one character — nothing in the app needs
        that any more, but it keeps the endpoint cheap to poke at.
        """
        cid = (parse_qs(urlparse(self.path).query).get("char") or [""])[0]
        agent = self.agent
        cards = load_characters()
        if cid:
            cards = [c for c in cards if c.get("id") == cid]
        chats = []
        for card in cards:
            ccid = str(card.get("id") or "")
            if not ccid:
                continue
            for p in agent._list_chats(ccid):
                try:
                    updated = int(p.stat().st_mtime)
                except OSError:
                    continue
                d = agent._load_chat_payload(p)
                chats.append({"char": ccid,
                              "char_name": str(card.get("name") or ccid),
                              "id": p.stem,
                              "preview": chat_preview(d),
                              "updated": updated,
                              "messages": len(d.get("messages") or [])})
        # file mtime, not the payload's "updated": it is what the sort of a
        # single character's own list already uses, and it moves even when a
        # chat was rewritten by something other than a turn
        chats.sort(key=lambda c: c["updated"], reverse=True)
        self._json({"chats": chats,
                    "active": agent.chat_id if agent.char else "",
                    "active_char": agent.char["id"] if agent.char else ""})

    def api_chat_select(self):
        body = self._body()
        cid = str(body.get("char", "")).strip()
        chat_id = str(body.get("chat", "")).strip()
        agent = self.agent
        if not agent.lock.acquire(blocking=False):
            self._json({"error": "busy — a turn is already running"}, 409)
            return
        try:
            agent.select_chat(cid, chat_id)
            self._json({"ok": True, "timeline": agent.timeline,
                        "auto_images": agent.auto_images,
                        "feedback": agent.feedback})
        except ValueError as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:                      # noqa: BLE001
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        finally:
            agent.lock.release()

    def api_chat_delete(self):
        body = self._body()
        cid = str(body.get("char", "")).strip()
        chat_id = str(body.get("chat", "")).strip()
        agent = self.agent
        if not agent.lock.acquire(blocking=False):
            self._json({"error": "busy — a turn is already running"}, 409)
            return
        try:
            agent.delete_chat(cid, chat_id)
            self._json({"ok": True, "timeline": agent.timeline,
                        "auto_images": agent.auto_images,
                        "feedback": agent.feedback})
        finally:
            agent.lock.release()

    def api_chat_feedback(self):
        """Rate the active conversation: "like", "dislike", or "" to clear.

        Captured, not acted on — there is nothing downstream that reads it
        yet. Stored per chat so it survives restarts and archived into the
        chat's own log when the conversation moves on.
        """
        body = self._body()
        rating = body.get("rating", "")
        if not isinstance(rating, str):
            self._json({"error": "rating must be a string"}, 400)
            return
        agent = self.agent
        # a verdict on a chat with nothing in it is noise, and it would sit in
        # the archive until the first reply silently retired it
        if not any(e.get("type") in ("user", "reply") for e in agent.timeline):
            self._json({"error": "nothing to rate yet"}, 400)
            return
        if not agent.lock.acquire(blocking=False):
            self._json({"error": "busy — a turn is already running"}, 409)
            return
        try:
            fb = agent.set_feedback(rating)
            self._json({"ok": True, "feedback": fb})
        finally:
            agent.lock.release()

    def api_chat_auto_images(self):
        """Chat top-bar toggle: may the model generate images on its own?

        Stored per chat, so it survives restarts and each archived chat keeps
        its own setting. Off means the image tools are withheld from the LLM
        request entirely — regenerating from the UI still works.
        """
        body = self._body()
        on = body.get("on")
        if not isinstance(on, bool):
            self._json({"error": "on must be true or false"}, 400)
            return
        agent = self.agent
        if not agent.lock.acquire(blocking=False):
            self._json({"error": "busy — a turn is already running"}, 409)
            return
        try:
            agent.set_auto_images(on)
            self._json({"ok": True, "auto_images": agent.auto_images})
        finally:
            agent.lock.release()

    def api_status(self):
        """Cheap read of everything the UI polls for.

        The image-server half comes from the cached SDState snapshot, so this
        never waits on the WebUI — see SDState for why that matters.
        """
        agent = self.agent
        force = "refresh" in (parse_qs(urlparse(self.path).query) or {})
        sd = self.sd_state.snapshot(force=force)
        char = agent.char
        self._json({
            "sd_url": agent.client.base_url,
            "sd_ok": sd["sd_ok"],
            "sd_error": sd["error"],
            "sd_pending": sd["pending"],
            "current_model": sd["current_model"],
            "current_arch": sd["current_arch"],
            "models": sd["models"],
            "model_detail": sd["detail"],
            "capabilities": sd["caps"],
            "llm": agent.llm_models,
            "has_key": agent.has_key(),
            "gallery_count": gallery_total(),
            "busy": agent.lock.locked(),
            "auto_images": agent.auto_images,
            "feedback": agent.feedback,
            "character": ({"id": char["id"],
                           "name": char.get("name", "")}
                          if char else None),
        })

    def api_llm_models(self):
        """OpenRouter's catalogue for the Settings model picker.

        Served from the day-old disk cache after the first call; `refresh=1`
        re-queries. A fetch failure returns the last good list plus an error
        string rather than an empty picker.
        """
        force = "refresh" in (parse_qs(urlparse(self.path).query) or {})
        self._json(fetch_openrouter_models(
            self.agent.cfg.get("openrouter_key", ""), force=force))

    # ------------------------------------------------------- characters

    @staticmethod
    def _avatar_path(cid: str) -> Path | None:
        if not CHAR_ID_RE.match(cid or ""):
            return None
        try:
            for f in AVATAR_DIR.glob(cid + ".*"):
                return f
        except OSError:
            pass
        return None

    @staticmethod
    def _scenario_out(cid: str, s: dict) -> dict:
        """Scenario dict as seen by the client — adds its cover URL."""
        s = dict(s)
        cov = cover_url("sc-" + cid + "." + str(s.get("id", "")))
        if cov:
            s["cover"] = cov
        else:
            s.pop("cover", None)
        return s

    def api_characters_list(self):
        uid = self._uid()
        # ensure_assistant is a write, so it runs at startup, not on a read
        # path that N users hit at once. Here we only filter to what this
        # user may see: their own cards plus shared ones.
        cards = visible_characters(uid, self._is_admin())
        out = []
        for c in cards:
            c = dict(c)
            c["owned"] = can_edit_card(uid, c, self._is_admin())
            av = self._avatar_path(c["id"])
            if av:
                try:
                    c["avatar"] = ("/api/avatar/" + c["id"]
                                   + "?v=" + str(int(av.stat().st_mtime)))
                except OSError:
                    pass
            cov = cover_url("char-" + c["id"])
            if cov:
                c["cover"] = cov
            if isinstance(c.get("scenarios"), list):
                c["scenarios"] = [self._scenario_out(c["id"], s)
                                  for s in c["scenarios"]]
            try:
                c["chats"] = len(self.agent._list_chats(c["id"]))
            except Exception:
                c["chats"] = 0
            out.append(c)
        # favourites first, most recently pinned at the very top; the rest
        # keep creation order (sort is stable)
        out.sort(key=lambda c: (not c.get("fav"), -_fav_at(c)))
        active = self.agent.char
        self._json({"characters": out,
                    "active": active["id"] if active else ""})

    def api_loras(self):
        """The LoRA library, merged with what is actually on the server.

        The two are kept separate on purpose. A note about a LoRA the user is
        thinking of buying is worth writing down, and it must not disappear
        because the file is not downloaded yet — so a library entry with no
        file comes back as `installed: false` rather than being dropped.

        GET returns the merged view for the settings list; POST edits one
        record or asks the server to rescan its Lora folder.
        """
        if self.command == "GET":
            self._json({"loras": self._loras_merged(),
                        "current_arch": self._safe_arch()})
            return
        body = self._body()
        action = str(body.get("action") or "").strip()
        if action == "refresh":
            try:
                self.agent.client.refresh_loras()
            except Exception as e:                     # noqa: BLE001
                self._json({"error": f"rescan failed: {e}"}, 502)
                return
            self._json({"ok": True, "loras": self._loras_merged(),
                        "current_arch": self._safe_arch()})
            return

        name = str(body.get("name") or "").strip()
        if not name:
            self._json({"error": "name required"}, 400)
            return
        if not lora_referenceable(name):
            self._json({"error": "that name cannot be used: a LoRA name must "
                                 "not contain ':' or '>'"}, 400)
            return
        lib = load_lora_library()
        if action == "delete":
            lib.pop(name, None)
            # references from cards are dropped too, so a card can never point
            # at a record that no longer exists
            cards = load_characters()
            touched = False
            for c in cards:
                refs = c.get("loras")
                if isinstance(refs, list) and name in refs:
                    c["loras"] = [n for n in refs if n != name]
                    touched = True
            if touched:
                save_characters(cards)
        elif action == "save":
            lib[name] = _clean_lora_record(body.get("record") or {}, name)
        else:
            self._json({"error": "unknown action"}, 400)
            return
        try:
            save_lora_library(lib)
        except OSError as e:
            self._json({"error": f"could not save: {e}"}, 500)
            return
        # the active card's prompt block quotes trigger words and notes, so a
        # saved edit has to reach the system message without a restart
        agent = self.agent
        if agent.messages and agent.messages[0].get("role") == "system":
            agent.messages[0] = agent._sys_msg()
        self._json({"ok": True, "loras": self._loras_merged(),
                    "current_arch": self._safe_arch()})

    def _safe_arch(self) -> str:
        try:
            return self.agent.client.current_arch()
        except Exception:                              # noqa: BLE001
            return ""

    def lora_dir(self) -> Path:
        """Where this app writes LoRAs — its own folder, never Forge's.

        Forge scans `lora_dir` plus every `--lora-dirs` entry
        (`extensions-builtin/sd_forge_lora/networks.py:209`), so this folder is
        a second root rather than a replacement: the LoRAs you drop into
        models/Lora by hand keep working alongside whatever is downloaded here.

        Not derived from the running server's reported paths. That would return
        Forge's own models/Lora, which this process cannot write — the unit's
        ProtectHome=read-only makes it read-only to us, and the error arrives
        only after the whole file has streamed.
        """
        d = LORA_DIR_FALLBACK
        d.mkdir(parents=True, exist_ok=True)
        return d

    def api_loras_search(self):
        """GET browse Civitai; POST install one. Results are server-cached."""
        if self.command == "GET":
            qs = parse_qs(urlparse(self.path).query)
            installed = set(_installed_loras(self.agent.client))
            try:
                out = civitai_search(
                    (qs.get("q") or [""])[0],
                    (qs.get("sort") or [""])[0],
                    (qs.get("cursor") or [""])[0],
                    (qs.get("limit") or ["24"])[0],
                    installed,
                    (qs.get("tag") or [""])[0],
                    (qs.get("period") or [""])[0])
            except SDWebUIError as e:
                self._json({"error": str(e)}, 502)
                return
            except (TypeError, ValueError) as e:
                self._json({"error": f"bad search: {e}"}, 400)
                return
            self._json(out)
            return

        body = self._body()
        try:
            vid = int(body.get("version_id") or 0)
        except (TypeError, ValueError):
            vid = 0
        if vid <= 0:
            self._json({"error": "version_id required"}, 400)
            return
        dest = self.lora_dir()
        # Cheap preflight: the app's own folder is inside ReadWritePaths, so
        # this normally passes. It is here because the failure it guards against
        # is silent in the worst way — a LoRA downloaded into a folder Forge
        # does not watch simply never appears, with no error anywhere.
        probe = dest / ".sdagent-write-probe"
        try:
            probe.write_text("x")
            probe.unlink()
        except OSError as e:
            self._json({"error":
                        f"cannot write to {dest} ({e.strerror}) — nothing "
                        f"was downloaded"}, 500)
            return
        try:
            info = download_lora(vid, dest)
        except ValueError as e:
            self._json({"error": str(e)}, 400)
            return
        except OSError as e:
            self._json({"error": f"could not write to {dest}: {e}"}, 500)
            return
        except requests.RequestException as e:
            self._json({"error": f"download failed: {e}"}, 502)
            return

        # Seed the notes from what the uploader already wrote, so an
        # installed LoRA is never a bare filename with an empty record.
        # Deliberately not gated on trainedWords: plenty of uploads have an
        # empty list and a perfectly good description, and gating on it left
        # those with nothing. Only fills blanks — whatever the user wrote is
        # theirs and is never overwritten.
        seeded = False
        try:
            ver = _civitai_get(f"/model-versions/{vid}", {})
            trained = [str(t).strip() for t in (ver.get("trainedWords") or [])
                       if str(t).strip()][:4]
            desc = _plain(ver.get("description"), 400)
            name = info["name"]
            if (trained or desc) and name not in load_lora_library():
                lib = load_lora_library()
                lib[name] = _clean_lora_record({
                    "weight": 0.9 if trained else 1.0,
                    "trigger": ", ".join(trained)[:120],
                    "arch": info["arch"],
                    "notes": "From CivitAI" + (f" ({info['base']})"
                                                if info["base"] else ""),
                    "example": desc,
                    "enabled": True,
                }, name)
                save_lora_library(lib)
                seeded = True
        except Exception:                              # noqa: BLE001
            pass            # seeding is a nicety; never fail the install

        try:
            self.agent.client.refresh_loras()
        except Exception as e:                         # noqa: BLE001
            return self._json({"ok": True, "installed": info,
                               "warning": f"downloaded, but the server did "
                                          f"not re-scan: {e}"}, 200)
        rows = self._loras_merged()
        # The download succeeding is not the same as Forge being able to see
        # it. If --lora-dirs was lost (settings file edited, Forge restarted
        # the old way) the file sits here forever and simply never appears.
        seen = any(r["name"] == info["name"] and r["installed"] for r in rows)
        payload = {"ok": True, "installed": info, "loras": rows,
                   "current_arch": self._safe_arch(), "seeded": seeded}
        if not seen:
            payload["warning"] = (
                f"{info['file']} downloaded to {dest}, but Forge is not "
                f"scanning that folder — it will not appear until "
                f"--lora-dirs {dest} is in webui.settings.sh and Forge "
                f"restarts.")
            return self._json(payload, 200)
        return self._json(payload)

    def _loras_merged(self) -> list[dict]:
        """Library records joined onto the server's installed list.

        Keyed by Forge's own name, because that is what a card stores and what
        `<lora:…>` has to carry. Names present in only one side are the two
        interesting cases: described-but-absent, and installed-but-undescribed.
        """
        lib = load_lora_library()
        disk = _installed_loras(self.agent.client)
        rows: list[dict] = []
        for name in sorted(set(lib) | set(disk), key=str.lower):
            d = disk.get(name) or {}
            rec = _clean_lora_record(lib.get(name) or {}, name)
            row = {
                "name": name,
                "installed": name in disk,
                "alias": d.get("alias", name),
                "path": d.get("path", ""),
                "file_arch": d.get("arch", ""),
                "rank": d.get("rank"),
                "described": name in lib,
            }
            row.update(rec)
            # a stored arch wins over the guessed one: it is the user's call
            row["arch"] = rec["arch"] or d.get("arch", "")
            row["mismatch"] = _arch_mismatch(row["arch"], self._safe_arch())
            rows.append(row)
        return rows

    def api_avatar(self, cid_raw: str):
        cid = _safe_name(cid_raw.strip().lower()) or ""
        f = self._avatar_path(cid)
        if f and f.is_file():
            self._file(f, cache="private, max-age=86400")
        else:
            self.send_error(404)

    def api_cover(self, key_raw: str):
        key = _safe_name(key_raw.strip().lower()) or ""
        f = cover_file(key)
        if f and f.is_file():
            self._file(f, cache="private, max-age=86400")
        else:
            self.send_error(404)

    def api_characters_save(self):
        """Create (no id) or update (with id) a character card."""
        body = self._body()
        name = str(body.get("name", "")).strip()
        if not name:
            self._json({"error": "name required"}, 400)
            return
        uid = self._uid()
        cards = load_characters()
        cid = str(body.get("id", "")).strip()
        existing = next((c for c in cards if c.get("id") == cid), None) \
            if cid else None
        # Someone else's shared card is read-only: you can chat with it, but
        # editing it would rewrite their work and republish it under your
        # name. Admins bypass (MULTIUSER.md decision 5).
        if existing and not can_edit_card(uid, existing, self._is_admin()):
            audit(self._user(), "character.edit.other",
                  str(existing.get("owner") or ""), f"tried {cid}")
            self._json({"error": "that character belongs to someone else"},
                       403)
            return
        if existing and self._is_admin() and \
                card_owner(existing) != uid:
            audit(self._user(), "character.edit.other",
                  card_owner(existing), f"edited {cid}")
        if not existing:
            cid = slugify(name)
            while any(c.get("id") == cid for c in cards):
                cid = cid[:35] + "-" + secrets.token_hex(2)
        card = dict(existing) if existing else {
            "id": cid, "created": int(time.time()), "owner": uid}
        card["owner"] = card_owner(card) or uid
        # Shared is the default; private is the opt-out. The cast is meant to
        # be common to everyone, and ownership governs who may *edit* a card,
        # not who may chat with it. `True` for a card that predates the flag.
        card["shared"] = bool(body.get("shared", True))
        card["name"] = name
        card["appearance"] = str(body.get("appearance", "")).strip()[:4000]
        card["persona"] = str(body.get("persona", "")).strip()[:8000]
        card["greeting"] = str(body.get("greeting", "")).strip()[:2000]
        card["checkpoint"] = str(body.get("checkpoint", "")).strip()[:200]
        # Short labels shown as chips on the character card. Capped and
        # trimmed because they render as pills beside a name and an over-long
        # one pushes the greeting off the row.
        tsel = body.get("tags")
        if isinstance(tsel, (list, tuple)):
            tags: list[str] = []
            for t in tsel:
                s = re.sub(r"\s+", " ", str(t or "")).strip()[:24]
                if s and s not in tags and len(tags) < 6:
                    tags.append(s)
            card["tags"] = tags
        else:
            card["tags"] = []
        # References into the LoRA library, in application order. Capped here
        # as well as at read time so a hand-edited file with fifty entries
        # cannot smuggle past the limit by skipping the form.
        lsel = body.get("loras")
        if isinstance(lsel, (list, tuple)):
            names: list[str] = []
            for n in lsel:
                s = str(n or "").strip()
                if s and s not in names and len(names) < LORA_PER_CARD:
                    names.append(s)
            card["loras"] = names[:LORA_PER_CARD]
        else:
            card["loras"] = []
        size = body.get("size")
        if isinstance(size, (list, tuple)) and len(size) == 2:
            try:
                card["size"] = [int(size[0]), int(size[1])]
            except (TypeError, ValueError):
                card["size"] = []
        else:
            card["size"] = []
        try:
            card["temp"] = round(float(body.get("temp")), 3) \
                if body.get("temp") is not None else None
        except (TypeError, ValueError):
            card["temp"] = None
        if isinstance(card["temp"], float) and \
                not (0.1 <= card["temp"] <= 2.0):
            card["temp"] = None
        try:
            card["max_tokens"] = int(body.get("max_tokens")) \
                if body.get("max_tokens") is not None else None
        except (TypeError, ValueError):
            card["max_tokens"] = None
        if isinstance(card["max_tokens"], int) and \
                not (16 <= card["max_tokens"] <= 8192):
            card["max_tokens"] = None
        avatar = str(body.get("avatar", "") or "")
        m = re.match(r"data:image/(png|jpe?g|webp);base64,(.+)", avatar,
                     re.S)
        if m and len(avatar) < 3_500_000:
            ext = "jpg" if m.group(1).startswith("jp") else m.group(1)
            try:
                AVATAR_DIR.mkdir(parents=True, exist_ok=True)
                for old in AVATAR_DIR.glob(cid + ".*"):
                    old.unlink()            # extension may change between saves
                (AVATAR_DIR / f"{cid}.{ext}").write_bytes(
                    base64.b64decode(m.group(2)))
            except Exception:
                pass
        if body.get("avatar_remove"):
            try:
                for old in AVATAR_DIR.glob(cid + ".*"):
                    old.unlink()
            except OSError:
                pass
        cover = str(body.get("cover", "") or "")
        if cover:
            write_cover("char-" + cid, cover)
        if body.get("cover_remove"):
            remove_cover("char-" + cid)
        if existing:
            cards = [card if c.get("id") == cid else c for c in cards]
        else:
            cards.append(card)
        save_characters(cards)

        agent = self.agent
        if agent.char and agent.char["id"] == cid:
            agent.char = card                       # refresh live card
            if agent.messages and \
                    agent.messages[0].get("role") == "system":
                agent.messages[0] = agent._sys_msg()
        # migration: the first created character adopts the unbound chat
        if not existing and agent.char is None and agent.timeline \
                and agent._home == agent.default_chat_file():
            agent.bind_active_chat(card)
        self._json({"ok": True, "id": cid})

    def api_character_select(self):
        body = self._body()
        cid = str(body.get("id", "")).strip()
        uid = self._uid()
        agent = self.agent
        if not agent.lock.acquire(blocking=False):
            self._json({"error": "busy — a turn is already running"}, 409)
            return
        try:
            if cid:
                card = next((c for c in visible_characters(uid, self._is_admin())
                             if c.get("id") == cid), None)
                if not card:
                    self._json({"error": "character not found"}, 404)
                    return
                agent.set_character(card)
            else:
                agent.set_character(None)
            self._json({"ok": True, "timeline": agent.timeline,
                        "auto_images": agent.auto_images,
                        "feedback": agent.feedback})
        finally:
            agent.lock.release()

    def api_character_favourite(self):
        """Pin a character to the top of the list, or unpin it.

        `fav` lives on the card, so pinning someone else's shared card would
        reorder *their* list. Owner-only (admins excepted)."""
        body = self._body()
        cid = str(body.get("id", "")).strip()
        cards = load_characters()
        card = next((c for c in cards if c.get("id") == cid), None)
        if not card:
            self._json({"error": "character not found"}, 404)
            return
        if not can_edit_card(self._uid(), card, self._is_admin()):
            audit(self._user(), "character.fav.other",
                  card_owner(card), f"tried {cid}")
            self._json({"error": "that character belongs to someone else"},
                       403)
            return
        want = body.get("favourite")
        on = (not card.get("fav")) if want is None else bool(want)
        if on:
            card["fav"] = True
            card["fav_at"] = time.time()   # pins land at the very top
        else:
            card["fav"] = False
            card.pop("fav_at", None)
        save_characters(cards)
        self._json({"ok": True, "favourite": on})

    def api_character_delete(self):
        body = self._body()
        cid = str(body.get("id", "")).strip()
        cards = load_characters()
        card = next((c for c in cards if c.get("id") == cid), None)
        if not card:
            self._json({"error": "character not found"}, 404)
            return
        if cid == ASSISTANT_ID:
            self._json({"error": "the assistant cannot be deleted"}, 400)
            return
        if not can_edit_card(self._uid(), card, self._is_admin()):
            audit(self._user(), "character.delete.other",
                  card_owner(card), f"tried {cid}")
            self._json({"error": "that character belongs to someone else"},
                       403)
            return
        if self._is_admin() and card_owner(card) != self._uid():
            audit(self._user(), "character.delete.other", card_owner(card),
                  f"deleted {cid}")
        agent = self.agent
        if not agent.lock.acquire(blocking=False):
            self._json({"error": "busy — a turn is already running"}, 409)
            return
        try:
            if agent.char and agent.char["id"] == cid:
                agent.set_character(None)
            agent.delete_character(cid)
            save_characters([c for c in cards if c.get("id") != cid])
            av = self._avatar_path(cid)
            if av:
                try:
                    av.unlink()
                except OSError:
                    pass
            remove_cover("char-" + cid)
            for f in COVER_DIR.glob("sc-" + cid + ".*"):
                try:
                    f.unlink()
                except OSError:
                    pass
            self._json({"ok": True})
        finally:
            agent.lock.release()

    def api_image_info(self):
        qs = parse_qs(urlparse(self.path).query)
        rel = _safe_rel((qs.get("name") or [""])[0]) or ""
        f = out_file(rel)
        if not f:
            self._json({"error": "not found"}, 404)
            return
        info = {"name": Path(rel).name, "rel": rel,
                "seed": _seed_from_name(rel),
                "prompt": "", "negative": "", "params": ""}
        try:
            st = f.stat()
            info["bytes"] = st.st_size
            info["mtime"] = int(st.st_mtime)
        except OSError:
            pass
        try:
            from PIL import Image
            with Image.open(f) as im:
                info["width"], info["height"] = im.size
        except Exception:
            pass
        info.update(parse_png_info(f))
        self._json(info)

    # ------------------------------------------------- gallery folders

    def _folder_lock(self) -> bool:
        """Folder mutations share the agent lock so they never race a
        generation that is mid-write or mid-img2img-read."""
        if not self.agent.lock.acquire(blocking=False):
            self._json({"error": "busy — a turn is already running"}, 409)
            return False
        return True

    def api_delete_images(self):
        """Delete one image ('name') or several ('names'), in any folder."""
        body = self._body()
        raw = body.get("names")
        if not isinstance(raw, list):
            raw = [body.get("name", "")]
        rels = [r for r in (_safe_rel(str(v)) for v in raw[:200]) if r]
        if not rels:
            self._json({"error": "not found"}, 404)
            return
        agent = self.agent
        removed, failed = [], False
        for rel in rels:
            f = out_file(rel)
            if f is None:
                failed = True
                continue
            try:
                f.unlink()
            except OSError:
                failed = True
                continue
            t = _out_root() / ".thumbs" / (f.stem + ".jpg")
            try:
                if t.exists():
                    t.unlink()
            except OSError:
                pass
            removed.append(rel)
        if removed:
            reindex_timeline(agent, {}, set(removed))
        if not removed:
            self._json({"error": "not found"}, 404)
            return
        self._json({"ok": True, "removed": len(removed), "partial": failed})

    def api_gallery_folder(self):
        """POST {action}: create | rename | delete | move_images | move |
        organize."""
        body = self._body()
        action = str(body.get("action", ""))
        agent = self.agent

        # `folder` (and `parent`) may carry a `~<uid>/` prefix, which points
        # the whole request at another user's gallery. Admin-only; set once so
        # every branch below inherits it.
        for key in ("folder", "parent"):
            raw = str(body.get(key) or "")
            if raw.startswith("~"):
                as_uid, tail = split_scope(raw)
                if as_uid and scope_as(self._uid(), as_uid, self._is_admin()):
                    body[key] = tail

        if action == "move_images":
            if not self._folder_lock():
                return
            try:
                dest = _safe_rel(str(body.get("folder", "")) or "")
                if dest is None:
                    self._json({"error": "bad folder"}, 400)
                    return
                dest_dir = out_dir(dest)
                if dest_dir is None:
                    self._json({"error": "bad folder"}, 400)
                    return
                if dest and not dest_dir.is_dir():
                    self._json({"error": "no such folder"}, 404)
                    return
                rels = [r for r in
                        (_safe_rel(str(v)) for v in (body.get("rels") or [])[:200])
                        if r]
                if not rels:
                    self._json({"error": "nothing to move"}, 400)
                    return
                moved, failed = move_images(dest, rels)
                mapping = {old: new for old, new in moved}
                reindex_timeline(agent, mapping, set())
                self._json({"ok": True,
                            "moved": [{"from": o, "to": n} for o, n in moved],
                            "failed": failed})
            finally:
                agent.lock.release()
            return

        if action == "organize":
            """Bulk file-by-prompt: create outputs/<parent>/<keyword> and move
            every image sitting in <parent> whose POSITIVE prompt contains
            the keyword. Plain substring matching, no model involved."""
            parent = _safe_rel(str(body.get("folder", "")) or "")
            if parent is None:
                self._json({"error": "bad folder"}, 400)
                return
            base = out_dir(parent)
            if base is None or not base.is_dir():
                self._json({"error": "no such folder"}, 404)
                return
            keyword = str(body.get("keyword", ""))
            needle = keyword.strip().casefold()
            name = clean_folder_name(keyword)
            if not needle or not name:
                self._json({"error": "enter a keyword"}, 400)
                return
            if len(parent.split("/")) + 1 > MAX_FOLDER_DEPTH:
                self._json({"error": "folders are nested too deep"}, 400)
                return
            target_rel = join_rel(parent, name)
            if not self._folder_lock():
                return
            try:
                out_dir(target_rel).mkdir(parents=True, exist_ok=True)
                found = images_matching(parent, needle, agent.timeline)
                moved, failed = move_images(target_rel, found["rels"])
                reindex_timeline(agent, {o: n for o, n in moved}, set())
            except OSError as e:
                self._json({"error": str(e)}, 500)
                return
            finally:
                agent.lock.release()
            self._json({"ok": True, "folder": target_rel,
                        "moved": len(moved), "checked": found["checked"],
                        "nometa": found["nometa"], "failed": failed})
            return

        if action == "create":
            name = clean_folder_name(body.get("name", ""))
            if not name:
                self._json({"error": "bad folder name"}, 400)
                return
            parent = _safe_rel(str(body.get("parent", "")) or "")
            if parent is None:
                self._json({"error": "bad folder"}, 400)
                return
            base = out_dir(parent)
            if base is None or not base.is_dir():
                self._json({"error": "no such folder"}, 404)
                return
            if len(parent.split("/")) + 1 > MAX_FOLDER_DEPTH:
                self._json({"error": "folders are nested too deep"}, 400)
                return
            target = base / name
            if target.exists():
                self._json({"error": "that folder already exists"}, 409)
                return
            if not self._folder_lock():
                return
            try:
                target.mkdir(parents=True)
            except OSError as e:
                self._json({"error": str(e)}, 500)
                return
            finally:
                agent.lock.release()
            self._json({"ok": True, "folder": rel_of(target)})
            return

        if action in ("rename", "move"):
            rel = _safe_rel(str(body.get("folder", "")) or "")
            if not rel:
                self._json({"error": "bad folder"}, 400)
                return
            src = out_dir(rel)
            if src is None or not src.is_dir():
                self._json({"error": "no such folder"}, 404)
                return
            up = rel.rsplit("/", 1)[0] if "/" in rel else ""
            if action == "rename":
                name = clean_folder_name(body.get("name", ""))
                if not name:
                    self._json({"error": "bad folder name"}, 400)
                    return
                dest_rel = join_rel(up, name)
            else:
                parent = _safe_rel(str(body.get("parent", "")) or "")
                if parent is None:
                    self._json({"error": "bad folder"}, 400)
                    return
                # never into itself or one of its own descendants
                if parent and (parent == rel
                               or parent.startswith(rel + "/")):
                    self._json({"error": "a folder cannot live inside "
                                         "itself"}, 400)
                    return
                if parent == up:
                    self._json({"error": "already there"}, 400)
                    return
                if parent:
                    pdir = out_dir(parent)
                    if pdir is None or not pdir.is_dir():
                        self._json({"error": "no such folder"}, 404)
                        return
                dest_rel = join_rel(parent, src.name)
                if len(dest_rel.split("/")) > MAX_FOLDER_DEPTH:
                    self._json({"error": "folders are nested too deep"}, 400)
                    return
            dest = out_dir(dest_rel)
            if dest is None:
                self._json({"error": "bad folder"}, 400)
                return
            if dest.exists():
                self._json({"error": "a folder with that name exists"}, 409)
                return
            if not self._folder_lock():
                return
            try:
                if action == "rename":              # re-root every file below
                    moved = []
                    for p in sorted(src.rglob("*")):
                        if not p.is_file() or p.name.startswith("."):
                            continue
                        rel_old = rel_of(p)
                        rel_new = dest_rel + rel_old[len(rel):]
                        moved.append((rel_old, rel_new))
                    shutil.move(str(src), str(dest))
                    reindex_timeline(agent,
                                     {o: n for o, n in moved}, set())
                else:
                    shutil.move(str(src), str(dest))
            except OSError as e:
                self._json({"error": str(e)}, 500)
                return
            finally:
                agent.lock.release()
            self._json({"ok": True, "folder": dest_rel})
            return

        if action == "delete":
            rel = _safe_rel(str(body.get("folder", "")) or "")
            if not rel:
                self._json({"error": "the gallery root can't be deleted"},
                           400)
                return
            folder = out_dir(rel)
            if folder is None or not folder.is_dir():
                self._json({"error": "no such folder"}, 404)
                return
            gone = set()
            count = 0
            try:
                for p in folder.rglob("*"):
                    if p.is_file() and not p.name.startswith("."):
                        gone.add(rel_of(p))
                        count += 1
            except OSError:
                pass
            if not self._folder_lock():
                return
            try:
                shutil.rmtree(folder)
            except OSError as e:
                self._json({"error": str(e)}, 500)
                return
            finally:
                agent.lock.release()
            reindex_timeline(agent, {}, gone)
            self._json({"ok": True, "removed": count})
            return

        self._json({"error": "unknown action"}, 400)

    def _switch_sd_url(self, url: str):
        """Check a new image-server address *before* it is saved and live.

        Returns (True, loaded_checkpoint) when the switch is good, or
        (False, reason) when it is not — in which case nothing is written to
        the config and the current, working client keeps serving the app.

        This is the fix for "switching the image server breaks the app": the URL
        used to be saved on faith, so a typo (or a WebUI that was not started
        with --api --listen) left the agent pointed at a dead server with no
        way back from the UI — the only recovery was editing config.json on the
        server and restarting the unit.
        """
        agent = self.agent
        if not agent.lock.acquire(blocking=False):
            return False, ("a turn is running — stop it before switching the "
                           "image server")
        try:
            try:
                probe = SDClient(base_url=url)
                ok, detail = probe.probe(timeout=SD_PROBE_TIMEOUT)
            except Exception as e:                       # noqa: BLE001
                return False, f"{type(e).__name__}: {e}"[:200]
            if not ok:
                return False, f"{url} did not answer: {detail}"
            # only now is the address trusted: swap the client, which drops the
            # old server's cached model/capability lists on the way
            cfg = dict(agent.cfg)
            cfg["sd_url"] = url
            agent.apply_config(cfg)
            return True, detail
        finally:
            agent.lock.release()


    def api_admin_users(self):
        """GET the account list, POST an account action.

        Admin-only. The operations themselves are the same functions the
        `--add-user` / `--passwd` / `--disable-user` CLI calls, so the UI and
        the command line cannot drift apart."""
        if not self._is_admin():
            self._json({"error": "admins only"}, 403)
            return
        self.app._sync_disk()
        if self.command == "GET":
            self._json({"users": [user_row(u) for u in self.app.users],
                        "me": self._uid()})
            return
        body = self._body()
        action = str(body.get("action") or "")
        uid = str(body.get("id") or "")
        try:
            if action == "create":
                u = create_user(str(body.get("name") or ""),
                                str(body.get("password") or ""),
                                str(body.get("role") or ROLE_USER),
                                actor=self._user(),
                                api_key=str(body.get("api_key") or ""),
                                sd_url=str(body.get("sd_url") or ""))
                result = {"id": u["id"]}
            elif action == "passwd":
                u = set_user_password(uid, str(body.get("password") or ""),
                                      actor=self._user())
                result = {"id": u["id"]}
            elif action in ("disable", "enable"):
                u = set_user_disabled(uid, action == "disable",
                                       actor=self._user())
                result = {"id": u["id"]}
            elif action == "delete":
                # An admin deleting themselves locks the admin UI and the UI
                # is the fast path — the CLI still works, but make them mean it.
                if uid == self._uid():
                    raise ValueError("you cannot delete your own account here")
                u = delete_user(uid, actor=self._user())
                result = {"id": u["id"]}
            else:
                self._json({"error": f"unknown action {action!r}"}, 400)
                return
        except ValueError as e:
            self._json({"error": str(e)}, 400)
            return
        self.app._sync_disk()
        self._json({"ok": True, **result,
                    "users": [user_row(x) for x in self.app.users],
                    "me": self._uid()})

    def api_audit(self):
        """GET the audit trail. Admin only — it names other people's accounts.

        `?admin_only=1` hides ordinary use so privilege events stand out
        without filtering."""
        if not self._is_admin():
            self._json({"error": "admins only"}, 403)
            return
        try:
            limit = max(1, min(1000, int(
                (parse_qs(urlparse(self.path).query).get("limit") or [200])[0])))
        except (TypeError, ValueError):
            limit = 200
        only = (parse_qs(urlparse(self.path).query).get("admin_only")
                or [""])[0] in ("1", "true", "yes")
        self._json({"events": read_audit(limit=limit, admin_only=only),
                    "file": AUDIT_FILE.name})

    def api_settings(self):
        """GET returns masked settings; POST applies and persists them."""
        agent = self.agent
        if self.command == "GET":
            cfg = agent.cfg
            key = cfg["openrouter_key"]
            masked = (key[:7] + "…" + key[-4:]) if len(key) > 14 \
                else ("set" if key else "")
            override = cfg.get("system_prompt") or ""
            guide = cfg.get("model_guide") or ""
            # from the snapshot, not from a live probe: this is the request the
            # Settings page waits on before it will paint anything at all
            sd = self.sd_state.snapshot()
            self._json({"sd_url": cfg["sd_url"], "llm": cfg["llm_models"],
                        "key_masked": masked, "has_key": bool(key),
                        "system_prompt": override or DEFAULT_BASE_PROMPT,
                        "system_prompt_custom": bool(override),
                        "model_guide": guide or default_guide_text(),
                        "model_guide_custom": bool(guide),
                        "username": cfg.get("username", ""),
                        "sd_components": cfg.get("sd_components") or {},
                        "current_arch": sd["current_arch"],
                        "sd_ok": sd["sd_ok"], "sd_error": sd["error"],
                        "sd_pending": sd["pending"],
                        "capabilities": sd["caps"]})
            return
        body = self._body()
        cfg = dict(agent.cfg)
        sys_touched = False
        guide_touched = False
        switched = None
        shared_note = ""
        if "sd_url" in body:
            url = str(body["sd_url"]).strip().rstrip("/")
            if url and not url.startswith(("http://", "https://")):
                url = "http://" + url
            if not re.match(r"^https?://[\w.\-]+(:\d+)?(/[\w./\-]*)?$", url):
                self._json({"error": "invalid URL"}, 400)
                return
            # Probe-before-trust proves a Forge answered; it says nothing
            # about whether the address was safe to fetch. This is a
            # publicly reachable box, and the fetch is on the user's behalf,
            # so check first — before the connection is ever made.
            ok_url, why = validate_forge_url(url, self._is_admin())
            if not ok_url:
                self._json({"error": why}, 400)
                return
            if url != agent.client.base_url:
                others = self.app.forge_others_on(url, self._uid())
                switched = self._switch_sd_url(url)
                if not switched[0]:                  # refused — keep the old
                    self._json({"error": switched[1]}, 502)
                    return
                if others:
                    # Not an error: two people on one box is legitimate. But
                    # it serialises their generations behind a single global
                    # checkpoint dial, so say so rather than let them find out
                    # from a mysteriously wrong image.
                    who = ", ".join(others)
                    shared_note = (
                        f"{who} {'already uses' if len(others) == 1 else 'already use'} "
                        "this image server. Generations serialise, and each of "
                        "you pays a model reload whenever you ask for a "
                        "different checkpoint.")
            audit(self._user(), "settings.sd_url", self._uid(), url)
            cfg["sd_url"] = url
            if switched:
                # persist the switch on its own: a later field in this same
                # body could still be rejected, and the file must never
                # disagree with the client that is already live
                save_config(cfg, self._uid())
        if "llm" in body:
            models = [m.strip() for m in str(body["llm"]).split(",")
                      if m.strip()]
            if not models:
                self._json({"error": "no models given"}, 400)
                return
            cfg["llm_models"] = models
        if "openrouter_key" in body:
            key = str(body["openrouter_key"]).strip()
            if key and not key.startswith("sk-or-"):
                self._json({"error": "that doesn't look like an OpenRouter "
                                     "key (should start with sk-or-)"}, 400)
                return
            audit(self._user(), "settings.key", self._uid(),
                  "set" if key else "cleared")
            cfg["openrouter_key"] = key
        if body.get("system_prompt_reset"):
            cfg["system_prompt"] = ""          # back to the built-in default
            sys_touched = True
        elif "system_prompt" in body:
            text = str(body["system_prompt"]).strip()
            if len(text) > 32000:
                self._json({"error": "system message too long "
                                     "(32000 characters max)"}, 400)
                return
            audit(self._user(), "system_prompt.set", self._uid(),
                  f"{len(text)} chars")
            cfg["system_prompt"] = text        # empty = default
            sys_touched = True
        if body.get("model_guide_reset"):
            cfg["model_guide"] = ""            # back to model_guide.txt
            guide_touched = True
        elif "model_guide" in body:
            text = str(body["model_guide"]).strip()
            # twice the system message's cap: the stock guide is already 19k
            if len(text) > 64000:
                self._json({"error": "model guide too long "
                                     "(64000 characters max)"}, 400)
                return
            cfg["model_guide"] = text          # empty = model_guide.txt
            guide_touched = True
        if "username" in body:
            cfg["username"] = str(body["username"]).strip()[:60]
        if "sd_components" in body:
            comps = body["sd_components"]
            if not isinstance(comps, dict):
                self._json({"error": "sd_components must be an object"}, 400)
                return
            clean: dict = {}
            for arch, vals in comps.items():
                if not isinstance(vals, dict):
                    continue
                entry = {k: str(vals[k]).strip()[:120]
                         for k in _COMPONENT_KEYS if vals.get(k)}
                if entry:
                    clean[str(arch).strip()[:16].lower()] = entry
            cfg["sd_components"] = clean
        save_config(cfg, self._uid())
        if switched is not None:
            # already applied live in _switch_sd_url; apply_config below is a
            # no-op for the client and keeps the system message in step
            out = {"ok": True, "sd_switched": True, "sd_url": cfg["sd_url"],
                   "sd_model": switched[1]}
            if shared_note:
                out["note"] = shared_note
        else:
            out = {"ok": True}
        agent.apply_config(cfg)
        if sys_touched:
            out.update({"system_prompt": cfg["system_prompt"]
                        or DEFAULT_BASE_PROMPT,
                        "system_prompt_custom": bool(cfg["system_prompt"])})
        if guide_touched:
            out.update({"model_guide": cfg["model_guide"]
                        or default_guide_text(),
                        "model_guide_custom": bool(cfg["model_guide"])})
        self._json(out)

    def api_personas_list(self):
        self._json({"personas": load_personas(self._uid()),
                    "active": self.agent.cfg.get("persona_id", "")})

    def api_personas_save(self):
        """Create (no id) or update (with id) a user persona."""
        body = self._body()
        name = str(body.get("name", "")).strip()
        if not name:
            self._json({"error": "name required"}, 400)
            return
        lst = load_personas(self._uid())
        pid = str(body.get("id", "")).strip()
        existing = next((p for p in lst if p.get("id") == pid), None) \
            if pid else None
        if not existing:
            pid = slugify(name)
            while any(p.get("id") == pid for p in lst):
                pid = pid[:35] + "-" + secrets.token_hex(2)
        p = dict(existing) if existing else {"id": pid}
        p["name"] = name[:80]
        p["description"] = str(body.get("description", "")).strip()[:4000]
        # Optional. Personas written before this field keep working: an empty
        # appearance simply means nothing extra is auto-injected.
        ap = str(body.get("appearance", existing.get("appearance", "")
                          if existing else "")).strip()[:4000]
        if ap:
            p["appearance"] = ap
        else:
            p.pop("appearance", None)
        if existing:
            lst = [p if x.get("id") == pid else x for x in lst]
        else:
            lst.append(p)
        save_personas(self._uid(), lst)
        agent = self.agent
        if agent.cfg.get("persona_id") == pid:
            # live persona edit — rebuild the running system message
            agent.apply_config(dict(agent.cfg))
        self._json({"ok": True, "id": pid, "personas": lst,
                    "active": agent.cfg.get("persona_id", "")})

    def api_persona_select(self):
        body = self._body()
        pid = str(body.get("id", "")).strip()
        if pid and not any(p.get("id") == pid
                            for p in load_personas(self._uid())):
            self._json({"error": "persona not found"}, 404)
            return
        agent = self.agent
        cfg = dict(agent.cfg)
        cfg["persona_id"] = pid
        save_config(cfg, self._uid())
        agent.apply_config(cfg)
        self._json({"ok": True, "active": pid})

    def api_persona_delete(self):
        body = self._body()
        pid = str(body.get("id", "")).strip()
        lst = load_personas(self._uid())
        if not any(p.get("id") == pid for p in lst):
            self._json({"error": "persona not found"}, 404)
            return
        lst = [p for p in lst if p.get("id") != pid]
        save_personas(self._uid(), lst)
        agent = self.agent
        if agent.cfg.get("persona_id") == pid:
            cfg = dict(agent.cfg)
            cfg["persona_id"] = ""
            save_config(cfg, self._uid())
            agent.apply_config(cfg)
        self._json({"ok": True, "personas": lst,
                    "active": agent.cfg.get("persona_id", "")})

    def api_scenario_save(self):
        """Create (no id) or update (with id) a scenario on a character."""
        body = self._body()
        cid = str(body.get("char_id", "")).strip()
        cards = load_characters()
        card = next((c for c in cards if c.get("id") == cid), None)
        if not card:
            self._json({"error": "character not found"}, 404)
            return
        if not can_edit_card(self._uid(), card, self._is_admin()):
            audit(self._user(), "scenario.other", card_owner(card),
                  f"tried {cid}")
            self._json({"error": "that character belongs to someone else"},
                       403)
            return

        name = str(body.get("name", "")).strip()
        if not name:
            self._json({"error": "name required"}, 400)
            return
        scenarios = list(card.get("scenarios") or [])
        sid = str(body.get("id", "")).strip()
        existing = next((s for s in scenarios if s.get("id") == sid), None) \
            if sid else None
        if not existing:
            sid = slugify(name)[:30] or "scenario"
            while any(s.get("id") == sid for s in scenarios):
                sid = sid[:28] + "-" + secrets.token_hex(2)
        s = dict(existing) if existing else {"id": sid}
        s["name"] = name[:80]
        s["description"] = str(body.get("description", "")).strip()[:4000]
        s["first_message"] = str(body.get("first_message", "")).strip()[:4000]
        if existing:
            scenarios = [s if x.get("id") == sid else x for x in scenarios]
        else:
            scenarios.append(s)
        card["scenarios"] = scenarios
        cards = [card if c.get("id") == cid else c for c in cards]
        save_characters(cards)
        cover = str(body.get("cover", "") or "")
        if cover:
            write_cover(f"sc-{cid}.{sid}", cover)
        if body.get("cover_remove"):
            remove_cover(f"sc-{cid}.{sid}")
        agent = self.agent
        if agent.char and agent.char["id"] == cid:
            agent.char = card
            if agent.messages and \
                    agent.messages[0].get("role") == "system":
                agent.messages[0] = agent._sys_msg()
        self._json({"ok": True, "id": sid,
                    "scenarios": [self._scenario_out(cid, x)
                                  for x in scenarios]})

    def api_scenario_delete(self):
        body = self._body()
        cid = str(body.get("char_id", "")).strip()
        sid = str(body.get("id", "")).strip()
        cards = load_characters()
        card = next((c for c in cards if c.get("id") == cid), None)
        if not card:
            self._json({"error": "character not found"}, 404)
            return
        if not can_edit_card(self._uid(), card, self._is_admin()):
            self._json({"error": "that character belongs to someone else"},
                       403)
            return

        scenarios = list(card.get("scenarios") or [])
        if not any(s.get("id") == sid for s in scenarios):
            self._json({"error": "scenario not found"}, 404)
            return
        card["scenarios"] = [s for s in scenarios if s.get("id") != sid]
        cards = [card if c.get("id") == cid else c for c in cards]
        save_characters(cards)
        remove_cover(f"sc-{cid}.{sid}")
        agent = self.agent
        if agent.char and agent.char["id"] == cid:
            agent.char = card
            if agent.scenario_id == sid:
                agent.scenario_id = ""
                if agent.messages and \
                        agent.messages[0].get("role") == "system":
                    agent.messages[0] = agent._sys_msg()
        self._json({"ok": True, "scenarios": card["scenarios"]})

    def api_chat_scenario(self):
        """Start a new chat for the active character bound to a scenario.
        The scenario's first message (if any) seeds the chat."""
        body = self._body()
        sid = str(body.get("scenario_id", "")).strip()
        agent = self.agent
        if not agent.char:
            self._json({"error": "select a character first"}, 400)
            return
        if sid and not next((s for s in (agent.char.get("scenarios") or [])
                             if s.get("id") == sid), None):
            self._json({"error": "scenario not found"}, 404)
            return
        if not agent.lock.acquire(blocking=False):
            self._json({"error": "busy — a turn is already running"}, 409)
            return
        try:
            agent.new_chat(scenario_id=sid)
        finally:
            agent.lock.release()
        self._json({"ok": True, "timeline": agent.timeline})

    def api_char_generate(self):
        """Character form's AI assistant, as a conversation.

        Each call is a PATCH over the live card: the client sends the form's
        current values (which include the user's hand edits) plus the
        conversation so far, and the model returns only the fields it wants
        to change. Keys it omits are left alone by the client.

        That omission-is-the-default contract is the reason this is not the
        one-shot filler it used to be: assigning `d.persona || ""` into the
        form meant a reply that skipped a key silently emptied that field,
        and a "make the greeting shorter" turn had no way to exist.
        """
        body = self._body()          # read first — never leak the body when
        agent = self.agent       # an early error returns on keep-alive
        if not agent.has_key():
            self._json({"error": "no_api_key"}, 400)
            return
        prompt = str(body.get("prompt", "")).strip()
        if not prompt:
            self._json({"error": "say what you want first"}, 400)
            return

        # The live card, which the model must revise rather than replace.
        cur = body.get("current") or {}
        state = "\n".join(
            f"{k}: {(str(cur.get(k) or '').strip() or '(empty)')}"
            for k in ("name", "appearance", "persona", "greeting"))
        system = (CHAR_MAKER_PROMPT + "\n\nCURRENT CARD STATE (live; includes "
                  "the user's own edits, which you must preserve unless you "
                  "are asked to change that field):\n" + state)

        msgs = [{"role": "system", "content": system}]
        for h in (body.get("history") or [])[-10:]:
            if not isinstance(h, dict):
                continue
            role = "assistant" if h.get("role") == "assistant" else "user"
            text = str(h.get("content") or "").strip()[:4000]
            if text:
                msgs.append({"role": role, "content": text})
        msgs.append({"role": "user", "content": prompt[:4000]})

        try:
            data = agent.llm_complete(msgs, temperature=0.9, max_tokens=2000)
        except LLMError as e:
            self._json({"error": scrub_paths(str(e))}, 502)
            return
        content = (data["choices"][0].get("message") or {}).get("content") \
            or ""
        s, e = content.find("{"), content.rfind("}")
        d = {}
        if s >= 0 and e > s:
            try:
                parsed = json.loads(content[s:e + 1])
                if isinstance(parsed, dict):
                    d = parsed
            except json.JSONDecodeError:
                pass

        out = {"reply": scrub_paths(str(d.get("reply", "")).strip())[:600]}
        if d.get("revert") is True:
            out["revert"] = True
        caps = (("name", 60), ("appearance", 4000), ("persona", 8000),
                ("greeting", 2000))
        for key, cap in caps:
            val = scrub_paths(str(d.get(key, "")).strip())[:cap]
            if not val:
                continue               # omitted or empty: leave the field be
            if key == "appearance":
                # The model describes the opening shot in the greeting and
                # lifts pieces of it into `appearance`, where ensure_tags
                # re-injects them into every image for the life of the card.
                val, dropped = strip_scene_tags(val)
                if dropped:
                    out["dropped_tags"] = dropped
                if not val:
                    continue
            out[key] = val
        if not out.get("revert") and not any(k in out for k, _ in caps) \
                and not out.get("reply"):
            # Nothing usable came back at all. A reply on its own is NOT
            # this error: the prompt asks for one when the request is a
            # question ("what is his name again?"), and the client shows it
            # without touching a field.
            self._json({"error": "the model didn't return anything — "
                                 "try again"}, 502)
            return
        self._json(out)

    def api_image_generate(self):
        """One-off SD generation for the character form / scenario page:
        the avatar (square, cropped client-side style) or an opening cover
        image. Saves into outputs/ (so it shows in the gallery + lightbox)
        and returns a data URL the form holds until Save."""
        agent = self.agent
        body = self._body()          # read first — never leak the body when
        if not agent.lock.acquire(blocking=False):   # the busy 409 returns
            self._json({"error": "busy — a turn is already running"}, 409)
            return
        try:
            purpose = str(body.get("purpose", ""))
            prompt = str(body.get("prompt", "")).strip()
            if not prompt:
                self._json({"error": "prompt required"}, 400)
                return
            appearance = str(body.get("appearance", "")).strip()[:4000]
            if appearance:
                prompt = ensure_tags(prompt, appearance)
            card = None
            cid = str(body.get("char_id", "")).strip()
            if cid:
                card = next((c for c in visible_characters(self._uid(), self._is_admin())
                             if c.get("id") == cid), None)
            args: dict = {"prompt": prompt,
                          "negative_prompt":
                              str(body.get("negative_prompt", "")).strip()}
            if purpose == "avatar":
                args["width"] = args["height"] = 1024   # square to crop from
            else:
                size: list = []
                m = re.match(r"^(\d{3,4})x(\d{3,4})$",
                             str(body.get("size", "")).strip())
                if m:
                    size = [int(m.group(1)), int(m.group(2))]
                elif isinstance(card, dict) \
                        and isinstance(card.get("size"), list) \
                        and len(card["size"]) == 2:
                    size = card["size"]
                if len(size) == 2:
                    args["width"], args["height"] = size
            cp = (card or {}).get("checkpoint", "")
            cp = str(cp).strip() if cp else ""
            if cp:
                args["model"] = cp
                arch = agent.client.arch_for(cp)
                args.update(agent.saved_components(arch))
            else:
                arch = agent.client.current_arch()
            try:
                result = execute_tool(agent.client, "generate_image", args,
                                      user_out_dir(self._uid()))
            except Exception as e:                     # noqa: BLE001 — report
                self._json({"error": scrub_paths(
                    f"{type(e).__name__}: {e}")}, 502)
                return
            if arch and result.get("gen"):
                result["gen"]["arch"] = arch
            files = [rel_of(Path(f)) for f in result.get("saved_files", [])]
            if not files:
                self._json({"error": "generation produced no image"}, 502)
                return
            name = files[0]
            path = out_file(name)
            if not path:
                self._json({"error": "image vanished"}, 500)
                return
            out: dict = {"ok": True, "name": name, "url": "/outputs/" + name}
            try:
                from io import BytesIO
                from PIL import Image
                if purpose == "avatar":
                    with Image.open(path) as im:
                        side = min(im.size)
                        sq = im.convert("RGB").crop((
                            (im.width - side) // 2, (im.height - side) // 2,
                            (im.width + side) // 2, (im.height + side) // 2))
                        sq.thumbnail((256, 256))
                        buf = BytesIO()
                        sq.save(buf, "JPEG", quality=86)
                    out["avatar"] = ("data:image/jpeg;base64,"
                                     + base64.b64encode(
                                         buf.getvalue()).decode())
                else:
                    out["cover"] = ("data:image/png;base64,"
                                    + base64.b64encode(
                                        path.read_bytes()).decode())
            except Exception:                          # noqa: BLE001
                pass                                   # URL-only fallback
            self._json(out)
        finally:
            agent.lock.release()

    def api_model(self):
        """Load a checkpoint, optionally with its companion components.

        A modular DiT needs a text encoder and a VAE attached to the right
        architecture slot, so those travel with the switch instead of being a
        separate settings trip. Everything goes out as one options write.
        """
        body = self._body()
        title = str(body.get("model", "")).strip()
        if not title:
            self._json({"error": "no model given"}, 400)
            return
        agent = self.agent
        if not agent.lock.acquire(blocking=False):
            self._json({"error": "busy — a turn is already running"}, 409)
            return
        try:
            arch = str(body.get("arch") or "").strip() \
                or agent.client.arch_for(title)
            components = {k: str(body.get(k) or "").strip()
                          for k in _COMPONENT_KEYS}
            with forge_lock(agent.client.base_url):
                info = agent.client.set_model(
                    title, arch=arch,
                    **{k: v for k, v in components.items() if v})
            self._json({"ok": True,
                        "current_model": info.get("model") or title,
                        "arch": info.get("arch", arch),
                        "modules": info.get("modules") or [],
                        "low_bits": info.get("low_bits") or "",
                        "profile": _public_profile(agent.client, arch)})
        except SDWebUIError as e:
            self._json({"error": str(e)}, 502)
        except Exception as e:                        # noqa: BLE001
            self._json({"error": f"{type(e).__name__}: {e}"}, 502)
        finally:
            agent.lock.release()

    def api_model_components(self):
        """Attach/clear the companion files and precision of one architecture.

        This is the Settings > Image hook. It writes options rather than a
        per-request override: on Neo an override reloads the model inside the
        request, and a reload that does not fit reports "Failed to load model".
        """
        body = self._body()
        agent = self.agent
        if not agent.lock.acquire(blocking=False):
            self._json({"error": "busy — a turn is already running"}, 409)
            return
        try:
            arch = str(body.get("arch") or "").strip() \
                or agent.client.current_arch()
            # an empty value means "clear this slot" and is sent on purpose;
            # a key the client left out is simply not passed
            components = {k: str(body[k]).strip()
                          for k in _COMPONENT_KEYS if k in body}
            with forge_lock(agent.client.base_url):
                info = agent.client.configure(arch, **components)
            self.sd_state.invalidate()
            self._json({"ok": True, **info,
                        "profile": _public_profile(agent.client, arch)})
        except SDWebUIError as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:                        # noqa: BLE001
            self._json({"error": f"{type(e).__name__}: {e}"}, 502)
        finally:
            agent.lock.release()


class App:
    """Process-wide state handed to the request handler."""

    def __init__(self, password: str):
        self.password = password
        self.session_secret = load_session_secret()
        # The store is process-wide and mutated by every login/logout, so it
        # needs its own lock. Phases 1-2 add per-user agents to the same
        # pattern; do not assume the GIL makes bare dict writes safe here.
        self.sessions_lock = threading.Lock()
        self.users = load_users()
        self.sessions = prune_sessions(load_sessions())
        self._stamps = {}
        self._sync_disk()
        adopt_legacy_state(self.owner_uid())  # Phase 1, once
        migrate_flat_chats(self.owner_uid())  # Phase A → B chat layout
        # ensure_assistant *writes* (it appends the assistant card). It used
        # to run in Agent.__init__, which is a write on a construction path
        # now that there is one Agent per user. Startup is the only correct
        # place for it.
        ensure_assistant(load_characters(), self.owner_uid())
        # One Agent per user, built lazily. Deliberately no `self.agent`
        # alias: an attribute that looks process-wide but is really the
        # owner's is exactly the bug Phase 2 is removing.
        self.agents_lock = threading.Lock()
        self.agents: dict[str, Agent] = {}

    def _sync_disk(self) -> None:
        """Re-read users.json / sessions.json when they changed on disk.

        The admin CLI (--add-user, --passwd, --disable-user) edits these files
        directly, and it is expected to run against a *live* server. Without
        this the process keeps honouring a revoked session or a disabled
        account from the copy it loaded at boot.

        One stat per file per request; the parse only happens when the stamp
        actually moved, so the common case stays cheap.
        """
        for path, attr in ((USERS_FILE, "users"), (SESSIONS_FILE, "sessions")):
            try:
                st = path.stat()
                stamp = (st.st_mtime_ns, st.st_size)
            except OSError:
                stamp = None
            if self._stamps.get(attr) == stamp:
                continue
            self._stamps[attr] = stamp
            if attr == "users":
                self.users = load_users()
            else:
                # Disk is authoritative, and deliberately so: new_session
                # flushes under the lock, so nothing minted in-process is
                # missing — while a session the CLI just revoked *is* here,
                # and merging memory back in would resurrect it.
                with self.sessions_lock:
                    self.sessions = prune_sessions(load_sessions())

    def user_for_token(self, token: str) -> dict:
        """Cookie token -> user record, or {} when expired/revoked/unknown."""
        if not token:
            return {}
        self._sync_disk()
        with self.sessions_lock:
            rec = self.sessions.get(_token_key(token))
            if not rec:
                return {}
            expires = int(rec.get("expires") or 0)
            if expires <= int(time.time()):
                self.sessions.pop(_token_key(token), None)
                save_sessions(self.sessions)
                return {}
        user = find_user(self.users, str(rec.get("user") or ""))
        # A disabled account loses access immediately, even if its cookie is
        # still inside the TTL — that is the whole point of disabling one.
        return {} if user.get("disabled") else user

    def is_admin(self, user: dict) -> bool:
        return bool(user) and user.get("role") == ROLE_ADMIN

    def owner_uid(self) -> str:
        """The user whose pre-multi-user state was adopted. The first
        admin, always."""
        admins = [u for u in self.users if self.is_admin(u)
                  and not u.get("disabled")]
        if admins:
            return str(admins[0].get("id") or "u001")
        return str(self.users[0].get("id")) if self.users else "u001"

    # ------------------------------------------------------- per-user agents
    #
    # An Agent is a conversation: messages, active character, active chat,
    # timeline, feedback, and a lock so one user cannot double-submit. All of
    # that is per-user state, which is why there used to be exactly one.
    #
    # Each Agent also holds its whole conversation in RAM and nothing trims it
    # (see MULTIUSER.md Phase 3), so agents are evicted when idle rather than
    # accumulated — otherwise N users on a box that also hosts Forge is how you
    # get OOM-killed mid-generation. Eviction saves state first, and the
    # Agent rehydrates from its own files on the next request.

    AGENT_IDLE_SECS = 30 * 60
    AGENT_MAX_RESIDENT = 8

    def agent_for(self, uid: str) -> Agent:
        """The Agent owning `uid`'s conversation, built on first use.

        The lock is held across the build because Agent.__init__ does disk
        I/O and calls ensure_assistant-adjacent loaders; two concurrent
        first requests for the same user must not both construct one."""
        with self.agents_lock:
            agent = self.agents.get(uid)
            if agent is not None:
                agent.last_used = time.time()
                return agent
            self._evict_agents_locked(room=1)
            agent = Agent(self.config_for(uid), uid=uid)
            agent.last_used = time.time()
            agent.sd_state = SDState(agent)  # so a URL switch can drop it
            self.agents[uid] = agent
            return agent

    def config_for(self, uid: str) -> dict:
        """This user's settings, from users/<uid>/config.json.

        `data/config.json` is the template a new account starts from, minus
        the key — BYOK means the OpenRouter key is the user's own, so nobody
        inherits someone else's."""
        return load_config(uid)

    def _evict_agents_locked(self, room: int = 0) -> int:
        """Drop idle agents, then enforce the residency cap. Caller holds
        `agents_lock`. `room` accounts for the agent about to be created.

        Never evict one mid-turn: dropping an Agent whose lock is held would
        strand the running turn and orphan its thread."""
        now = time.time()
        for uid, a in list(self.agents.items()):
            if a.lock.locked():
                continue
            if now - a.last_used >= self.AGENT_IDLE_SECS:
                self._drop_agent_locked(uid)
        cap = self.AGENT_MAX_RESIDENT
        # `room` agents are about to be added, so the cap applies to the
        # total after they land: evict while len + room would exceed it.
        # Dropping to MAX+room-1 is deliberate — one agent is about to leave.
        idle = sorted((a for a in self.agents.values()
                       if not a.lock.locked()),
                      key=lambda a: a.last_used)
        dropped = 0
        while len(self.agents) + room > cap and idle:
            self._drop_agent_locked(idle.pop(0).uid)
            dropped += 1
        return dropped

    def _drop_agent_locked(self, uid: str) -> None:
        agent = self.agents.pop(uid, None)
        if agent is None:
            return
        try:
            agent._save_state()          # the conversation only lives in RAM
        except Exception:                             # noqa: BLE001
            pass

    def evict_agents(self) -> int:
        """Public sweep. Normally unnecessary — `agent_for` evicts as it
        goes, which is the only moment the cap can bind."""
        with self.agents_lock:
            return self._evict_agents_locked()

    def sd_state_for(self, uid: str) -> SDState:
        return self.agent_for(uid).sd_state

    def forge_others_on(self, url: str, exclude_uid: str = "") -> list[str]:
        """Names of other enabled users already pointed at this Forge.

        Deliberately not an error — one box serving two people is legitimate.
        It just means they share a global checkpoint dial, so generations
        serialise and asking for a different checkpoint costs a reload. Forge's
        own queue keeps the GPU safe; `forge_lease` keeps the dial correct.
        See MULTIUSER.md, "A Forge is single-tenant"."""
        from sd_client import forge_key
        key = forge_key(url)
        if not key or key == "?":
            return []
        out = []
        for u in self.users:
            uid = str(u.get("id") or "")
            if u.get("disabled") or uid == exclude_uid:
                continue
            if forge_key(load_config(uid).get("sd_url") or "") == key:
                out.append(str(u.get("name") or uid))
        return out


# ------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description="Anima — standalone web app (chat + gallery + PWA)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("PORT", 8000)))
    # Account provisioning. Signup is closed by design (MULTIUSER.md #1), so
    # the admin needs a way in that does not involve editing users.json.
    ap.add_argument("--add-user", metavar="NAME",
                    help="create an account and prompt for its password")
    ap.add_argument("--with-key", metavar="KEY",
                    help="seed the new account's OpenRouter key")
    ap.add_argument("--with-sd-url", metavar="URL",
                    help="seed the new account's image server")
    ap.add_argument("--admin", action="store_true",
                    help="--add-user creates an admin, not a normal user")
    ap.add_argument("--passwd", metavar="NAME",
                    help="reset an existing account's password")
    ap.add_argument("--disable-user", metavar="NAME",
                    help="disable an account (keeps its data)")
    ap.add_argument("--enable-user", metavar="NAME")
    ap.add_argument("--delete-user", metavar="NAME",
                    help="delete an account and everything it owned")
    ap.add_argument("--list-users", action="store_true")
    args = ap.parse_args()

    if args.list_users:
        users = load_users()
        if not users:
            print("no accounts yet")
            return
        for u in users:
            state = "disabled" if u.get("disabled") else u.get("role", "")
            print(f"  {u.get('id'):>6}  {u.get('name'):<20} {state}")
        return

    if args.add_user or args.passwd:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            if args.add_user:
                pw = _prompt_pw(f"password for {args.add_user.strip()}: ")
                u = create_user(args.add_user, pw,
                                ROLE_ADMIN if args.admin else ROLE_USER,
                                api_key=args.with_key or "",
                                sd_url=args.with_sd_url or "")
                print(f"created {u['role']} {u['id']} ({u['name']})")
            else:
                pw = _prompt_pw("new password: ")
                u = set_user_password(args.passwd, pw)
                print(f"password updated for {u['id']}")
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        return

    if args.disable_user or args.enable_user or args.delete_user:
        name = args.disable_user or args.enable_user or args.delete_user
        try:
            if args.delete_user:
                u = delete_user(name)
                print(f"deleted {u.get('id')} ({u.get('name')}) and its data")
            else:
                dis = bool(args.disable_user)
                u = set_user_disabled(name, dis)
                print(f"{'disabled' if dis else 'enabled'} "
                      f"{u.get('id')} ({u.get('name')})")
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        return

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    password = os.environ.get("AGENT_PASSWORD", "")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # AGENT_PASSWORD is the bootstrap secret, not a gate. It is only read
    # when there is no users.json, to mint the first admin — a later boot
    # cannot mint an admin from the environment (bootstrap_admin returns {}
    # once any account exists).
    if not load_users():
        if not password:
            print("ERROR: no accounts yet — set AGENT_PASSWORD to create the "
                  "first admin.\n  e.g.  AGENT_PASSWORD=my-secret "
                  "python server.py", file=sys.stderr)
            sys.exit(1)
        admin = bootstrap_admin(password)
        print(f"  bootstrapped admin account {admin['name']!r} "
              f"(id {admin['id']}) — change it in Settings")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    srv.app = App(password)

    agent = srv.app.agent_for(srv.app.owner_uid())
    cfg = agent.cfg
    try:
        model = agent.client.current_model()
    except Exception:
        model = "(server unreachable)"

    w = 56
    print()
    print("  ✦ ANIMA — standalone web app")
    print("  " + "─" * w)
    live = [u for u in srv.app.users if not u.get("disabled")]
    print(f"  Accounts: {len(live)} "
          f"({sum(1 for u in live if srv.app.is_admin(u))} admin)")
    print(f"  Key     : {'configured' if agent.has_key() else 'NOT set — add in Settings'}")
    print(f"  LLM     : {', '.join(cfg['llm_models'])}")
    print(f"  SysMsg  : {'custom' if cfg.get('system_prompt') else 'default'}")
    guide = cfg.get("model_guide") or ""
    print(f"  Guide   : {'custom' if guide else 'model_guide.txt'}"
          f"  [{len(guide or default_guide_text())} chars]")
    print(f"  SD      : {cfg['sd_url']}  [{model}]")
    print(f"  Output  : {OUT_DIR}")
    for u in advertise_urls(args.port):
        print(f"  URL     : {u}")
    print("  Behind Caddy, serve as-is (HTTP); TLS terminates at the proxy.")
    print("  " + "─" * w + "\n")

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
