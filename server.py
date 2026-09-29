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
    DEFAULT_LLM,
    LLMError,
    MAX_TOOL_ROUNDS,
    SYSTEM_PROMPT,
    _clean_assistant_msg,
    _is_auth_error,
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


# ------------------------------------------------------------------ agent

class Agent:
    """Conversation + SD client, driven by user-supplied config."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.client = SDClient(base_url=cfg["sd_url"])
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.timeline = []
        self.lock = threading.Lock()     # one turn at a time
        self.status_cb = None            # set while a turn streams

    # ------------------------------------------------------------ config

    def apply_config(self, cfg: dict):
        """Hot-apply new settings (LLM chain / key / SD URL)."""
        self.cfg = cfg
        if self.client.base_url != cfg["sd_url"]:
            self.client = SDClient(base_url=cfg["sd_url"])

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

    def llm_complete(self, messages: list) -> dict:
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
                                           model, messages)
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

    def clear(self):
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.timeline = []
        self._save_state()

    def _save_state(self):
        try:
            STATE_FILE.write_text(json.dumps({
                "messages": self.messages[1:],
                "timeline": self.timeline,
            }, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _load_state(self):
        if not STATE_FILE.exists():
            return
        try:
            d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(d.get("messages"), list):
                self.messages = [{"role": "system",
                                  "content": SYSTEM_PROMPT}] + d["messages"]
            self.timeline = d.get("timeline") or []
        except Exception:
            pass

    def run_turn(self, user_text: str, emit) -> None:
        self.messages.append({"role": "user", "content": user_text})
        evt = {"type": "user", "text": user_text}
        self.timeline.append(evt)
        emit(evt)

        reply = None
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                data = self.llm_complete(self.messages)
                msg = data["choices"][0]["message"]
                self.messages.append(_clean_assistant_msg(msg))

                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    reply = scrub_paths(msg.get("content") or "(no reply)")
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

        evt = {"type": "reply", "text": reply}
        self.timeline.append(evt)
        emit(evt)

    def _run_tool(self, name: str, args: dict, emit) -> dict:
        """Run one tool call in a worker thread; stream SD progress while
        generation tools run."""
        is_gen = name in GEN_TOOLS
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


def chat_completion(api_key: str, llm: str, messages: list) -> dict:
    """One OpenRouter chat completion with tool definitions."""
    from agent_core import TOOLS
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "SD Agent Web",
    }
    body = {"model": llm, "messages": messages, "tools": TOOLS,
            "temperature": 0.7}
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
        elif path == "/api/clear":
            self.app.agent.clear()
            self._json({"ok": True})
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
        else:
            self.send_error(404)

    # -------------------------------------------------------------- APIs

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
            pass                                      # client left; turn continues

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
        if now - self.app.models_cache["ts"] > 300:
            try:
                r = requests.get(
                    f"{agent.client.base_url}/sdapi/v1/sd-models", timeout=6)
                if r.status_code == 200:
                    self.app.models_cache["models"] = [
                        m.get("title", "") for m in r.json()]
                    self.app.models_cache["ts"] = now
            except Exception:
                pass
        self._json({
            "sd_url": agent.client.base_url,
            "sd_ok": sd_ok,
            "current_model": cur,
            "models": self.app.models_cache["models"],
            "llm": agent.llm_models,
            "has_key": agent.has_key(),
            "gallery_count": len(gallery_images()),
            "busy": agent.lock.locked(),
        })

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
            self._json({"sd_url": cfg["sd_url"], "llm": cfg["llm_models"],
                        "key_masked": masked, "has_key": bool(key)})
            return
        body = self._body()
        cfg = dict(agent.cfg)
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
        save_config(cfg)
        agent.apply_config(cfg)
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
        self.agent = Agent(load_config())
        self.agent._load_state()


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
