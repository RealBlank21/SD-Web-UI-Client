# SD Agent — deployment (Linux home server behind Caddy)

Standalone: no imports from the old project. Layout:

```
server.py        web server + auth + API (Python stdlib + requests + Pillow)
agent_core.py    LLM tool-calling brain (OpenRouter function calling)
sd_client.py     Stable Diffusion WebUI API client
webui/           frontend (index.html, static/, manifest, service worker)
data/            created at runtime: config.json, session secret, chat state
outputs/         generated images
```

## 1. Upload to the server

```bash
rsync -av --exclude data --exclude outputs --exclude __pycache__ \
    sd-agent-web/ user@server:/opt/sd-agent/
```

## 2. Install

```bash
cd /opt/sd-agent
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 3. Set the app password

The password is read from the environment (`AGENT_PASSWORD`). Keep it in the
systemd unit (below) or a drop-in file — do not commit it anywhere.

```bash
# quick manual test
AGENT_PASSWORD='your-secret' .venv/bin/python server.py --port 8000
curl -s localhost:8000/api/auth     # {"authed": false}
```

Configuration the user enters in the app's Settings sheet (stored server-side
in `data/config.json`): OpenRouter API key, LLM model chain, SD WebUI URL.
Optionally pre-seed the SD URL:

```bash
mkdir -p data
echo '{"sd_url": "http://100.93.220.68:7860"}' > data/config.json
```

## 4. systemd service

Copy `deploy/sd-agent.service` to `/etc/systemd/system/`, edit the password
line, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sd-agent
systemctl status sd-agent
```

## 5. Caddy reverse proxy

`deploy/Caddyfile` has the snippet for `image.blanketcodestudio.com`.
Key points: proxy to `localhost:8000`, **disable buffering** so the SSE chat
stream arrives live:

```caddy
image.blanketcodestudio.com {
    reverse_proxy localhost:8000 {
        flush_interval -1        # stream SSE immediately
    }
}
```

Caddy obtains TLS certificates automatically (ACME). Reload with
`sudo systemctl reload caddy`.

## 6. Install as a PWA on the phone

1. Open `https://image.blanketcodestudio.com` in Safari (iOS) or Chrome (Android).
2. Log in with the app password.
3. **iOS:** Share → *Add to Home Screen*. **Android:** menu → *Add to Home screen* / *Install app*.
4. Launch from the home screen — it runs fullscreen (standalone), keeps its
   own session cookie, and the service worker caches the shell so it opens
   even when the phone is offline (chat/gallery still need connectivity).

## Security notes

- The password gates the UI **and** every API route (cookie session, HttpOnly,
  SameSite=Lax, 1 year). "Lock app" in Settings clears it.
- Login attempts are rate-limited by a 0.6 s delay per wrong try; sessions are
  signed with a per-install random secret in `data/session_secret`.
- The API key never reaches the browser (only a masked preview like
  `sk-or-v1-…abcd`).
- LLM replies and tool errors are scrubbed: absolute paths are replaced by
  bare file names, and `edit_image` only accepts file names inside
  `outputs/`.
- Images/thumbnails are served `Cache-Control: private` so shared caches
  don't retain them.
