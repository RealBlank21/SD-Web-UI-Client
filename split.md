# SPLIT: one AI → two AIs (roleplay + image generation)

Plan for moving from the current single-LLM architecture (one model writes the
roleplay prose *and* crafts the SD prompt in one tool-calling loop) to two
separate AIs: a **roleplay AI** that owns the conversation, and an **image AI**
that owns prompt craft and generation.

Status: plan, not implemented. Facts below were verified against the code on
2026-10-17; re-check line numbers before trusting them.

---

## Current architecture, in one paragraph

Each user has one `Agent` (`server.py`) holding one conversation
(`self.messages`) and one `timeline`. Every turn goes through `_complete()`:
up to `MAX_TOOL_ROUNDS=6` calls to a single LLM chain (`llm_models`, via
`chat_completion`), with the full tool set (`generate_image`, `edit_image`,
`list_sd_models`) unless the chat's `auto_images` toggle withholds them
(`tools=[]` in `llm_complete`). That one model both writes the roleplay prose
and crafts the booru-tag SD prompt, guided by a fused system prompt
(`_BASE_SYSTEM_PROMPT` in `agent_core.py` — ~10k chars of roleplay rules + tag
craft — plus the `model_guide.txt` tail, ~19k) built in `_sys_msg()`. The
prompt lineage ("start from your last prompt verbatim") works because the tool
*result* message in the same context carries `prompt` + `series`. Server-side,
`_run_tool()` applies the deterministic net — `ensure_tags`, count
reconciliation, LoRA strip/apply, checkpoint/size defaults — then
`execute_tool()` drives Forge. The SSE event stream (`tool_start` →
`progress` → `generation` → `reply`) is what the webui renders, and what
`qa_probe.py` asserts on.

An image turn today is **2 LLM calls**: call #1 returns the `generate_image`
tool call, the server runs it, the tool result is appended, call #2 writes the
prose. Both calls carry the full fused prompt + guide + whole chat history.

## Target shape

Two brains, one conversation:

```
user message
   │
   ▼
ROLEPLAY AI  (own system prompt: character, story, meta-detection,
   │          correction semantics, "when to request an image")
   │   tool call: request_image(brief="plain-prose description of
   │          what changed; who is in frame; camera; tone", ...)
   ▼
IMAGE AI  (own system prompt: all tag craft + model_guide.txt,
   │       sees: the brief + the previous SD prompt — nothing else)
   │   tool call: generate_image(prompt, negative_prompt, ...)
   ▼
existing deterministic pipeline (ensure_tags → LoRA → checkpoint) → Forge
   │
   ▼
tool result back to RP AI → prose reply
```

The RP AI keeps the **decision** (image or not, one per turn, what changed);
the image AI keeps the **craft** (tags, negatives, scale ladder, checkpoint
recipes). Neither sees the other's rulebook.

## Design decisions

### 1. The interface is a tool call, not prose parsing

RP AI gets exactly one tool:

```python
RP_TOOLS = [{
    "type": "function",
    "function": {
        "name": "request_image",
        "description": "Request an image of the current story moment. Pass a "
                       "plain-prose brief: who is in frame, what changed since "
                       "the last image, the camera/framing, the mood. No "
                       "booru tags — the image assistant writes those.",
        "parameters": {
            "type": "object",
            "properties": {
                "brief":      {"type": "string"},
                "correction": {"type": "string",
                               "description": "What the user said is wrong "
                                              "with the last image, if they "
                                              "said so."},
            },
            "required": ["brief"],
        },
    },
}]
```

This preserves today's semantics almost for free:

- no tool call = pure-dialogue turn (the current "NO image only when nothing
  visual changed" rule becomes "no brief, no image");
- `auto_images` off = withhold the tool, byte-identical to today's behavior;
- abort semantics stay at tool boundaries (`stop_flag` checked between hops);
- the RP tool-call arguments land in the chat file where `qa_probe.py` already
  reads them.

The alternative — the RP AI emitting JSON in its reply text — is strictly
worse with weaker models.

### 2. The image AI is blind to the chat — by design, with two injections

Its per-call context is:

1. system prompt: craft rules + `_guide_tail()` (the model guide);
2. one user message: the brief, the correction if any, and the **previous
   prompt** — the server walks the timeline backwards for the newest
   `generation` event's `gen.prompt` (or "(none — first image of this chat)").

This is *more* robust than today, where "start from your last prompt verbatim"
depends on the model reading its own tool history — the server now supplies it
deterministically, and `sync_gen_context` already keeps the timeline's prompt
current after regens.

### 3. Two model chains, but only one is new

```python
DEFAULT_CONFIG = {
    ...
    "llm_models":   [DEFAULT_LLM],  # becomes the ROLEPLAY chain (kept for compat)
    "image_models": [],             # empty = fall back to llm_models
    "image_prompt": "",             # override for the image system prompt
    ...
}
```

`llm_complete()` becomes `rp_complete()`; a sibling `img_complete()` reuses the
same retry-over-chain logic. One OpenRouter key, so `has_key()` is untouched.
No migration for existing users: absent `image_models` falls back to
`llm_models`. The RP side can drop to a cheap model; the tag-craft side is the
harder rule-following job and probably deserves the stronger one. Ship both
defaulting to the current model and let usage decide.

### 4. The prompt split follows the existing seams

- **RP system prompt**: character block, persona, scenario, LoRA *notes*, the
  meta-vs-in-character detection paragraph, correction handling, length/tone
  rules — plus a new short section on *how to write a brief* (plain prose, no
  booru tags, name who is in frame and what changed).
- **Image system prompt**: everything from "Prompt craft" down — scale ladder,
  count totals, ownership tags, anatomy negatives, image-prompt construction,
  the "DO NOT USE edit_image" caveat reworded — plus the `_guide_tail()`.
- `model_guide` override stays attached to the image side. The existing
  `system_prompt` override (currently replaces everything) becomes RP-only;
  the new `image_prompt` override covers the image side.
  **Both overrides are empty on this box right now**, so there is no live
  override to migrate.

### 5. The deterministic layer does not move

`ensure_tags`, `_count_state` reconciliation, `strip_lora_tags` /
`apply_lora_tags`, `_QUALITY_TAGS` placement, `card_loras` — all stay in
`_run_tool()`, unchanged, because they are code, not prompt. If anything the
split raises their value: the image AI will still drift, and this is the net.
`execute_tool()`, `sd_client.py`, `forge_lease`, `run_regeneration` (which
never used the LLM) — untouched.

### 6. Sequencing: keep today's order first, optimize later

Today an image turn is 2 LLM calls. The split naturally makes it 3:

```
RP#1 (brief)  →  IMG (tags)  →  RP#2 (prose after tool result)
```

Phase 4 can collapse it back to 2 by letting the RP model emit `content` (the
reply) *and* `tool_calls` (the brief) in one message — the server holds the
reply, runs the image, and emits `generation` then `reply` in the current
order. Gate that on measuring, because some providers are shaky about
content+tool_calls coexisting, and "reply written before the image ran"
reintroduces the promise-the-image failure mode the current prompt explicitly
suppresses.

## What does NOT change

- **SSE event protocol and the chat UI.** A turn still emits `tool_start` →
  `progress` → `generation` → `reply`. Phases 1–3 need zero `webui/` changes
  (phase 2 touches only the Settings pane, which triggers the four-place
  version bump from AGENTS.md).
- **Chat persistence.** `messages` remains the RP conversation. Old chats
  contain `generate_image` tool calls with tag prompts in their tool results —
  they're just history; the image AI gets its previous prompt from the
  timeline, not from context, so no chat-file migration. Keep writing `prompt`
  into the tool result content for `sync_gen_context` and QA lineage.
- **Regen sheet, favourites, feedback, multiuser, auth, LoRA library, Forge
  lease.** All orthogonal.
- **qa_probe's checks.** They run on `gen.prompt` from the `generation` event —
  the post-everything prompt — so every check (count conflicts, scale ladder,
  anatomy guards…) still works. One addition: log the image AI's raw tool
  arguments into the tool-result content (e.g. `img_args`) so the
  MODEL-vs-SENT diff workflow in AGENTS.md keeps working — after the split,
  `tool_calls.arguments` on the RP side holds the *brief*, and the QA diff
  wants the image AI's raw prompt as the "MODEL" side.

## File-level change map

| File | Change |
|---|---|
| `agent_core.py` | Split `_BASE_SYSTEM_PROMPT` into `RP_SYSTEM_PROMPT` + `IMG_SYSTEM_PROMPT`; `RP_TOOLS` (`request_image`) and `IMG_TOOLS` (`generate_image`, `edit_image`, `list_sd_models`); `build_image_context(brief, prev_prompt)`; keep `effective_system_prompt` for the RP side; tag helpers untouched |
| `server.py` | `_sys_msg()` → RP prompt; `llm_complete` → `rp_complete`, add `img_complete` (chain from `image_models`); `_complete()` handles the `request_image` hop (image call inside the tool-execution path, stop-flag checked); `_run_tool()` unchanged but fed by the image AI's args; config template + settings API for the new keys |
| `webui/` | Phase 2+: Settings > Generation > Text gets a second chain editor (and phase 3: an `image_prompt` textarea). Four-place version bump; missing-element check before commit |
| `qa/` | New scenarios: brief quality, correction propagation, tone-shift expression matching; existing `counts.json` must keep passing unchanged |
| `AGENTS.md` | The "prompt text is instructions" section changes shape (two prompts, two override fields); the verification snippet needs an `image_prompt` argument |

## Phases

1. **Baseline first.** `.venv/bin/python qa_probe.py --scenario
   qa/scenarios/counts.json` and keep the report as the `--baseline` for
   everything after.
2. **Phase 1 — the split, wire format unchanged.** All of the
   agent_core/server work above. Verify: qa scenarios pass against baseline;
   hand-drive a correction turn and a meta turn through the live API and diff
   `gen.prompt` turn-over-turn (the 80–98% reuse rule should still hold — it
   is now enforced by an injected previous prompt, which is exactly why it
   should get *better*).
3. **Phase 2 — config + Settings.** `image_models`, two chain editors,
   version bump ×4, missing-element check.
4. **Phase 3 — overrides split.** `image_prompt` + reset path; `system_prompt`
   documented RP-only.
5. **Phase 4 — latency** (optional): brief+prose in one RP message; only if
   the third call measurably hurts.
6. **Phase 5 — cleanup:** reword dead prompt text, update
   AGENTS.md/MULTIUSER.md notes.

## Risks to chase in QA

- **Brief fidelity** is the new failure surface: the image AI can't see the
  chat, so a vague brief ("things get spicy") degrades images that today's
  fused model would have gotten right. Adversarial scenarios: tone flip
  mid-scene, character entering/leaving frame, a correction that must
  regenerate, a meta question that must *not*.
- **Tag contamination**: the RP model was trained on this fused prompt's
  habits — briefs may arrive full of booru tags. The brief rules must say
  plain prose, and the image prompt must treat the brief as prose to
  translate.
- **Double rate-limit pressure** on free-tier OpenRouter models (two chains
  per turn).
- The **empty assistant card** must keep working: the brief tool is
  card-agnostic, and `ensure_tags` already handles empty appearance.

## Open decisions (defaults chosen, push back if wanted)

1. **Image-on/off stays RP-decided per turn** (recommended) vs. always
   generate when `auto_images` is on.
2. **`edit_image` stays available to the image AI** (recommended) vs. dropping
   it in favor of the regen sheet.
3. **Phase 2's two-chain UI lands together with the split** (recommended) vs.
   lagging a phase.
