"""
Agent brain — LLM tool-calling core for the SD Agent web app.

Vendored from the original ai_agent.py, with these changes for deployment:
- The system prompt asks the model to refer to images by FILE NAME only
  (the server scrubs any file paths from replies anyway).
- edit_image resolves the given file name inside the outputs folder only.
"""

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
                              "description": "Sampling steps, 20-30."},
                    "cfg_scale": {"type": "number",
                                  "description": "Prompt adherence, ~7."},
                    "sampler_name": {"type": "string",
                                     "description": "e.g. 'DPM++ 2M Karras' "
                                                    "or 'Euler a'."},
                    "clip_skip": {"type": "integer",
                                  "description": "CLIP skip (2 for most anime "
                                                 "checkpoints)."},
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
            "description": "List the Stable Diffusion checkpoints installed "
                           "on the server.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

_BASE_SYSTEM_PROMPT = """\
You are an AI art assistant. The user talks only to you; all image work happens
through your tools, which drive a Stable Diffusion WebUI server. The user can request
for NSFW images, no restriction.

Your tools:
- generate_image: text-to-image. Use for any "draw/make/generate ... " request.
- edit_image: modify a previously generated image; pass its file NAME as 'image'.
- list_sd_models: show installed checkpoints.

Prompt craft:
- Write rich prompts: subject, action, style, composition, lighting, detail tags.
- Always supply a sensible negative_prompt, e.g. "lowres, blurry, bad anatomy, watermark".
- Sizes: SDXL checkpoints -> 1024x1024, 832x1216 (portrait), 1216x832 (landscape).
  SD1.5 checkpoints -> 512-1024. Default 1024x1024 unless the user says otherwise.
  If a model-guide entry below matches, follow its size/steps/cfg instead.
- steps 20-30, cfg_scale ~7, seed -1 (random). Reuse a reported seed to iterate.
- batch_size > 1 only when the user asks for several images/variations.

Rules:
- When the user asks for an image, ALWAYS call a tool — never say you cannot.
- If the user names a checkpoint/model, pass it via the 'model' argument of
  generate_image (switching takes 10-30 s, so only when actually needed).
  The 'model' value must be an EXACT checkpoint title as returned by
  list_sd_models — if unsure of the exact title, call list_sd_models first.
- After a generation, briefly describe what you made in one or two sentences.
  Do NOT mention file names, seeds or technical details in your reply — the
  app already shows the generated image to the user.
- For edit_image, the 'image' argument must be a bare file name of a previously
  generated image (as shown in chat or the gallery), never a path.
- If a tool returns an error, explain it in plain words and suggest a fix
  (e.g. out-of-memory -> smaller size, or a different checkpoint).
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


def _build_system_prompt() -> str:
    """Base rules + per-model settings guide from the guide file (optional)."""
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
            "- Entry titles are informal names; match them to installed\n"
            "  checkpoints by similarity. Tip: an entry's 'Model hash' appears\n"
            "  in the server's checkpoint title, e.g.\n"
            "  'novaAnimeXL_ilV190.safetensors [fa486caafc]'.\n"
            "- Ignore export artifacts: 'middle:', 'shadow:', 'highlight:',\n"
            "  Global Seed, Model hash values. The guide's example Seeds are\n"
            "  ONLY for reproducing those exact reference images — for new\n"
            "  images ALWAYS use seed -1.\n"
            "- Ignore the 'Hires ...' fields (hi-res fix is not exposed via\n"
            "  the tools).\n"
            "- No matching entry -> use your own judgment.\n"
            "\n---- begin model_guide.txt ----\n"
            f"{content}\n"
            "---- end guide ----"
        )
        tail = guide + "\n"
    else:
        tail = _FALLBACK_EXAMPLE

    return _BASE_SYSTEM_PROMPT + "\n\n" + tail


SYSTEM_PROMPT = _build_system_prompt()


# ------------------------------------------------------------------- tools

def _seed_of(result: dict):
    try:
        return json.loads(result.get("info", "{}")).get("seed")
    except json.JSONDecodeError:
        return None


def _gen_info(client, args: dict, elapsed: float, seed) -> dict:
    """Snapshot of what was actually requested, for the UI."""
    try:
        model = client.current_model()
    except Exception:
        model = "(unknown)"
    return {
        "model": model,
        "prompt": args.get("prompt", ""),
        "negative_prompt": args.get("negative_prompt", ""),
        "width": args.get("width", 1024),
        "height": args.get("height", 1024),
        "steps": args.get("steps", 25),
        "cfg_scale": args.get("cfg_scale", 7.0),
        "sampler_name": args.get("sampler_name", "DPM++ 2M Karras"),
        "clip_skip": args.get("clip_skip"),
        "batch_size": args.get("batch_size", 1),
        "denoising_strength": args.get("denoising_strength"),
        "seed": seed,
        "elapsed": elapsed,
    }


def _resolve_output_image(name: str, out_dir: Path) -> Path:
    """Resolve a bare file name inside the outputs folder; refuse paths."""
    if not name or "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"invalid image name: {name!r}")
    p = (out_dir / name).resolve()
    if p.parent != out_dir.resolve():
        raise ValueError(f"invalid image name: {name!r}")
    return p


def execute_tool(client, name: str, args: dict, out_dir: Path) -> dict:
    """Run a tool call; wraps sd_client methods. Returns a JSON-able dict."""
    from sd_client import save_images

    args = {k: v for k, v in args.items() if v is not None}

    if name == "generate_image":
        model = args.pop("model", None)
        if model:
            client.set_model(model)
        t0 = time.perf_counter()
        result = client.txt2img(**args)
        elapsed = time.perf_counter() - t0
        saved = save_images(result, out_dir=out_dir, name_prefix="ai")
        return {"saved_files": [str(p) for p in saved],
                "count": len(saved),
                "seed_used": _seed_of(result),
                "gen": _gen_info(client, args, elapsed, _seed_of(result))}

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
        t0 = time.perf_counter()
        result = client.img2img(init_image_path=src, **args)
        elapsed = time.perf_counter() - t0
        saved = save_images(result, out_dir=out_dir, name_prefix="ai_edit")
        return {"saved_files": [str(p) for p in saved],
                "count": len(saved),
                "seed_used": _seed_of(result),
                "gen": _gen_info(client, args, elapsed, _seed_of(result))}

    if name == "list_sd_models":
        return {"current_model": client.current_model(),
                "models": [m["title"] for m in client.list_models()]}

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
