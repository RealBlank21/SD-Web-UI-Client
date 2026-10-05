"""
Agent brain — LLM tool-calling core for the SD Agent web app.

Vendored from the original ai_agent.py, with these changes for deployment:
- The system prompt asks the model to refer to images by FILE NAME only
  (the server scrubs any file paths from replies anyway).
- edit_image resolves the given file name inside the outputs folder only.
"""

import base64
import json
import re
import time
from pathlib import Path

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_LLM = "google/gemini-3.5-flash-lite"
MAX_TOOL_ROUNDS = 6   # safety cap: tool calls per user message


class LLMError(Exception):
    """Raised when the OpenRouter API call fails."""


def _is_auth_error(e: LLMError) -> bool:
    return "auth error" in str(e).lower()


# ------------------------------------------------------------------ tools

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "Generate one or more images from a text prompt "
                           "using Stable Diffusion.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "SD prompt: subject, style, "
                                       "composition, lighting, quality tags.",
                    },
                    "negative_prompt": {
                        "type": "string",
                        "description": "What to avoid, e.g. 'lowres, blurry, "
                                       "bad anatomy, watermark'.",
                    },
                    "width": {"type": "integer",
                              "description": "px. SDXL: 1024x1024, 832x1216, "
                                             "1216x832. SD1.5: 512-1024."},
                    "height": {"type": "integer", "description": "px, see width."},
                    "steps": {"type": "integer",
                              "description": "Sampling steps. Depends on the "
                                             "model: 20-35 for SDXL/Anima/Krea, "
                                             "4-15 for distilled Turbo/LCM/LCM-"
                                             "Schnell models, 20 for Flux.1 "
                                             "dev."},
                    "cfg_scale": {"type": "number",
                                  "description": "Prompt adherence. 4-6 for "
                                                 "SDXL/Anima/Krea, 1-2 for "
                                                 "distilled models (Flux, "
                                                 "Qwen, Z-Image Turbo, Wan), "
                                                 "~7 for SD1.5."},
                    "distilled_cfg_scale": {
                        "type": "number",
                        "description": "Real guidance of a distilled model "
                                       "(Flux/Qwen/Z-Image/Wan). Only needed "
                                       "when you want something other than the "
                                       "model's own default (usually 3-9)."},
                    "sampler_name": {"type": "string",
                                     "description": "e.g. 'Euler a', 'Euler', "
                                                    "'ER SDE', 'LCM', 'DPM++ "
                                                    "2M'."},
                    "scheduler": {"type": "string",
                                  "description": "Schedule type, separate from "
                                                 "the sampler in Forge Neo: "
                                                 "'Automatic', 'Karras', "
                                                 "'Simple', 'Normal', 'Beta', "
                                                 "'Turbo', 'flow_match', "
                                                 "'flux2'."},
                    "clip_skip": {"type": "integer",
                                  "description": "CLIP skip (2 for most anime "
                                                 "SDXL checkpoints)."},
                    "seed": {"type": "integer",
                             "description": "-1 = random. Reuse a seed to "
                                            "iterate on an image."},
                    "batch_size": {"type": "integer",
                                   "description": "How many images (only if "
                                                  "user asks for several)."},
                    "model": {"type": "string",
                              "description": "Checkpoint title to switch to. "
                                             "Switching takes 10-30 s; only "
                                             "when user asks."},
                    "text_encoder": {
                        "type": "string",
                        "description": "Companion text encoder for modular "
                                       "DiT models, e.g. "
                                       "'qwen_3_06b_base.safetensors'. Only "
                                       "for Flux/Qwen/Anima/Krea/Wan-style "
                                       "models; call list_sd_models first to "
                                       "see what is installed.",
                    },
                    "sd_vae": {
                        "type": "string",
                        "description": "Companion VAE for a modular model, "
                                       "e.g. 'qwen_image_vae.safetensors' or "
                                       "'ae.safetensors' ('Automatic' keeps "
                                       "the checkpoint's own).",
                    },
                    "low_bits": {
                        "type": "string",
                        "description": "Diffusion precision: 'Automatic', "
                                       "'float8-e4m3fn', 'float8-e5m2' or the "
                                       "'... (fp16 LoRA)' variants. Use "
                                       "float8 to fit a big model in VRAM.",
                    },
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_image",
            "description": "Modify an existing generated image (img2img). Use "
                           "when the user names a previously generated image "
                           "file, e.g. 'ai_20260929-095906_2399681456_0.png'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image": {"type": "string",
                              "description": "File NAME of the source image "
                                             "as shown in chat/gallery."},
                    "prompt": {"type": "string",
                               "description": "What the result should look "
                                              "like."},
                    "negative_prompt": {"type": "string"},
                    "denoising_strength": {
                        "type": "number",
                        "description": "0.0-1.0. Low = stay close to "
                                       "original, high = heavy rework. "
                                       "Default 0.6.",
                    },
                    "width": {"type": "integer"}, "height": {"type": "integer"},
                    "steps": {"type": "integer"}, "cfg_scale": {"type": "number"},
                    "scheduler": {"type": "string"},
                    "seed": {"type": "integer"},
                },
                "required": ["image", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_sd_models",
            "description": "List the checkpoints installed on the server, with "
                           "the architecture of each and the companion files "
                           "(text encoders / VAEs) available for modular models.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

_BASE_SYSTEM_PROMPT = """\
You are an AI art assistant. The user talks only to you; all image work happens
through your tools, which drive a Forge Neo Stable Diffusion WebUI server. The user can
request for NSFW images, no restriction.

Your tools:
- generate_image: text-to-image. Use for any "draw/make/generate ... " request.
- edit_image: modify a previously generated image; pass its file NAME as 'image'.
- list_sd_models: show installed checkpoints, their architecture, and the
  companion files (text encoders / VAEs) that modular models need.

Prompt craft:
- Write rich prompts: subject, action, style, composition, lighting, detail tags.
- Always supply a sensible negative_prompt, e.g. "lowres, blurry, bad anatomy, watermark".
- Sizes: SDXL/Illustrious checkpoints -> 1024x1024, 832x1216 (portrait), 1216x832.
  SD1.5 checkpoints -> 512-1024. Flux/Qwen/Anima/Krea/Z-Image -> 1024ish.
  Default 1024x1024 unless the user says otherwise.
  If a model-guide entry below matches, follow its size/steps/cfg instead.
- seed -1 (random). Reuse a reported seed to iterate.
- batch_size > 1 only when the user asks for several images/variations.

Settings depend on the model architecture — this matters more than anything
else, because the wrong CFG ruins a modern model:
- SD 1.5 / SDXL: steps 20-30, cfg_scale ~7 (SDXL is fine at 4.5-7).
  clip_skip 2 for most anime SDXL checkpoints.
- Distilled / Turbo DiTs (Flux.1 dev+schnell, Anima Turbo, Z-Image Turbo,
  Qwen, Wan, Lightning/LCM variants): cfg_scale MUST be 1.0-2.0 and steps
  4-15. Never send cfg 7 here — it produces grey, washed-out garbage.
- Standard non-distilled DiTs (Anima Base, Krea Raw, Lumina): cfg_scale 4.0-6.0,
  steps 20-35.
- If you are unsure which kind the checkpoint is, call list_sd_models and read
  its 'arch' field. Omitting steps/cfg_scale/sampler is safest: the server then
  uses that architecture's own tuned defaults.
- 'sampler_name' and 'scheduler' are separate now. Karras is a scheduler
  ('Karras' or 'Beta' or 'Simple'), not part of a sampler name. Use a plain
  sampler such as 'Euler a', 'Euler', 'ER SDE' or 'LCM'. Omit both to use the
  architecture's defaults.
- Negative prompts matter for SD1.5/SDXL and barely do anything for most
  distilled DiTs — don't fight it, just keep a light one.

Modular models (Forge Neo):
- Newer architectures (Flux, Qwen, Anima, Krea, Wan, Z-Image, Lumina, ERNIE)
  are not single files: the checkpoint is a diffusion model that needs a
  companion TEXT ENCODER and often a companion VAE. The app detects each
  checkpoint's family from its own bytes and attaches the right companions
  automatically, so you normally do nothing.
- Only pass 'text_encoder' / 'sd_vae' / 'low_bits' when the user asks to change
  them, or when a tool error says a component is missing. Use an exact
  file name from list_sd_models. 'sd_vae': 'Automatic' keeps the default.
- 'low_bits' controls diffusion precision: 'Automatic', 'float8-e4m3fn',
  'float8-e5m2' (or the '(fp16 LoRA)' variants). float8 roughly halves VRAM,
  which is how a big DiT fits on a small card. Never invent values like
  'int4' or 'nvfp4' — this server does not offer them.

Rules:
- When the user asks for an image, ALWAYS call a tool — never say you cannot.
- If the user names a checkpoint/model, pass it via the 'model' argument of
  generate_image (switching takes 10-30 s, so only when actually needed).
  The 'model' value must be an EXACT checkpoint title as returned by
  list_sd_models — if unsure of the exact title, call list_sd_models first.
- Do NOT mention file names, seeds or technical details in your reply — the
  app already shows the generated image to the user.
- For edit_image, the 'image' argument must be a bare file name of a previously
  generated image (as shown in chat or the gallery), never a path.
- If a tool returns an error, explain it in plain words and suggest a fix
  (e.g. out-of-memory -> lower size, or 'low_bits': 'float8-e4m3fn').
- Ordinary conversation: just answer, no tools.
"""

# Used only when the model guide file is missing or empty.
_FALLBACK_EXAMPLE = """\
Good example of image-gen settings (anime/booru-tag style):

Prompt:
masterpiece, best quality, amazing quality, 4k, very aesthetic, high resolution, ultra-detailed, absurdres, newest, esthetic, scenery, 1girl, solo, cute, pink hair, long hair, choppy bangs, long sidelocks, nebulae cosmic purple eyes, rimlit eyes, facing to the side, looking at viewer, downturned eyes, light smile, red annular solar eclipse halo, red choker, detailed purple blazer, collared white shirt, big red neckerchief, glowing stars in hand, dispersion \\(optics\\), from side, from below, dutch angle, portrait, upper body, head tilt, colorful, rim light, backlit, (colorful light particles:1.2), cosmic sky, aurora, chaos, perfect night, fantasy background, dreamlike atmosphere, BREAK, detailed background, blurry foreground, bokeh, depth of field, volumetric lighting

Negative prompt:
photorealistic, realistic, 3d, extra digits, (particles, adversarial_noise:1.2), multiple views, multiple angle, split view, grid view, two shot, outside border, picture frame, framed, border, letterboxed, pillarboxed, 2koma, modern, recent, old, oldest, cartoon, graphic, text, painting, crayon, graphite, abstract, glitch, deformed, mutated, ugly, disfigured, long body, lowres, bad anatomy, bad hands, missing fingers, extra fingers, extra digits, fewer digits, cropped, very displeasing, (worst quality, bad quality:1.2), sketch, jpeg artifacts, signature, watermark, username, (censored, bar_censor, mosaic_censor:1.2), simple background, conjoined, bad ai-generated

Settings:
cfg_scale: 4.5, steps: 20, sampler_name: Euler a
width: 768, height: 1344, clip_skip: 2
seed: -1 (random) — reuse a previous seed only to iterate on an image

Match this level of prompt detail and structure (quality tags, character tags,
composition/camera tags, lighting, BREAK for background section) whenever the
request fits that style.
"""


def _guide_tail() -> str:
    """Per-model settings guide from the guide file (optional section)."""
    guide_file = Path(__file__).resolve().parent / "model_guide.txt"
    content = ""
    if guide_file.exists():
        content = guide_file.read_text(encoding="utf-8",
                                       errors="replace").strip()

    if content:
        guide = (
            "## Model-specific settings guide (model_guide.txt)\n"
            "Reference prompt + generation settings per checkpoint. When the\n"
            "checkpoint you are generating with matches an entry, follow that\n"
            "entry closely:\n"
            "- Keep its quality-tag scaffolding and negative prompt; swap the\n"
            "  subject, character, composition and camera tags for what the\n"
            "  user asked for.\n"
            "- Use its steps, CFG scale, sampler, width/height and clip_skip\n"
            "  as-is.\n"
            "- Its sampler names may be legacy combined ones. In Forge Neo,\n"
            "  'DPM++ 2M Karras' means sampler 'DPM++ 2M' + scheduler 'Karras'\n"
            "  and 'DPM++ SDE Karras' means 'DPM++ SDE' + 'Karras'. Either pass\n"
            "  the split pair, or just pass the sampler alone — the server\n"
            "  keeps the schedule type itself.\n"
            "- Entry titles are informal names; match them to installed\n"
            "  checkpoints by similarity. Tip: an entry's 'Model hash' appears\n"
            "  in the server's checkpoint title, e.g.\n"
            "  'novaAnimeXL_ilV190.safetensors [fa486caafc]'.\n"
            "- Ignore export artifacts: 'middle:', 'shadow:', 'highlight:',\n"
            "  Global Seed, Model hash values. The guide's example Seeds are\n"
            "  ONLY for reproducing those exact reference images — for new\n"
            "  images ALWAYS use seed -1.\n"
            "- Ignore the 'Hires ...' fields (hi-res fix is not exposed via\n"
            "  the tools) and the older A1111-only notes such as 'Enable\n"
            "  Quantization in K samplers'.\n"
            "- These entries are written for SD1.5/SDXL. If the matched\n"
            "  checkpoint is a newer architecture (Flux, Qwen, Anima, Krea,\n"
            "  Wan, Z-Image), follow that architecture's CFG/steps rules above\n"
            "  instead of the entry's numbers.\n"
            "- No matching entry -> use your own judgment.\n"
            "\n---- begin model_guide.txt ----\n"
            f"{content}\n"
            "---- end guide ----"
        )
        return guide + "\n"
    return _FALLBACK_EXAMPLE


def build_system_prompt(base: str | None = None) -> str:
    """Compose a full system prompt: base rules + model-guide tail.
    Pass a custom base to swap the rules while keeping the guide."""
    return (base if base is not None else _BASE_SYSTEM_PROMPT) \
        + "\n\n" + _guide_tail()


DEFAULT_BASE_PROMPT = _BASE_SYSTEM_PROMPT       # what Settings edits
DEFAULT_SYSTEM_PROMPT = build_system_prompt()   # built-in default (base+guide)


def effective_system_prompt(override: str | None = None) -> str:
    """The system prompt actually sent to the LLM: the custom system message
    with the guide tail appended, or the built-in default when unset."""
    base = (override or "").strip()
    return build_system_prompt(base) if base else DEFAULT_SYSTEM_PROMPT


def ensure_tags(prompt: str, tags: str) -> str:
    """Prepend identity tags missing from an SD prompt (no duplicates).
    Matching ignores backslash-escapes and parentheses so guide-style
    tags like 'arlecchino \\(genshin impact\\)' still count as present."""
    prompt = (prompt or "").strip()
    tags = (tags or "").strip()
    if not tags:
        return prompt
    have = [t.strip() for t in prompt.split(",") if t.strip()]

    def norm(t: str) -> str:
        return re.sub(r"[\\()]", "", t.lower()).strip()

    have_norm = {norm(t) for t in have}
    missing = []
    for t in tags.split(","):
        t = t.strip()
        if t and norm(t) not in have_norm:
            missing.append(t)
    if not missing:
        return prompt
    return ", ".join(missing + have)


# ------------------------------------------------------------------- tools

def _seed_of(result: dict):
    try:
        return json.loads(result.get("info", "{}")).get("seed")
    except json.JSONDecodeError:
        return None


def _gen_info(client, args: dict, elapsed: float, seed,
              prepared: dict | None = None) -> dict:
    """Snapshot of what was actually requested, for the UI.

    `prepared` is the payload sd_client actually built — the numbers in it are
    the ones the server used, after the architecture had filled in the blanks
    and clamped anything out of range, which is not necessarily what the LLM
    asked for. The UI's recipe editor and the regen sheet both replay this.
    """
    try:
        model = client.current_model()
    except Exception:
        model = "(unknown)"
    info = {
        "model": model,
        "prompt": args.get("prompt", ""),
        "negative_prompt": args.get("negative_prompt", ""),
        "width": args.get("width", 1024),
        "height": args.get("height", 1024),
        "steps": args.get("steps", 25),
        "cfg_scale": args.get("cfg_scale", 7.0),
        "sampler_name": args.get("sampler_name", "Euler a"),
        "clip_skip": args.get("clip_skip"),
        "batch_size": args.get("batch_size", 1),
        "denoising_strength": args.get("denoising_strength"),
        "seed": seed,
        "elapsed": elapsed,
    }
    if prepared:
        info.update({
            "model": prepared.get("model") or model,
            "arch": prepared.get("arch", ""),
            "distilled": prepared.get("distilled", False),
            "steps": prepared.get("steps", info["steps"]),
            "cfg_scale": prepared.get("cfg_scale", info["cfg_scale"]),
            "sampler_name": prepared.get("sampler")
            or info["sampler_name"],
            "scheduler": prepared.get("scheduler") or "",
            "distilled_cfg_scale": prepared.get("distilled_cfg_scale"),
            "clip_skip": prepared.get("clip_skip", info["clip_skip"]),
        })
        notes = [n for n in (prepared.get("notes") or [])
                 if not n.startswith(("sampler ", "'"))]
        if notes:
            info["notes"] = notes
    return info


def _resolve_output_image(name: str, out_dir: Path) -> Path:
    """Resolve a bare file name to an image inside the outputs tree.

    Only bare names are accepted (no paths). The LLM is told file names, not
    paths, and paths are scrubbed from its replies — but a file may have been
    filed into a gallery subfolder, so fall back to searching the tree. File
    names embed a timestamp + seed, so collisions are not a practical worry.
    """
    if not name or "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"invalid image name: {name!r}")
    p = out_dir / name
    try:
        if p.resolve().is_file() and p.resolve().parent == out_dir.resolve():
            return p.resolve()
        for hit in sorted(out_dir.rglob(name))[:1]:
            if hit.is_file():
                return hit.resolve()
    except OSError:
        pass
    raise ValueError(f"invalid image name: {name!r}")


def execute_tool(client, name: str, args: dict, out_dir: Path) -> dict:
    """Run a tool call; wraps sd_client methods. Returns a JSON-able dict."""
    from sd_client import SDWebUIError, save_images

    def _human(e: SDWebUIError) -> SDWebUIError:
        """Translate raw WebUI load failures into something actionable."""
        msg = str(e)
        if "VAE state dict" in msg or "text encoder" in msg.lower() \
                or "Failed to recognize model" in msg:
            return SDWebUIError(
                msg + " — the checkpoint could not run: its model family is "
                "missing companion files (text encoder / VAE) or it is filed "
                "under the wrong one. Fix in Settings -> Image: pick the "
                "family, attach the components, load the checkpoint again.")
        return e

    args = {k: v for k, v in args.items() if v is not None}

    if name == "generate_image":
        # A model switch and its companion components belong in one options
        # write: separate posts mean separate model reloads, and the second
        # reload can fail on a card that is already tight on VRAM.
        model = args.pop("model", None)
        components = {k: args.pop(k) for k in
                      ("text_encoder", "sd_vae", "low_bits") if k in args}
        switched = None
        try:
            if model:
                switched = client.set_model(model, **components)
            elif components:
                client.configure(**components)
            t0 = time.perf_counter()
            prepared = client.prepare_args(args, model=model, kind="txt2img")
            result = client.submit(prepared["payload"])
        except SDWebUIError as e:
            raise _human(e) from None
        elapsed = time.perf_counter() - t0
        saved = save_images(result, out_dir=out_dir, name_prefix="ai")
        gen = _gen_info(client, args, elapsed, _seed_of(result), prepared)
        if switched and switched.get("notes"):
            gen["notes"] = (gen.get("notes") or []) + switched["notes"]
        return {"saved_files": [str(p) for p in saved],
                "count": len(saved),
                "seed_used": _seed_of(result),
                "gen": gen}

    if name == "edit_image":
        # accept both 'image' and the legacy 'image_path' key
        src_name = args.pop("image", None) or args.pop("image_path", None)
        if not src_name:
            raise ValueError("no source image given")
        src = _resolve_output_image(str(src_name), out_dir)
        if not src.exists():
            raise FileNotFoundError(
                f"image not found: {src.name} — pick a name from the "
                "gallery or a generation in this chat")
        model = args.pop("model", None)
        if model:
            try:
                client.set_model(model)
            except SDWebUIError as e:
                raise _human(e) from None
        t0 = time.perf_counter()
        try:
            prepared = client.prepare_args(
                {**args, "init_images": [base64.b64encode(src.read_bytes())
                                         .decode()]},
                model=model, kind="img2img")
            result = client.submit(prepared["payload"], img2img=True)
        except SDWebUIError as e:
            raise _human(e) from None
        elapsed = time.perf_counter() - t0
        saved = save_images(result, out_dir=out_dir, name_prefix="ai_edit")
        return {"saved_files": [str(p) for p in saved],
                "count": len(saved),
                "seed_used": _seed_of(result),
                "gen": _gen_info(client, args, elapsed, _seed_of(result),
                                 prepared)}

    if name == "list_sd_models":
        models = [{"title": m["title"], "arch": m.get("arch", "sd")}
                  for m in client.list_models()]
        files = client.companion_files()
        return {"current_model": client.current_model(),
                "current_arch": client.current_arch(),
                "models": models,
                "text_encoders": files["text_encoder"],
                "vaes": files["vae"],
                "low_bits": client.list_low_bits()}

    return {"error": f"unknown tool: {name}"}


def _clean_assistant_msg(msg: dict) -> dict:
    """Keep only the standard fields when feeding the reply back to the API."""
    out = {"role": "assistant"}
    if msg.get("content"):
        out["content"] = msg["content"]
    if msg.get("tool_calls"):
        out["tool_calls"] = msg["tool_calls"]
    if "content" not in out and "tool_calls" not in out:
        out["content"] = ""
    return out
