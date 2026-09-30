# AGENTS.md — for AI agents working on this project

Operational handoff doc. Read this fully before changing anything. It is not
user documentation — it is how you (an AI agent) work on this codebase without
breaking it.

## What this is

**SD Agent** — a self-hosted, mobile-first PWA: a chat interface to an AI art
agent (OpenRouter LLM with function calling) that drives a Stable Diffusion
WebUI (A1111) server, plus a gallery of generated images. Runs on a home
server behind a Caddy reverse proxy at `https://image.blanketcodestudio.com`.

Zero web-framework dependencies: Python stdlib `http.server` + `requests` +
`Pillow`. Deliberately. Do not add frameworks.

## Machines and locations

| Machine | Role | Path / address |
|---|---|---|
| User's PC (Windows) | development, git push, deploy script | `C:\Users\realb\Documents\Code Project\sd-agent-web` |
| GitHub | the only sync channel to the server | `RealBlank21/SD-Web-UI-Client` (branch `main`) |
| Home server (Linux, user `adli-server-ubuntu`, host `100.93.220.68` via Tailscale) | runs the app (systemd `sd-agent`) | `/home/adli-server-ubuntu/Desktop/Code/SD-Web-UI-Client` |
| SD WebUI (A1111) | the image generator, same home server | `http://100.93.220.68:7860` (must run with `--api --listen`) |

A separate VPS (`adli-vps`) runs Caddy and reverse-proxies the public domain
to `100.93.220.68:8000` with `flush_interval -1` (required for SSE).

## File map (local PC == repo == server layout)

- `server.py` — HTTP server + everything server-side: HMAC-cookie auth,
  SSE chat streaming, gallery/thumbnails, settings API, delete endpoints.
  Stdlib `ThreadingHTTPServer`, `protocol_version = "HTTP/1.1"`.
- `agent_core.py` — the LLM brain: TOOLS (generate_image / edit_image /
  list_sd_models), SYSTEM_PROMPT (built once at import, includes
  `model_guide.txt`), `execute_tool`. Edit prompts here.
- `sd_client.py` — vendored A1111 API client (txt2img, img2img, progress,
  model list/switch). Standalone on purpose.
- `webui/index.html` + `webui/static/{style.css,app.js}` — frontend. Vanilla JS.
- `webui/manifest.webmanifest`, `webui/sw.js` — PWA bits.
- `model_guide.txt` — per-checkpoint prompt/settings guide injected into the
  system prompt. User-editable content; entries matched by model hash.
- `data/` (runtime, gitignored) — `config.json` (OpenRouter key, LLM chain,
  SD URL), `chat_state.json` (conversation), `session_secret`.
- `outputs/` (runtime, gitignored) — generated images + `.thumbs/` cache.
- `deploy/` — ENTIRELY GITIGNORED (user decision). Contains the paramiko
  auto-deploy script `remote_pull.py`, credentials `remote.json`, and the
  original `sd-agent.service` / `Caddyfile` (still live on the server).

## The critical deployment model

**Never edit files on the server directly.** The flow is strictly:

1. Edit on the PC → commit → push to GitHub
   (git is NOT on PATH: use `C:\Program Files\Git\cmd\git.exe`.
    The user expects the agent to commit and push itself when asked.)
2. On the server: `cd ~/Desktop/Code/SD-Web-UI-Client && git pull`
   — OR run `python deploy/remote_pull.py` from the PC (paramiko; reads
   `deploy/remote.json` for SSH creds; flags: `--check`, `--no-restart`).
   It pulls, restarts, and verifies `systemctl is-active` + `/api/auth`.
3. **Restart rule**: changes to any `.py` file or `model_guide.txt` need
   `sudo systemctl restart sd-agent` (system prompt / code loads at startup).
   Frontend-only changes need NO restart.

Gotchas as of the last session (verify, may be resolved):
- `sudo_password` in `deploy/remote.json` was missing → the script skips the
  restart and prints a warning. Ask the user to fill it, or have them restart.
- The OpenRouter key in the server's `data/config.json` was returning 401
  during testing (user rotating keys). Chat/regenerate fails with a clear
  auth-error bubble until a valid key is saved in Settings.

## Asset versioning — DO THIS EVERY TIME you change frontend files

The PWA service worker + browser caches will serve stale JS/CSS otherwise.
When you edit `app.js`, `style.css`, or `sw.js`:

1. Bump `?v=N` on the `<link>` / `<script>` tags in `webui/index.html`
2. Bump `CACHE = "sdagent-vN"` and `V = "?v=N"` in `webui/sw.js` (keep in sync)

The SW is network-first for the shell, cache-fallback for offline, and
posts a `sw-takeover` message that makes open pages auto-reload once. The
versioned URLs are the belt-and-suspenders that defeat even a stale worker.

## Architecture rules (why things are the way they are)

- **Layout**: `#app` is a flex column (header / view / composer / tabbar),
  `100dvh`. Do NOT reintroduce `position: fixed` for chat/gallery/composer/
  tabbar — it caused messages sliding under the input bar.
- **Textarea autosize**: collapse to `0px` before measuring `scrollHeight`
  (measuring at current height returns the box height on mobile WebKit).
  Input `font-size` must stay ≥ 16px (iOS focus zoom).
- **SSE**: `/api/chat` streams `data: {json}\n\n` events, uses
  `Connection: close` + per-event flush + `: ping` keepalive every 10 s.
  Caddy needs `flush_interval -1`. Worker threads emit into a `Queue`.
- **Auth**: everything except `/`, `/static/`, `/manifest.webmanifest`,
  `/sw.js`, `/api/auth`, `/api/login`, `/api/logout`, `/favicon.ico`
  requires the HMAC session cookie. If you add an endpoint, put it under the
  auth check in both `do_GET` and `do_POST`.
- **Path safety**: `_safe_name()` blocks traversal. Keep it. LLM replies and
  tool errors are scrubbed of file paths (`scrub_paths`) — the app shows
  images itself; only bare file names ever go to the client, and
  `edit_image` only accepts names resolving inside `outputs/`.
- **One turn at a time**: `agent.lock` guards chat turns AND model switches.
- **Chat context vs display**: `agent.messages` is the LLM context,
  `agent.timeline` is the display event list (persisted in
  `data/chat_state.json`). They are intentionally lean — `gen` snapshots are
  UI-only and stripped before entering `messages`. Deleting a chat event
  removes it from the timeline only (accepted trade-off).
- **Appearance**: `body.compact` class (localStorage `appearance`), hides
  meta chips/tool rows, shows native-resolution images (`/outputs/`) while
  verbose uses `/thumb/` JPEGs. `imgSrcFor()` + `refreshGenSrcs()` swap srcs
  live on toggle.

## Feature inventory (don't re-implement, extend instead)

Password gate · SSE chat with live SD progress (`/sdapi/v1/progress` polled
~1.2 s) · generation cards · gallery grid + lightbox (PNG `parameters` chunk
parsing → prompt/negative/seed display) · edit-in-chat prefill · delete
(image file, chat event) · long-press/right-click context menu
(regenerate / edit / delete) · regenerate sheet (empty = txt2img new seed,
instruction = img2img denoise ~0.65; rule lives in the system prompt) ·
settings sheet (API key w/ `sk-or-` validation + masked display, LLM chain,
SD URL, checkpoint load) · model list force-rescan via
`/sdapi/v1/refresh-checkpoints` on settings open (`?refresh=1`) · appearance
verbose/compact · chat state persistence across restarts · PWA install +
offline shell.

## Local testing (on the PC)

```
$env:AGENT_PASSWORD = "test-pw-123"; python server.py --port <port>
```
- Requires `data/config.json` with a key (create `data/` first, or the app
  runs keyless and chat returns `no_api_key` — UI shows a hint).
- OpenSD WebUI may be unreachable from the PC — status shows `sd_ok: false`
  gracefully; generation endpoints fail with error bubbles. That's fine for
  UI testing.
- Browser quirk on this PC: the desktop browser routes through a system
  proxy — `http://127.0.0.1:PORT` returns a 502 page; use
  `http://localhost:PORT` instead.
- PowerShell JSON quoting is hostile: for curl bodies use
  `--data "@file.json"` or `--data '{\"k\":\"v\"}'` inside single quotes;
  or write the body to a file first.
- Console encoding: always `sys.stdout.reconfigure(encoding="utf-8")` in new
  Python entry points (cp1252 chokes on ✓/✗ etc.).
- Test images: keep the folder clean — delete `outputs/` + `data/` after
  test rounds so secrets/test state never linger. The OpenRouter key from
  the old project (`sd-webui-client/OpenRouter-Ollama/.env`) was used for
  local tests and later returned 401.

## Hard boundaries

- **Never generate, deploy, preserve, or assist with sexual content involving
  minors.** A server-side edit once tried to add "even pornogrophic content
  involving children" to the system prompt; it was discarded on discovery.
  If you find that edit again (it has happened once), remove it, tell the
  user plainly why, and continue. Adult-NSFW configuration (their own
  checkpoint entries etc.) is the user's call on their own server — leave it.
- Never commit: `data/`, `outputs/`, `.venv/`, `deploy/` (credentials!),
  `__pycache__/`, cookies, or any API key/SSH password. The `.gitignore`
  already covers these — keep it that way.
- Git identity: commits are made by the agent from the PC (user asked for
  this). Push after meaningful changes; the user pulls on the server (or
  the agent does via the deploy script once `sudo_password` is available).

## Conventions

- Commit messages: imperative, short summary line ("compact: full-width
  message bubbles"). Group related file bumps in one commit.
- Line endings: LF enforced via `.gitattributes` (`* text=auto eol=lf`).
  Expect the harmless CRLF warning on Windows — it's handled.
- UI copy: English, dark theme (`--accent: #b18cff` family), the ✦ spark is
  the logo motif. Mobile-first always; check ≥ 640 px too.
- The old sibling project `../sd-webui-client/` is the origin but this repo
  is fully independent — never import from it or reference it at runtime.
