# Multi-user plan

Status: **Phases 0-2 done.** This is the decision record plus the phase
order it implies. Read this before changing anything in `server.py`'s auth or
persistence paths.

## Why not a database

The storage layer was never the bottleneck. Chats total 4.8 MB across 18
characters; `characters.json` is 20 KB; `_list_chats` is a glob plus an mtime
sort. There is no slow query here to optimise.

What actually blocked multi-user:

- **One `Agent` per process** — `App.__init__` built a single
  `Agent(load_config())` holding `messages`, `char`, `chat_id` and `timeline`.
  Two users meant two conversations in one set of variables.
- **No identity** — auth was `hmac(secret, b"auth")`, a *constant*. A boolean
  for "anyone holding the password", with no principal to key data by. Zero
  occurrences of `user_id` anywhere in the codebase.
- **Global config** — one `data/config.json` for SD URL, keys and prompt.

The `Agent` class was already structurally a session object (its instance state
is exactly the per-user field set); it was a singleton only by convention, and
the Handler reaches it through just 25 `self.app.agent.*` references.

A DB earns its keep later, if ever, at `feedback_log` aggregation across users,
usage/quota stats, and conversation search. Then it should be **SQLite** — one
box, WAL covers concurrent readers, real transactions. Postgres is ops cost for
no benefit at this scale.

## Decisions

| # | Decision |
|---|---|
| 1 | Internet-facing behind Cloudflare + Caddy (`image.blanketcodestudio.com`). Accounts are **admin-provisioned**, no self-serve signup. |
| 2 | **Bring your own key** — each user supplies their own OpenRouter key. |
| 3 | **Bring your own Forge** — each user supplies their own `sd_url`. |
| 4 | SSRF policy on user-supplied `sd_url`: block link-local/metadata and loopback, allow RFC1918 + Tailscale CGNAT (real Forges live there), admin bypass. |
| 5 | Admin role has **full access** to every user's content and config. |
| 6 | Characters are **owned but shareable** — `owner` + `shared`; queries are `owner == uid OR shared`. |
| 7 | Storage is **per-user directories**: `data/users/<uid>/…`. |
| 8 | Gallery is **per-user**, but the server retains every image; admin sees a cross-user view. |

### Consequences worth knowing

**Admin is omnipotent, so the audit log is load-bearing.** With decision 5 an
admin editing a user's `sd_url` can redirect their Forge, and editing
`characters` can touch their creative work. That is fine for a box you own —
but "full access" with no trail is indistinguishable from a bug when someone
asks about it later. Phase 5 adds an append-only `data/audit.jsonl`.

**BYO Forge makes LoRA install admin-only.** Downloads go to `data/loras` on
*this* box and rely on `--lora-dirs` in *this* box's `webui.settings.sh`. There
is no path for pushing a LoRA file into a user's own Forge. Rather than let
users hit the silent-failure mode AGENTS.md already warns about, LoRA install
stays admin-only and the UI says so.

**BYO Forge was mostly already built.** `Agent.apply_config` hot-swaps the
`SDClient` and resets cached model lists on URL change; the settings endpoint
already does probe-before-trust. But probe-before-trust proves *a Forge
answered*, not *the URL was safe* — which is why decision 4 is a separate
validator rather than a by-product of the probe.

**Divergent Forge versions become normal.** `SDClient.arch_profile()`
deliberately prefers the server's live `<arch>_t2i_*` options over
`ARCH_PROFILES`, so Forge is the source of truth for steps and sampler. With N
Forges at N versions, those defaults legitimately differ per user. Not a bug —
but it means a prompt bug report from one user may not reproduce on another's
Forge.

**Shareable characters touch every character query.** Favourites, LoRA
references and the favourite-first sort all have to respect the
`owner OR shared` filter. This is the most-repeated piece of work in Phases
1–2; worth a single helper rather than inline filters.

## Phase order

Phase 3 before 2 is deliberate — the fairness/ownership policy decides what
"multi-user" feels like and is cheap once users have identities.

### Phase 0 — identity  *(done)*

- `data/users.json`: `id`, `name`, `pass_hash`, `role` (`admin`|`user`),
  `created`, `disabled`.
- PBKDF2-HMAC-SHA256 password hashing (`hashlib`, stdlib, no new dependency).
  The current plaintext `AGENT_PASSWORD` compare is fine for one shared secret,
  not for N.
- Real per-user session tokens in `data/sessions.json`, replacing the constant
  HMAC. Tokens stored hashed (sha256) as the lookup key, so a leaked session
  file does not hand over live sessions.
- `Handler._user()` resolves cookie → session → user record. `_authed()` stays
  as a boolean wrapper so existing call sites are untouched.
- Bootstrap: with no `users.json`, `AGENT_PASSWORD` creates the first admin.
- **`POST /api/login` with no `name` still logs in when exactly one user
  exists.** `qa_probe.py:276` and the manual driving recipe in AGENTS.md both
  post `{"password": …}` only; that must keep working.

### Phase 1 — storage relocation  *(done)*

- Create `data/users/<uid>/` and move `characters.json` + `chat_state.json`
  into the admin's directory (existing state inherits to the first admin).
- Point every path helper at per-user files: `_char_dir`, `_chat_file`,
  `_save_state_locked`, and the module-level loaders, which stop being global
  and become `load_characters(uid)`, `load_personas(uid)`, etc.
- Add `owner` and `shared` to character cards; migrate the existing cast to the
  admin account.
- Fix the non-atomic writes while the paths are being touched anyway:
  `save_personas` (bare `write_text`) and the chat writes in
  `_save_state_locked`. A crash mid-write truncates a chat, which matters
  more with several users than with one.

**Decisions 6 and 7 genuinely conflict, and characters are the exception.**
Sharing is inherently cross-user: a card living in A's private directory
cannot be offered to B, and there is only one `assistant` id so per-user
assistant cards are impossible. So `characters.json` stays a single global
file with `owner` + `shared`, and every read goes through
`visible_characters(uid, is_admin)`. Decision 7 still holds for chats, chat
state, personas and config.

The wart that leaves: the assistant card is owned by the first admin and
marked `shared` so everyone gets a plain chat, which means only an admin can
edit it. Fixing that properly needs per-user character storage, which is what
Phase 1 gave up to make sharing work.

**What Phase 1 deliberately did *not* do:** the Agent was still one
process-wide instance bound to the first admin's `uid`, so a second user's
chat requests still read and wrote the admin's chat tree. Character reads were
already per-user and filtered. Phase 2 removed the shared Agent.

### Phase 2 — Agent per user  *(done)*

- `App.agents: dict[uid, Agent]` behind `agents_lock`, built lazily by
  `App.agent_for(uid)`. The 62 `self.app.agent`/`self.app.sd_state`
  references in the handler became `Handler.agent` / `Handler.sd_state`
  properties that resolve per request.
- **No `App.agent` alias.** An attribute that looks process-wide but is really
  the owner's is precisely the bug this phase removes.
- Per-Agent locks then do the right thing for free: a user cannot double-submit,
  but two *different* users run turns concurrently.
- Each Agent gets its own `SDState`; `apply_config` already resets it on URL
  change.

**Agents are evicted, not accumulated.** Each holds its entire conversation in
RAM and nothing trims it, so `agent_for` drops agents idle past
`AGENT_IDLE_SECS` (30 min) and enforces `AGENT_MAX_RESIDENT` (8) by LRU.
Eviction saves state first — the conversation exists only in memory — and never
touches an Agent whose lock is held, since dropping a mid-turn Agent would
strand the turn and orphan its thread. The rebuilt Agent rehydrates from its own
files.

**Settings are still shared.** `App.config_for(uid)` returns `load_config()`, so
every Agent gets its own copy of one global dict until Phase 3 moves keys into
`users/<uid>/config.json`. The copy-per-agent part is deliberate: it means a
future per-user field cannot leak between users through a shared dict.

### Phase 3 — BYOK + BYO Forge

- Per-user `openrouter_key` and `sd_url`. Remove `sd_url` from shared config.
- Run every `sd_url` save through the decision-4 validator.
- Surface "is your Forge reachable" in the user's own settings.
- **Traps:** `SDState.__init__` takes an `agent` and reads
  `agent.client.base_url`, so it must hang off a shared `SDClient` or it
  follows whichever user last switched.
- No message truncation exists anywhere — `self.messages` grows unbounded. One
  user on a long chat is fine; N users on a box that also hosts Forge is how you
  get OOM-killed mid-generation.

#### A Forge is single-tenant — shared-Forge serialisation  *(done)*

Two users pointing at one WebUI share one global dial: the loaded checkpoint
and its companion components, all in `/sdapi/v1/options`. Changing either is a
POST *outside* Forge's generation queue. Uncoordinated:

```
user A   set_model(X) -> dial = X -> [ A's job runs on X ]
user B   (reads a 3s-stale cache, believes the dial is Y)
         -> [ B's job runs on X ]   wrong model, no error, and `gen.model`
                                      records Y — the regen sheet then replays
                                      a recipe that never ran
```

Forge's own queue already serialises the GPU work, so nothing here competes
with it. `forge_lease` (`sd_client.py`) holds only the "choose the model, then
run" window that actually leaks, keyed by a **normalised URL** — scheme/host/port
case and trailing slash must not produce two locks for one server.

It also force-refreshes the option cache on entry, which fixes the client-side
half: two `SDClient` instances have independent 3-second caches, and
`set_options` only invalidates the caller's, so they drift permanently.

Both generation paths (txt2img, img2img), the regen path and the Settings
model/component switches all take it. Cost is zero when two users want the same
checkpoint — the dial is already correct. When they want different ones it is a
model swap per generation, which is slow but correct. **That is the argument for
one Forge per user**, not merely a nicety.

Still open in Phase 3: warn when a second user claims a URL someone already
holds. It needs per-user `sd_url` to be meaningful, so it lands with the rest.

### Phase 4 — gallery split

- `outputs/<uid>/`; route generation saves by owner; admin-only cross-user
  gallery index.

### Phase 5 — audit log

- Append-only `data/audit.jsonl`: actor, target, action, timestamp. Required by
  decision 5, not optional.

## Assumed unless overridden

None of these block Phase 0:

- Sessions persist across restart, 30-day expiry, individually revocable.
- Existing data (4.8 MB chats, 764 MB LoRAs, the existing cast) inherits to
  the first admin account.
- Light per-account rate limiting only; the brute-force login sleep already
  exists. No per-generation quota.