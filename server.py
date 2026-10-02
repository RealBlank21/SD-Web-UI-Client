"""
SD Agent — standalone, deployable web app.

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
import hashlib
import hmac
import json
import os
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
from urllib.parse import parse_qs, unquote, urlparse

import requests

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from sd_client import SDClient, SDWebUIError
from agent_core import (                      # vendored agent brain
    DEFAULT_BASE_PROMPT,
    DEFAULT_LLM,
    LLMError,
    MAX_TOOL_ROUNDS,
    SCENE_DIRECTOR_PROMPT,
    _clean_assistant_msg,
    _is_auth_error,
    effective_system_prompt,
    ensure_tags,
    execute_tool,
)

APP_DIR = ROOT / "webui"
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "outputs"
CONFIG_FILE = DATA_DIR / "config.json"
SESSION_FILE = DATA_DIR / "session_secret"
STATE_FILE = DATA_DIR / "chat_state.json"

STATIC_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".webmanifest": "application/manifest+json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
}
IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
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
    "sd_url": "http://100.93.220.68:7860",
    "system_prompt": "",   # custom system message; "" = built-in default
    "scene_director": True,  # auto-generate an image on new visual moments
    "username": "",          # how the AI knows the user
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            user = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                cfg.update({k: v for k, v in user.items() if k in cfg})
        except Exception:
            pass
    if isinstance(cfg["llm_models"], str):
        cfg["llm_models"] = [m.strip() for m in cfg["llm_models"].split(",")
                             if m.strip()]
    return cfg


def save_config(cfg: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(CONFIG_FILE)


def load_session_secret() -> bytes:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SESSION_FILE.exists():
        return SESSION_FILE.read_bytes()
    secret = secrets.token_bytes(32)
    SESSION_FILE.write_bytes(secret)
    return secret


# -------------------------------------------------------------- characters

CHARS_FILE = DATA_DIR / "characters.json"
CHATS_DIR = DATA_DIR / "chats"
AVATAR_DIR = DATA_DIR / "avatars"
CHAR_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
CHAT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{0,40}$")
DEFAULT_CHAT_FILE = CHATS_DIR / "default.json"


def new_chat_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def derive_title(timeline: list) -> str:
    for evt in timeline:
        if evt.get("type") == "user":
            t = (evt.get("text") or "").strip()
            if t:
                return t[:60]
            break
    return "New chat"


def load_characters() -> list:
    if not CHARS_FILE.exists():
        return []
    try:
        d = json.loads(CHARS_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def save_characters(chars: list) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CHARS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(chars, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(CHARS_FILE)


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:40] or "char"


def migrate_flat_chats() -> None:
    """Phase A → B: move single-file chats/<char>.json into per-chat files."""
    try:
        for f in CHATS_DIR.glob("*.json"):
            if f == DEFAULT_CHAT_FILE:
                continue                     # free chat stays single
            cid = f.stem
            if not CHAR_ID_RE.match(cid):
                continue
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            cid_dir = CHATS_DIR / cid
            cid_dir.mkdir(parents=True, exist_ok=True)
            chat_id = new_chat_id()
            payload = {
                "id": chat_id,
                "title": derive_title(d.get("timeline") or []),
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

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.client = SDClient(base_url=cfg["sd_url"])
        self.char = None                 # active character card or None
        self.chat_id = ""                # active chat id (bound chats)
        self._title = ""                 # active chat's title
        self._home = DEFAULT_CHAT_FILE   # where the active chat persists
        self.messages = [self._sys_msg()]
        self.timeline = []
        self.lock = threading.Lock()     # one turn at a time
        self.status_cb = None            # set while a turn streams
        self._load_state()
        self._rehydrate_char()

    # ------------------------------------------------------------ config

    def _sys_msg(self) -> dict:
        """Opening system message: global override (or default) + username
        + the active character's persona/appearance block."""
        base = effective_system_prompt(self.cfg.get("system_prompt"))
        user = (self.cfg.get("username") or "").strip()
        if user:
            base += f"\n\nThe user's name is {user[:60]}."
        c = self.char
        if c:
            parts = [f"\n\nYou are roleplaying as {c.get('name', '?')}."
                     f"\nPersonality: {c.get('persona', '').strip()}"]
            if c.get("greeting"):
                parts.append("Your greeting (the chat's first message) was: "
                             + c["greeting"].strip())
            parts.append("Your appearance — reflect these tags in every "
                         "image prompt that shows you:\n"
                         + c.get("appearance", "").strip())
            base += "\n".join(parts)
        return {"role": "system", "content": base}

    def char_tags(self) -> str:
        return (self.char or {}).get("appearance", "") or ""

    def apply_config(self, cfg: dict):
        """Hot-apply new settings (LLM chain / key / SD URL / system msg)."""
        self.cfg = cfg
        if self.client.base_url != cfg["sd_url"]:
            self.client = SDClient(base_url=cfg["sd_url"])
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

    # -------------------------------------------------------------- LLM

    def llm_complete(self, messages: list, temperature: float | None = None,
                     max_tokens: int | None = None) -> dict:
        """LLMRouter logic inline: try models in order, retry rate limits,
        stick with whichever model last worked."""
        models = self.llm_models
        order = models + models[:1] * 0            # copy
        last_err: LLMError | None = None
        for model in order:
            for attempt in range(2):               # one retry per model
                try:
                    self.emit_status(f"thinking · {model.split('/')[-1]}")
                    return chat_completion(self.cfg["openrouter_key"],
                                           model, messages,
                                           temperature=temperature,
                                           max_tokens=max_tokens)
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

    def _chat_file(self, cid: str, chat_id: str = "") -> Path:
        """Phase B: per-chat files under chats/<char>/<chat_id>.json.
        Without a chat id this resolves the legacy single-file location."""
        if chat_id:
            return CHATS_DIR / cid / f"{chat_id}.json"
        return CHATS_DIR / f"{cid}.json"

    def _char_dir(self, cid: str) -> Path:
        return CHATS_DIR / cid

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
        self._title = str(d.get("title", ""))
        msgs = d.get("messages")
        self.messages = [self._sys_msg()] + \
            (msgs if isinstance(msgs, list) else [])
        self.timeline = d.get("timeline") or []

    def _fresh_chat(self):
        self.messages = [self._sys_msg()]
        self.timeline = []
        self._title = ""
        self._seed_greeting()

    def _seed_greeting(self):
        g = (self.char or {}).get("greeting", "").strip()
        if g:
            self.messages.append({"role": "assistant", "content": g})
            self.timeline.append({"type": "reply", "text": g})

    def set_character(self, card: dict | None) -> None:
        """Switch to a character (their newest chat) or the free chat.
        Caller must hold the agent lock."""
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
            self._home = DEFAULT_CHAT_FILE
            self._apply_chat_payload(self._load_chat_payload(
                DEFAULT_CHAT_FILE))
        self._save_state()

    def bind_active_chat(self, card: dict) -> None:
        """Attach the current (unbound, non-empty) chat to a new character:
        it becomes that character's first chat file."""
        self.char = card
        self.chat_id = new_chat_id()
        self._home = self._chat_file(card["id"], self.chat_id)
        self._title = derive_title(self.timeline)
        self._save_state()

    def delete_character(self, cid: str) -> None:
        """Remove a character's chat directory. Caller must hold the agent
        lock when the character is active."""
        try:
            shutil.rmtree(self._char_dir(cid), ignore_errors=True)
        except OSError:
            pass

    def new_chat(self) -> None:
        """Start a fresh chat for the active character — the old one stays
        archived. The unbound free chat simply resets in place."""
        if not self.char:
            self.messages = [self._sys_msg()]
            self.timeline = []
            self._title = ""
            self._save_state()
            return
        self._save_state()
        self.chat_id = new_chat_id()
        self._home = self._chat_file(self.char["id"], self.chat_id)
        self._fresh_chat()
        self._save_state()

    def select_chat(self, cid: str, chat_id: str) -> None:
        if not (self.char and self.char["id"] == cid):
            raise ValueError("character is not active")
        path = self._chat_file(cid, chat_id)
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

    def _rehydrate_char(self):
        """Restore the active character (and their active chat) at startup."""
        cid = getattr(self, "_state_char_id", "")
        self._state_char_id = ""
        if not cid:
            return
        card = next((c for c in load_characters()
                     if c.get("id") == cid), None)
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
            self._home = DEFAULT_CHAT_FILE
            self.chat_id = ""

    # -------------------------------------------------------- persistence

    def _save_state(self):
        """Persist the active chat to its home file + the restart mirror."""
        try:
            now = int(time.time())
            mirror = {
                "character_id": self.char["id"] if self.char else "",
                "chat_id": self.chat_id if self.char else "",
                "messages": self.messages[1:],
                "timeline": self.timeline,
            }
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            if self.char:
                cid = self.char["id"]
                if CHAT_ID_RE.match(self.chat_id):
                    self._char_dir(cid).mkdir(parents=True, exist_ok=True)
                    if self._title in ("", "New chat"):
                        # re-derive until the first real user message lands
                        self._title = derive_title(self.timeline)[:60]
                    payload = {
                        "id": self.chat_id,
                        "character_id": cid,
                        "title": self._title,
                        "updated": now,
                        "messages": mirror["messages"],
                        "timeline": self.timeline,
                    }
                    self._home.write_text(
                        json.dumps(payload, ensure_ascii=False),
                        encoding="utf-8")
            else:
                payload = {"messages": mirror["messages"],
                           "timeline": self.timeline}
                CHATS_DIR.mkdir(parents=True, exist_ok=True)
                DEFAULT_CHAT_FILE.write_text(
                    json.dumps(payload, ensure_ascii=False),
                    encoding="utf-8")
            STATE_FILE.write_text(
                json.dumps(mirror, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _load_state(self):
        """Startup: restore the active chat from the restart mirror."""
        self.chat_id = ""
        self._title = ""
        if not STATE_FILE.exists():
            self._home = DEFAULT_CHAT_FILE
            return
        try:
            d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self._state_char_id = d.get("character_id", "")
            self.chat_id = d.get("chat_id", "")
            self._title = d.get("title", "")
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
            self._home = DEFAULT_CHAT_FILE

    # ----------------------------------------------------- scene director

    def _last_gen_args(self) -> dict:
        """The 'gen' snapshot of the newest timeline generation."""
        for evt in reversed(self.timeline):
            if evt.get("type") == "generation":
                g = evt.get("gen") or {}
                if g.get("prompt"):
                    return g
        return {}

    def _director_messages(self) -> list:
        """Focused context for the scene-director decision call."""
        last = self._last_gen_args()
        if last:
            sys = (SCENE_DIRECTOR_PROMPT
                   + "\n\nLast generated image prompt:\n"
                   + str(last.get("prompt"))
                   + "\n\nLast negative prompt:\n"
                   + str(last.get("negative_prompt") or "(none)"))
        else:
            sys = SCENE_DIRECTOR_PROMPT + \
                "\n\nNo image has been generated yet."
        recent = []
        for m in reversed(self.messages[1:]):
            role = m.get("role")
            if role == "user" and m.get("content"):
                recent.append({"role": "user", "content": m["content"]})
            elif role == "assistant" and (m.get("content") or "").strip():
                recent.append({"role": "assistant",
                               "content": m["content"]})
            if len(recent) >= 6:
                break
        recent.reverse()
        return [{"role": "system", "content": sys}] + recent

    def scene_director_pass(self, emit) -> None:
        """After a turn: does the new moment need an image? Best-effort —
        any failure simply means no image. Never touches chat context."""
        if not self.cfg.get("scene_director"):
            return
        try:
            data = self.llm_complete(self._director_messages())
        except LLMError:
            return
        content = (data["choices"][0].get("message")
                   or {}).get("content") or ""
        s, e = content.find("{"), content.rfind("}")
        if s < 0 or e <= s:
            return
        try:
            d = json.loads(content[s:e + 1])
        except json.JSONDecodeError:
            return
        if not d.get("generate"):
            return
        prompt = str(d.get("prompt") or "").strip()
        if not prompt:
            return

        args = {"prompt": prompt,
                "negative_prompt": str(d.get("negative") or "").strip()}
        last = self._last_gen_args()
        model = None
        cp = (self.char or {}).get("checkpoint", "").strip()
        if cp:
            model = cp                      # character's preferred checkpoint
        elif last.get("model") and last["model"] != "(unknown)":
            model = last["model"]
        for k in ("width", "height", "steps", "cfg_scale", "sampler_name",
                  "clip_skip"):
            if last.get(k) is not None:
                args[k] = last[k]
        size = (self.char or {}).get("size") or []
        if len(size) == 2:
            args.setdefault("width", size[0])
            args.setdefault("height", size[1])
        args["prompt"] = ensure_tags(args["prompt"], self.char_tags())

        emit({"type": "status", "text": "scene changed — generating…"})
        if model:
            try:
                self.client.set_model(model)
            except Exception:
                model = None

        from sd_client import save_images
        out: dict = {"r": None, "err": None}

        def work():
            try:
                out["r"] = self.client.txt2img(**args)
            except Exception as e:                     # noqa: BLE001
                out["err"] = e

        th = threading.Thread(target=work, daemon=True)
        th.start()
        while th.is_alive():
            th.join(PROGRESS_POLL)
            if th.is_alive():
                try:
                    p = self.client.progress()
                    emit({"type": "progress",
                          "progress": p.get("progress") or 0,
                          "eta": p.get("eta_relative")})
                except Exception:
                    pass
        if out["err"] or not out["r"]:
            return
        result = out["r"]
        saved = save_images(result, out_dir=OUT_DIR, name_prefix="ai")
        if not saved:
            return
        try:
            seed = json.loads(result.get("info", "{}")).get("seed")
        except json.JSONDecodeError:
            seed = None
        gen = {k: args.get(k) for k in
               ("prompt", "negative_prompt", "width", "height", "steps",
                "cfg_scale", "sampler_name")}
        gen["model"] = model or "(unknown)"
        evt = {"type": "generation",
               "files": ["/outputs/" + f.name for f in saved],
               "count": len(saved),
               "seed": seed,
               "gen": gen,
               "auto": True}
        self.timeline.append(evt)
        emit(evt)
        # keep the model aware that a newer image now exists
        self.messages.append({
            "role": "system",
            "content": "(scene director generated an image; its prompt "
                       "was: " + prompt + ")"})

    def run_turn(self, user_text: str, emit) -> None:
        self.messages.append({"role": "user", "content": user_text})
        evt = {"type": "user", "text": user_text}
        self.timeline.append(evt)
        emit(evt)

        reply = None
        gens_before = sum(1 for e in self.timeline
                          if e.get("type") == "generation")
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
                data = self.llm_complete(self.messages,
                                         temperature=char_temp,
                                         max_tokens=char_mt)
                msg = data["choices"][0]["message"]
                self.messages.append(_clean_assistant_msg(msg))

                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    # an empty final message is normal now that the model
                    # isn't asked to describe the result — just show the card
                    reply = scrub_paths(msg.get("content") or "").strip() \
                        or None
                    break

                for tc in tool_calls:
                    name = tc["function"]["name"]
                    try:
                        args = json.loads(
                            tc["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = self._run_tool(name, args, emit)
                    # keep the context lean: the "gen" snapshot is UI-only
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "name": name,
                        "content": json.dumps(
                            {k: v for k, v in result.items() if k != "gen"},
                            ensure_ascii=False),
                    })
            else:
                reply = "(stopped: too many tool rounds)"
        except LLMError as e:
            evt = {"type": "error", "text": f"✗ {e}"}
            self.timeline.append(evt)
            emit(evt)
            return

        if reply:                                 # empty final reply: no bubble
            evt = {"type": "reply", "text": reply}
            self.timeline.append(evt)
            emit(evt)

        gens_now = sum(1 for e in self.timeline
                       if e.get("type") == "generation")
        # scene director only when the model itself did NOT illustrate this
        # turn — otherwise it tends to re-render the same moment twice
        if gens_now == gens_before:
            self.scene_director_pass(emit)

    def _run_tool(self, name: str, args: dict, emit) -> dict:
        """Run one tool call in a worker thread; stream SD progress while
        generation tools run."""
        is_gen = name in GEN_TOOLS
        if is_gen and self.char:
            # identity tags are authoritative — add them if the model forgot
            args["prompt"] = ensure_tags(args.get("prompt", ""),
                                         self.char_tags())
            # preferred checkpoint applies when the model didn't pick one
            cp = (self.char.get("checkpoint") or "").strip()
            if cp and not args.get("model"):
                args["model"] = cp
            size = self.char.get("size") or []
            if name == "generate_image" and len(size) == 2:
                args.setdefault("width", size[0])
                args.setdefault("height", size[1])
        emit({"type": "tool_start", "name": name})
        out = {"result": None, "error": None}

        def work():
            try:
                out["result"] = execute_tool(self.client, name, args,
                                             OUT_DIR)
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
            self.timeline.append(evt)
            emit(evt)
            return {"error": out["error"]}

        result = out["result"]
        # file PATHS -> bare file names (privacy: no server layout leaks)
        result["saved_files"] = [Path(f).name
                                 for f in result.get("saved_files", [])]
        if result.get("gen"):
            result["gen"]["prompt"] = result["gen"].get("prompt", "")
            evt = {
                "type": "generation",
                "files": ["/outputs/" + f for f in result["saved_files"]],
                "count": result.get("count", 0),
                "seed": result.get("seed_used"),
                "gen": result.get("gen"),
            }
            self.timeline.append(evt)
            emit(evt)
        elif "models" in result:
            evt = {"type": "models",
                   "current_model": result.get("current_model"),
                   "models": result.get("models", [])}
            self.timeline.append(evt)
            emit(evt)
        return result


def chat_completion(api_key: str, llm: str, messages: list,
                    temperature: float | None = None,
                    max_tokens: int | None = None) -> dict:
    """One OpenRouter chat completion with tool definitions."""
    from agent_core import TOOLS
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "SD Agent Web",
    }
    body: dict = {"model": llm, "messages": messages, "tools": TOOLS,
                  "temperature": temperature if temperature is not None
                  else 0.7}
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

    def emit(evt):
        q.put(evt)

    def worker():
        try:
            agent.status_cb = emit
            agent.run_turn(user_text, emit)
        except Exception as e:                         # noqa: BLE001 — last resort
            evt = {"type": "error",
                   "text": f"✗ server error — {type(e).__name__}: {e}"}
            agent.timeline.append(evt)
            emit(evt)
        finally:
            agent.status_cb = None
            agent._save_state()
            agent.lock.release()
            q.put({"type": "done"})

    threading.Thread(target=worker, daemon=True).start()
    return ("ok", q)


# ------------------------------------------------- direct regeneration

_REGEN_KEYS = ("prompt", "negative_prompt", "width", "height", "steps",
               "cfg_scale", "sampler_name", "clip_skip")


def _find_gen_args(agent: Agent, name: str) -> dict | None:
    """The 'gen' snapshot of the timeline generation that produced `name`."""
    for evt in agent.timeline:
        if evt.get("type") != "generation":
            continue
        files = [Path(f).name for f in evt.get("files", [])]
        if name in files:
            g = evt.get("gen") or {}
            if g.get("prompt"):
                return g
    return None


def _args_from_png(path: Path) -> dict:
    """Recover generation args from the image's PNG parameters chunk."""
    info = parse_png_info(path)
    args: dict = {"prompt": info.get("prompt", ""),
                  "negative_prompt": info.get("negative", "")}
    p = info.get("params", "")

    def grab(key: str):
        m = re.search(key + r":\s*([^,]+)", p)
        return m.group(1).strip() if m else None

    steps = grab("Steps")
    if steps and steps.isdigit():
        args["steps"] = int(steps)
    try:
        cfg = grab("CFG scale")
        if cfg:
            args["cfg_scale"] = float(cfg)
    except ValueError:
        pass
    size = grab("Size")
    if size:
        m = re.match(r"(\d+)x(\d+)", size)
        if m:
            args["width"], args["height"] = int(m.group(1)), int(m.group(2))
    sampler = grab("Sampler")
    if sampler:
        args["sampler_name"] = sampler
    clip = grab("Clip skip")
    if clip and clip.isdigit():
        args["clip_skip"] = int(clip)
    return args


def run_regeneration(agent: Agent, name: str, instruction: str, emit) -> dict:
    """Re-create an image directly — no LLM turn, no chat message.

    Empty instruction: same prompt/settings, fresh seed (txt2img).
    With instruction: img2img on the original, instruction appended to the
    original prompt (denoise 0.65 keeps the composition close).
    Returns a timeline-ready 'generation' event with 'src' lineage."""
    from sd_client import save_images

    src = OUT_DIR / name
    g = _find_gen_args(agent, name)
    if g:
        args = {k: g[k] for k in _REGEN_KEYS if g.get(k) is not None}
        model = g.get("model") or None
    else:
        args = _args_from_png(src)            # fall back to the PNG chunk
        model = None
    if not args.get("prompt"):
        raise ValueError("no original prompt found for this image")

    if instruction:
        args["prompt"] = (args.get("prompt", "") + ", "
                          + instruction).strip(" ,")
        args["denoising_strength"] = 0.65
        emit({"type": "status", "text": "applying change…"})
        result = agent.client.img2img(init_image_path=src, **args)
    else:
        args.pop("seed", None)                # fresh variation: new seed
        if model:
            emit({"type": "status", "text": "loading checkpoint…"})
            agent.client.set_model(model)
        emit({"type": "status", "text": "generating…"})
        result = agent.client.txt2img(**args)

    saved = save_images(result, out_dir=OUT_DIR, name_prefix="ai")
    if not saved:
        raise ValueError("the generator returned no image")
    try:
        seed = json.loads(result.get("info", "{}")).get("seed")
    except json.JSONDecodeError:
        seed = None
    gen = {k: args.get(k) for k in
           ("prompt", "negative_prompt", "width", "height", "steps",
            "cfg_scale", "sampler_name", "denoising_strength")}
    gen["model"] = model or "(unknown)"
    return {"type": "generation",
            "files": ["/outputs/" + f.name for f in saved],
            "count": len(saved),
            "seed": seed,
            "gen": gen,
            "src": name}


# ------------------------------------------------------------- gallery

def _safe_name(name: str) -> str | None:
    if not name or "/" in name or "\\" in name or ".." in name \
            or name.startswith("."):
        return None
    return name


def _seed_from_name(name: str) -> str | None:
    m = re.search(r"_\d{8}-\d{6}_(\d+)_\d+\.", name)
    return m.group(1) if m else None


def thumbnail(path: Path) -> Path | None:
    tdir = OUT_DIR / ".thumbs"
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


def gallery_images() -> list[dict]:
    items = []
    try:
        files = [p for p in OUT_DIR.iterdir()
                 if p.is_file() and p.suffix.lower() in IMG_EXTS]
    except OSError:
        return []
    for p in files:
        try:
            st = p.stat()
        except OSError:
            continue
        items.append({"name": p.name, "bytes": st.st_size,
                      "mtime": int(st.st_mtime),
                      "seed": _seed_from_name(p.name)})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


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
        token = self._cookies().get(COOKIE, "")
        expected = hmac.new(self.app.session_secret, b"auth",
                            hashlib.sha256).hexdigest()
        return bool(token) and hmac.compare_digest(token, expected)

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
              mime: str | None = None):
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
        self.end_headers()
        self.wfile.write(data)

    def _denied(self):
        self._json({"error": "unauthorized"}, 401)

    # --------------------------------------------------------------- GET

    def do_GET(self):
        path = unquote(urlparse(self.path).path)

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
            self._json({"authed": self._authed()})
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

        if path.startswith("/outputs/"):
            name = _safe_name(path[len("/outputs/"):])
            f = OUT_DIR / name if name else None
            if f and f.is_file():
                self._file(f, cache="private, max-age=86400")
            else:
                self.send_error(404)
        elif path.startswith("/thumb/"):
            name = _safe_name(path[len("/thumb/"):])
            f = OUT_DIR / name if name else None
            if not (f and f.is_file()):
                self.send_error(404)
                return
            t = thumbnail(f)
            self._file(t if t else f, cache="private, max-age=86400",
                       mime="image/jpeg" if t else None)
        elif path == "/api/status":
            self.api_status()
        elif path == "/api/gallery":
            self._json({"images": gallery_images()})
        elif path == "/api/history":
            self._json({"timeline": self.app.agent.timeline,
                        "busy": self.app.agent.lock.locked()})
        elif path == "/api/settings":
            self.api_settings()
        elif path == "/api/characters":
            self.api_characters_list()
        elif path == "/api/chats":
            self.api_chats_list()
        elif path.startswith("/api/avatar/"):
            self.api_avatar(path[len("/api/avatar/"):])
        elif path == "/api/image_info":
            self.api_image_info()
        else:
            self.send_error(404)

    # -------------------------------------------------------------- POST

    def do_POST(self):
        path = urlparse(self.path).path

        # ---- public ----
        if path == "/api/login":
            body = self._body()
            pw = str(body.get("password", ""))
            if self.app.password and hmac.compare_digest(
                    pw, self.app.password):
                token = hmac.new(self.app.session_secret, b"auth",
                                 hashlib.sha256).hexdigest()
                self._json({"ok": True}, headers=[(
                    "Set-Cookie",
                    f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; "
                    "Max-Age=31536000")])
            else:
                time.sleep(0.6)                       # slow brute force
                self._json({"error": "wrong password"}, 401)
            return
        if path == "/api/logout":
            self._json({"ok": True}, headers=[(
                "Set-Cookie",
                f"{COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")])
            return

        # ---- authed ----
        if not self._authed():
            self._denied()
            return

        if path == "/api/chat":
            self.api_chat()
        elif path == "/api/regenerate":
            self.api_regenerate()
        elif path == "/api/chat/new":
            agent = self.app.agent
            if not agent.lock.acquire(blocking=False):
                self._json({"error": "busy — a turn is already running"},
                           409)
                return
            try:
                agent.new_chat()
            finally:
                agent.lock.release()
            self._json({"ok": True, "timeline": agent.timeline})
        elif path == "/api/chat/select":
            self.api_chat_select()
        elif path == "/api/chat/delete":
            self.api_chat_delete()
        elif path == "/api/characters":
            self.api_characters_save()
        elif path == "/api/character/select":
            self.api_character_select()
        elif path == "/api/character/delete":
            self.api_character_delete()
        elif path == "/api/settings":
            self.api_settings()
        elif path == "/api/model":
            self.api_model()
        elif path == "/api/delete":
            body = self._body()
            name = _safe_name(str(body.get("name", "")))
            f = OUT_DIR / name if name else None
            if not (f and f.is_file()):
                self._json({"error": "not found"}, 404)
                return
            try:
                f.unlink()
                t = OUT_DIR / ".thumbs" / (f.stem + ".jpg")
                if t.exists():
                    t.unlink()
            except OSError as e:
                self._json({"error": str(e)}, 500)
                return
            self._json({"ok": True})
        elif path == "/api/delete_event":
            body = self._body()
            tl = self.app.agent.timeline
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
                self.app.agent.timeline = [e for i, e in enumerate(tl)
                                           if i not in idxs]
                self.app.agent._save_state()
                self._json({"ok": True, "removed": len(idxs)})
                return
            try:
                idx = int(body.get("index", -1))
            except (TypeError, ValueError):
                idx = -1
            if not (0 <= idx < len(tl)):
                self._json({"error": "bad index"}, 400)
                return
            evt = tl.pop(idx)
            self.app.agent._save_state()
            self._json({"ok": True, "removed": evt.get("type")})
        else:
            self.send_error(404)

    # -------------------------------------------------------------- APIs

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
        name = _safe_name(str(body.get("name", "")))
        instr = str(body.get("instruction", "")).strip()[:1000]
        f = OUT_DIR / name if name else None
        if not (f and f.is_file()):
            self._json({"error": "image not found"}, 404)
            return
        agent = self.app.agent
        if not agent.lock.acquire(blocking=False):
            self._json({"error": "busy — a turn is already running"}, 409)
            return
        q = Queue()

        def worker():
            out = {"evt": None, "error": None}

            def work():
                try:
                    out["evt"] = run_regeneration(
                        agent, name, instr, lambda e: q.put(e))
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
                if out["error"]:
                    q.put({"type": "regen_error",
                           "error": scrub_paths(out["error"])})
                else:
                    evt = out["evt"]
                    agent.timeline.append(evt)
                    agent._save_state()
                    q.put({"type": "regen_done",
                           "file": Path(evt["files"][0]).name,
                           "src": name,
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
        status, q = run_chat_turn(self.app.agent, text)
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
        """Chat list of a character, newest first."""
        cid = (parse_qs(urlparse(self.path).query).get("char") or [""])[0]
        card = next((c for c in load_characters()
                     if c.get("id") == cid), None)
        if not card:
            self._json({"error": "character not found"}, 404)
            return
        agent = self.app.agent
        chats = []
        for p in agent._list_chats(cid):
            d = agent._load_chat_payload(p)
            chats.append({"id": p.stem,
                          "title": str(d.get("title") or "New chat"),
                          "updated": int(p.stat().st_mtime),
                          "messages": len(d.get("messages") or [])})
        self._json({"chats": chats, "active": agent.chat_id})

    def api_chat_select(self):
        body = self._body()
        cid = str(body.get("char", "")).strip()
        chat_id = str(body.get("chat", "")).strip()
        agent = self.app.agent
        if not agent.lock.acquire(blocking=False):
            self._json({"error": "busy — a turn is already running"}, 409)
            return
        try:
            agent.select_chat(cid, chat_id)
            self._json({"ok": True, "timeline": agent.timeline})
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
        agent = self.app.agent
        if not agent.lock.acquire(blocking=False):
            self._json({"error": "busy — a turn is already running"}, 409)
            return
        try:
            agent.delete_chat(cid, chat_id)
            self._json({"ok": True, "timeline": agent.timeline})
        finally:
            agent.lock.release()

    def api_status(self):
        agent = self.app.agent
        sd_ok, cur = False, ""
        try:
            r = requests.get(f"{agent.client.base_url}/sdapi/v1/options",
                             timeout=4)
            if r.status_code == 200:
                sd_ok = True
                cur = r.json().get("sd_model_checkpoint", "")
        except Exception:
            pass
        now = time.time()
        force = "refresh" in (parse_qs(urlparse(self.path).query) or {})
        if force or now - self.app.models_cache["ts"] > 300:
            if force:                                     # ask SD to rescan
                try:
                    requests.get(
                        f"{agent.client.base_url}"
                        f"/sdapi/v1/refresh-checkpoints", timeout=10)
                except Exception:
                    pass
            try:
                r = requests.get(
                    f"{agent.client.base_url}/sdapi/v1/sd-models", timeout=6)
                if r.status_code == 200:
                    self.app.models_cache["models"] = [
                        m.get("title", "") for m in r.json()]
                    self.app.models_cache["ts"] = now
            except Exception:
                pass
        char = agent.char
        self._json({
            "sd_url": agent.client.base_url,
            "sd_ok": sd_ok,
            "current_model": cur,
            "models": self.app.models_cache["models"],
            "llm": agent.llm_models,
            "has_key": agent.has_key(),
            "gallery_count": len(gallery_images()),
            "busy": agent.lock.locked(),
            "character": ({"id": char["id"],
                           "name": char.get("name", "")}
                          if char else None),
        })

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

    def api_characters_list(self):
        cards = load_characters()
        out = []
        for c in cards:
            c = dict(c)
            av = self._avatar_path(c["id"])
            if av:
                try:
                    c["avatar"] = ("/api/avatar/" + c["id"]
                                   + "?v=" + str(int(av.stat().st_mtime)))
                except OSError:
                    pass
            try:
                c["chats"] = len(self.app.agent._list_chats(c["id"]))
            except Exception:
                c["chats"] = 0
            out.append(c)
        active = self.app.agent.char
        self._json({"characters": out,
                    "active": active["id"] if active else ""})

    def api_avatar(self, cid_raw: str):
        cid = _safe_name(cid_raw.strip().lower()) or ""
        f = self._avatar_path(cid)
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
        cards = load_characters()
        cid = str(body.get("id", "")).strip()
        existing = next((c for c in cards if c.get("id") == cid), None) \
            if cid else None
        if not existing:
            cid = slugify(name)
            while any(c.get("id") == cid for c in cards):
                cid = cid[:35] + "-" + secrets.token_hex(2)
        card = dict(existing) if existing else {
            "id": cid, "created": int(time.time())}
        card["name"] = name
        card["tagline"] = str(body.get("tagline", "")).strip()[:200]
        card["appearance"] = str(body.get("appearance", "")).strip()[:4000]
        card["persona"] = str(body.get("persona", "")).strip()[:8000]
        card["greeting"] = str(body.get("greeting", "")).strip()[:2000]
        card["checkpoint"] = str(body.get("checkpoint", "")).strip()[:200]
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
            AVATAR_DIR.mkdir(parents=True, exist_ok=True)
            ext = "jpg" if m.group(1).startswith("jp") else m.group(1)
            try:
                (AVATAR_DIR / f"{cid}.{ext}").write_bytes(
                    base64.b64decode(m.group(2)))
            except Exception:
                pass
        if existing:
            cards = [card if c.get("id") == cid else c for c in cards]
        else:
            cards.append(card)
        save_characters(cards)

        agent = self.app.agent
        if agent.char and agent.char["id"] == cid:
            agent.char = card                       # refresh live card
            if agent.messages and \
                    agent.messages[0].get("role") == "system":
                agent.messages[0] = agent._sys_msg()
        # migration: the first created character adopts the unbound chat
        if not existing and agent.char is None and agent.timeline \
                and agent._home == DEFAULT_CHAT_FILE:
            agent.bind_active_chat(card)
        self._json({"ok": True, "id": cid})

    def api_character_select(self):
        body = self._body()
        cid = str(body.get("id", "")).strip()
        agent = self.app.agent
        if not agent.lock.acquire(blocking=False):
            self._json({"error": "busy — a turn is already running"}, 409)
            return
        try:
            if cid:
                card = next((c for c in load_characters()
                             if c.get("id") == cid), None)
                if not card:
                    self._json({"error": "character not found"}, 404)
                    return
                agent.set_character(card)
            else:
                agent.set_character(None)
            self._json({"ok": True, "timeline": agent.timeline})
        finally:
            agent.lock.release()

    def api_character_delete(self):
        body = self._body()
        cid = str(body.get("id", "")).strip()
        cards = load_characters()
        if not any(c.get("id") == cid for c in cards):
            self._json({"error": "character not found"}, 404)
            return
        agent = self.app.agent
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
            self._json({"ok": True})
        finally:
            agent.lock.release()

    def api_image_info(self):
        qs = parse_qs(urlparse(self.path).query)
        name = _safe_name((qs.get("name") or [""])[0])
        f = OUT_DIR / name if name else None
        if not (f and f.is_file()):
            self._json({"error": "not found"}, 404)
            return
        info = {"name": name, "seed": _seed_from_name(name),
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

    def api_settings(self):
        """GET returns masked settings; POST applies and persists them."""
        agent = self.app.agent
        if self.command == "GET":
            cfg = agent.cfg
            key = cfg["openrouter_key"]
            masked = (key[:7] + "…" + key[-4:]) if len(key) > 14 \
                else ("set" if key else "")
            override = cfg.get("system_prompt") or ""
            self._json({"sd_url": cfg["sd_url"], "llm": cfg["llm_models"],
                        "key_masked": masked, "has_key": bool(key),
                        "system_prompt": override or DEFAULT_BASE_PROMPT,
                        "system_prompt_custom": bool(override),
                        "scene_director": bool(cfg.get("scene_director")),
                        "username": cfg.get("username", "")})
            return
        body = self._body()
        cfg = dict(agent.cfg)
        sys_touched = False
        if "sd_url" in body:
            url = str(body["sd_url"]).strip().rstrip("/")
            if url and not url.startswith(("http://", "https://")):
                url = "http://" + url
            if not re.match(r"^https?://[\w.\-]+(:\d+)?(/[\w./\-]*)?$", url):
                self._json({"error": "invalid URL"}, 400)
                return
            cfg["sd_url"] = url
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
            cfg["system_prompt"] = text        # empty = default
            sys_touched = True
        if "scene_director" in body:
            cfg["scene_director"] = bool(body["scene_director"])
        if "username" in body:
            cfg["username"] = str(body["username"]).strip()[:60]
        save_config(cfg)
        agent.apply_config(cfg)
        if sys_touched:
            self._json({"ok": True,
                        "system_prompt": cfg["system_prompt"]
                        or DEFAULT_BASE_PROMPT,
                        "system_prompt_custom": bool(cfg["system_prompt"])})
        else:
            self._json({"ok": True})

    def api_model(self):
        body = self._body()
        title = str(body.get("model", "")).strip()
        if not title:
            self._json({"error": "no model given"}, 400)
            return
        agent = self.app.agent
        if not agent.lock.acquire(blocking=False):
            self._json({"error": "busy — a turn is already running"}, 409)
            return
        try:
            agent.client.set_model(title)
            cur, t0 = "", time.time()
            while time.time() - t0 < 90:              # load takes 10-30 s
                try:
                    cur = agent.client.current_model()
                    if title in cur:
                        break
                except Exception:
                    pass
                time.sleep(2)
            self._json({"ok": True, "current_model": cur or title})
        except Exception as e:                        # noqa: BLE001
            self._json({"error": f"{type(e).__name__}: {e}"}, 502)
        finally:
            agent.lock.release()


class App:
    """Process-wide state handed to the request handler."""

    def __init__(self, password: str):
        self.password = password
        self.session_secret = load_session_secret()
        self.models_cache = {"ts": 0.0, "models": []}
        migrate_flat_chats()                 # Phase A → B chat layout
        self.agent = Agent(load_config())    # loads chat + character too


# ------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description="SD Agent — standalone web app (chat + gallery + PWA)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("PORT", 8000)))
    args = ap.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    password = os.environ.get("AGENT_PASSWORD", "")
    if not password:
        print("ERROR: set AGENT_PASSWORD (the password/PIN for the app).\n"
              "  e.g.  AGENT_PASSWORD=my-secret python server.py",
              file=sys.stderr)
        sys.exit(1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    srv.app = App(password)

    agent = srv.app.agent
    cfg = agent.cfg
    try:
        model = agent.client.current_model()
    except Exception:
        model = "(server unreachable)"

    w = 56
    print()
    print("  ✦ SD AGENT — standalone web app")
    print("  " + "─" * w)
    print(f"  Auth    : password required"
          f"{'' if password else '  (MISSING!)'}")
    print(f"  Key     : {'configured' if agent.has_key() else 'NOT set — add in Settings'}")
    print(f"  LLM     : {', '.join(cfg['llm_models'])}")
    print(f"  SysMsg  : {'custom' if cfg.get('system_prompt') else 'default'}")
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
