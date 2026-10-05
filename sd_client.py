"""
Stable Diffusion WebUI API client (vendored, standalone) — Forge Neo edition.

Targets **sd-webui-forge-neo** (the modular successor to SD WebUI Forge). The
Neo build still speaks the A1111 API on the classic paths, but it differs from
legacy Forge in ways that matter to a client:

  * **Checkpoints are not necessarily monolithic.** Modular DiT families
    (flux, qwen, anima, krea, wan, zit/z-image, lumina, ernie, klein, pid)
    load a diffusion model plus *companion* files — a text encoder and a VAE
    that live in their own folders and are attached per architecture through
    `forge_additional_modules_<arch>`, with the VAE bit-width chosen by
    `forge_unet_storage_dtype_<arch>`.
  * **Karras is no longer a sampler.** Samplers and schedule types are
    separate lists now, so a legacy "DPM++ 2M Karras" is sampler "DPM++ 2M"
    plus schedule type "Karras". Legacy names are still accepted by the
    server, but the schedule part is dropped silently.
  * **Distilled flow models** take their real guidance from
    `distilled_cfg_scale`; `cfg_scale` is only the distilled-model override.
  * **The payload is schema-validated.** Keys the Pydantic model does not
    know are dropped without a word (that is what quietly killed `clip_skip`
    in the payload — it now lives in the `CLIP_stop_at_last_layers` option),
    while an unknown key inside `override_settings` is a hard 500.

Everything below is derived from the live server rather than assumed: the
payload allowlist comes from `/openapi.json`, the sampler and schedule lists
from their own endpoints, and the per-architecture defaults from the
`<arch>_t2i_*` options. Static fallbacks keep the client usable when the WebUI
is unreachable (the app must still start and show an error bubble).

Server must run with --api --listen. Requires: requests.
"""

import base64
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

DEFAULT_URL = "http://100.93.220.68:7860"

# --------------------------------------------------------------------------
# Architecture registry (Forge Neo)
#
# One entry per Neo model family. Values mirror the server's own `<arch>_t2i_*`
# defaults as of neo-2.29.2 and are only used when /sdapi/v1/options is
# unreachable — `SDClient.arch_profile()` prefers the live numbers.
#   sampler/scheduler : schedule type in API spelling (see SCHEDULER_ALIASES)
#   cfg/steps         : starting point; the bands below decide the real range
#   dcfg              : distilled_cfg_scale, only for families that use it
#   distilled         : guidance-distilled -> low CFG, few steps
#   video             : family is a video diffusion model (Wan)
# --------------------------------------------------------------------------
NEO_ARCHES = ("sd", "xl", "flux", "klein", "qwen", "lumina", "zit", "wan",
              "anima", "ernie", "pid", "krea")

ARCH_LABELS = {
    "sd": "SD 1.5 / 2.1", "xl": "SDXL / Illustrious", "flux": "Flux",
    "klein": "Klein (Flux.2)", "qwen": "Qwen Image", "lumina": "Lumina 2",
    "zit": "Z-Image", "wan": "Wan (video)", "anima": "Anima",
    "ernie": "ERNIE", "pid": "PID", "krea": "Krea 2",
}

ARCH_PROFILES = {
    "sd":     dict(sampler="Euler a", scheduler="automatic", cfg=6.0, steps=32,
                   dcfg=None, distilled=False, video=False, size=512),
    "xl":     dict(sampler="Euler a", scheduler="automatic", cfg=4.5, steps=24,
                   dcfg=None, distilled=False, video=False, size=1024),
    "flux":   dict(sampler="Euler", scheduler="beta", cfg=1.0, steps=20,
                   dcfg=3.0, distilled=True, video=False, size=1024),
    "klein":  dict(sampler="Euler", scheduler="beta", cfg=1.0, steps=4,
                   dcfg=None, distilled=True, video=False, size=1024),
    "qwen":   dict(sampler="LCM", scheduler="normal", cfg=1.0, steps=8,
                   dcfg=None, distilled=True, video=False, size=1328),
    "lumina": dict(sampler="Res Multistep", scheduler="simple", cfg=4.0,
                   steps=32, dcfg=None, distilled=False, video=False,
                   size=1024),
    "zit":    dict(sampler="Euler", scheduler="beta", cfg=1.0, steps=9,
                   dcfg=None, distilled=True, video=False, size=1024),
    "wan":    dict(sampler="Euler", scheduler="simple", cfg=1.0, steps=4,
                   dcfg=None, distilled=True, video=True, size=832),
    "anima":  dict(sampler="ER SDE", scheduler="beta", cfg=4.0, steps=32,
                   dcfg=None, distilled=False, video=False, size=1024),
    "ernie":  dict(sampler="Euler", scheduler="simple", cfg=1.0, steps=8,
                   dcfg=None, distilled=True, video=False, size=1024),
    "pid":    dict(sampler="LCM", scheduler="simple", cfg=1.0, steps=4,
                   dcfg=None, distilled=True, video=False, size=1024),
    "krea":   dict(sampler="Euler", scheduler="simple", cfg=1.0, steps=8,
                   dcfg=None, distilled=False, video=False, size=1024),
}

# CFG / step bands. Low-guidance (distilled) flow models are ruined by ordinary
# guidance — CFG 7 on Flux Schnell is grey soup — so their band is *enforced*.
# The standard band is only *allowed* for the non-distilled DiTs (Anima Base,
# Krea Raw, Lumina): a value inside it is used as given, and a value outside it
# is still honoured, because the server's own tuned default for those families
# can sit outside the band. Legacy sd/xl are not banded at all — that is what
# the model guide and every existing recipe use.
CFG_BANDS = {"distilled": (1.0, 2.0), "standard": (4.0, 6.0)}
STEP_BANDS = {"distilled": (4, 15), "standard": (20, 35)}
#: families where an out-of-band CFG/step value is corrected, not just allowed
ENFORCE_BANDS = {"flux", "klein", "qwen", "zit", "wan", "ernie", "pid"}
#: legacy families — no distilled_cfg_scale, no bands
LEGACY_ARCHES = ("sd", "xl")

#: families whose distilled guidance is real (Neo presets.py: DISTILL) — the
#: `<arch>_t2i_dcfg` option of every OTHER family is the Shift slider under a
#: confusing option name, and sending its value as distilled_cfg_scale would
#: be meaningless. Mirrors neo_detect.DISTILLED_ARCHES.
DISTILLED_ARCHES = {"flux"}

#: `forge_unet_storage_dtype_<arch>` values, as advertised by the Neo UI's
#: "Diffusion in Low Bits" dropdown. Newer builds add more (nvfp4, int4, …);
#: add them here (and to LOW_BITS_ALIASES) when the server offers them.
LOW_BITS_CHOICES = ("Automatic", "Automatic (fp16 LoRA)", "float8-e4m3fn",
                    "float8-e4m3fn (fp16 LoRA)", "float8-e5m2",
                    "float8-e5m2 (fp16 LoRA)")
LOW_BITS_ALIASES = {
    "": "Automatic", "auto": "Automatic", "automatic": "Automatic",
    "none": "Automatic", "default": "Automatic", "off": "Automatic",
    "fp16": "Automatic (fp16 LoRA)", "auto_fp16": "Automatic (fp16 LoRA)",
    "automatic (fp16 lora)": "Automatic (fp16 LoRA)",
    "fp8": "float8-e4m3fn", "fp8_e4m3fn": "float8-e4m3fn",
    "fp8-e4m3fn": "float8-e4m3fn", "float8_e4m3fn": "float8-e4m3fn",
    "e4m3fn": "float8-e4m3fn", "float8-e4m3fn": "float8-e4m3fn",
    "fp8_e5m2": "float8-e5m2", "fp8-e5m2": "float8-e5m2",
    "float8_e5m2": "float8-e5m2", "e5m2": "float8-e5m2",
    "float8-e5m2": "float8-e5m2",
}

#: schedule types in the spelling /sdapi/v1/schedulers reports. The UI shows
#: prettier labels ("Simple", "FlowMatchEulerDiscrete", "Flux2"), and the API
#: wants the internal name — hence the alias table.
SCHEDULER_CHOICES = ("automatic", "karras", "exponential", "polyexponential",
                     "normal", "simple", "uniform", "sgm_uniform",
                     "linear_quadratic", "kl_optimal", "ddim",
                     "align_your_steps", "beta", "turbo", "bong_tangent",
                     "flow_match", "flux2")
SCHEDULER_ALIASES = {
    "flowmatcheulerdiscrete": "flow_match",
    "flowmatcheuler": "flow_match",
    "flowmatch": "flow_match",
    "flux2": "flux2",
}

SAMPLER_CHOICES = ("DPM++ 2M", "DPM++ SDE", "DPM++ 2M SDE", "DPM++ 3M SDE",
                   "DPM++ 2s a RF", "Euler a", "Euler", "ER SDE", "LCM", "LMS",
                   "Heun", "DPM2", "Res Multistep", "Kohaku LoNyu Yog",
                   "Restart", "UniPC", "DDIM", "PLMS", "DPM++ 2M CFG++",
                   "Euler a CFG++", "Euler CFG++")

#: request keys the Neo payload schema accepts (union of the txt2img and
#: img2img models, minus the img2img-only ones). Used to drop anything the
#: server would ignore anyway, so a stale caller cannot smuggle dead keys
#: into a request. Refreshed from /openapi.json when the server is reachable.
PAYLOAD_KEYS = (
    "alwayson_scripts", "batch_size", "cfg_scale", "comments",
    "disable_extra_networks", "distilled_cfg_scale", "do_not_save_grid",
    "do_not_save_samples", "enable_hr", "eta", "firstpass_image",
    "firstphase_height", "firstphase_width", "force_task_id", "height",
    "hr_additional_modules", "hr_cfg", "hr_checkpoint_name", "hr_distilled_cfg",
    "hr_negative_prompt", "hr_prompt", "hr_resize_x", "hr_resize_y",
    "hr_sampler_name", "hr_scale", "hr_scheduler", "hr_second_pass_steps",
    "hr_upscaler", "infotext", "initial_noise_multiplier", "n_iter",
    "negative_prompt", "override_settings", "override_settings_restore_afterwards",
    "prompt", "refiner_cfg", "refiner_checkpoint", "refiner_switch_at",
    "restore_faces", "s_churn", "s_min_uncond", "s_noise", "s_tmax", "s_tmin",
    "sampler_index", "sampler_name", "save_images", "scheduler", "script_args",
    "script_name", "seed", "seed_resize_from_h", "seed_resize_from_w",
    "send_images", "steps", "styles", "subseed", "subseed_strength", "tiling",
    "width",
)
IMG2IMG_KEYS = PAYLOAD_KEYS + (
    "denoising_strength", "image_cfg_scale", "include_init_images",
    "inpaint_full_res", "inpaint_full_res_padding", "inpainting_fill",
    "inpainting_mask_invert", "init_images", "init_latent", "latent_mask",
    "mask", "mask_blur", "mask_round", "resize_mode",
)

#: legacy payload key -> the option that replaced it in Forge Neo
LEGACY_KEY_MAP = {"clip_skip": "CLIP_stop_at_last_layers"}

#: VAE file extensions the legacy monolithic `sd_vae` option accepts; a
#: .safetensors VAE belongs to a modular pipeline and is attached as a
#: companion module instead.
LEGACY_VAE_EXT = (".ckpt", ".pt", ".pth", ".bin")

_CAP_TTL = 300.0          # capability/option cache lifetime, seconds


def _model_key(title: str) -> str:
    """Identity of a checkpoint for de-duplication.

    The same file shows up under several spellings: the model list carries a
    hash suffix ('x.safetensors [1d0c21959c]'), the per-architecture dropdowns
    often drop the extension ('x'), and paths may be absolute. Compare on the
    bare name so the picker shows one row per model.
    """
    base = str(title or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = re.sub(r"\s*\[[0-9a-f]{6,}\]\s*$", "", base, flags=re.I)
    base = re.sub(r"\.(safetensors|ckpt|pt|pth|bin)$", "", base, flags=re.I)
    return base.strip().lower()


class SDWebUIError(Exception):
    """Raised when the WebUI API returns an error or answers unexpectedly."""


# --------------------------------------------------------------- name logic

def _slug(name: str) -> str:
    """Loose key for a sampler/schedule name: case and separators don't matter."""
    return re.sub(r"[\s_\-]+", "", str(name or "")).lower()


def _slug_map(names) -> dict:
    return {_slug(n): n for n in names if n}


def guess_arch(title: str) -> str:
    """Best-effort architecture for a checkpoint title / file name.

    Byte-level detection (neo_detect.guess_file) is the authoritative route
    and is tried first wherever the caller has the file; this name-based
    fallback exists for the case where it doesn't (a remote WebUI, an
    unloadable header, GGUF). Modular DiTs are recognised by family-name
    patterns — including the ones that hide it, like Nova's "AM" suffix for
    Anima fine-tunes.
    """
    from neo_detect import guess_from_name
    return guess_from_name(title)


def is_distilled(title: str) -> bool:
    """True when the checkpoint name says it is a guidance-distilled model.

    Flux.1 *dev* is not distilled, so the architecture alone is never enough —
    the name has to say so (schnell, turbo, lightning, lcm, …).
    """
    from neo_detect import name_is_distilled
    return name_is_distilled(title)


def split_sampler(name: str) -> tuple[str, str | None]:
    """Split a legacy combined sampler name into (sampler, schedule type).

    "DPM++ 2M Karras" -> ("DPM++ 2M", "Karras");  "Euler a" -> ("Euler a", None).
    Longest schedule name first so "DPM++ 2M Karras" does not end up matching
    a shorter suffix.
    """
    raw = str(name or "").strip()
    if not raw:
        return "", None
    lowered = raw.lower()
    for sched in sorted(SCHEDULER_CHOICES, key=len, reverse=True):
        label = _display_scheduler(sched)
        for cand in (label, sched):
            c = cand.lower()
            if lowered.endswith(" " + c):
                return raw[: -len(cand)].strip(), label
    return raw, None


def _display_scheduler(api_name: str) -> str:
    """UI label for a schedule type ("flow_match" -> "FlowMatchEulerDiscrete")."""
    return {"flow_match": "FlowMatchEulerDiscrete", "flux2": "Flux2",
            "sgm_uniform": "SGM Uniform", "linear_quadratic": "Linear Quadratic",
            "kl_optimal": "KL Optimal", "align_your_steps": "Align Your Steps",
            "bong_tangent": "Bong Tangent"}.get(
                api_name, api_name.replace("_", " ").title())


def norm_low_bits(value) -> str:
    """Map a low-bit request onto a value the server's dropdown accepts."""
    raw = str(value if value is not None else "").strip()
    if not raw:
        return "Automatic"
    hit = LOW_BITS_ALIASES.get(_slug(raw))
    if hit:
        return hit
    for choice in LOW_BITS_CHOICES:                    # already exact
        if _slug(choice) == _slug(raw):
            return choice
    raise SDWebUIError(
        f"low-bit setting {raw!r} is not available on this Forge Neo build. "
        f"Supported: {', '.join(LOW_BITS_CHOICES)}. Add newer values to "
        f"LOW_BITS_CHOICES once the server offers them.")


def _is_legacy_vae(name: str) -> bool:
    return str(name or "").lower().endswith(LEGACY_VAE_EXT)


# ------------------------------------------------------------- capabilities

class _Caps:
    """Cached view of what the connected WebUI actually supports."""

    def __init__(self):
        self.lock = threading.Lock()
        self.ts = 0.0
        self.payload_keys: set[str] = set(PAYLOAD_KEYS)
        self.img2img_keys: set[str] = set(IMG2IMG_KEYS)
        self.paths: set[str] = set()
        self.samplers: list[str] = list(SAMPLER_CHOICES)
        self.schedulers: list[str] = list(SCHEDULER_CHOICES)
        self.low_bits: list[str] = list(LOW_BITS_CHOICES)
        self.text_encoders: list[str] = []
        self.vaes: list[str] = []
        self.other_modules: list[str] = []
        self.option_keys: set[str] = set()
        self.arches: list[str] = list(NEO_ARCHES)
        self.scripts: set[str] = set()
        self.version: str = ""
        self.ok = False


def _seed_of(result: dict):
    try:
        return json.loads(result.get("info", "{}")).get("seed")
    except (json.JSONDecodeError, TypeError, AttributeError):
        return None


def save_images(result: dict, out_dir: str | Path = ".",
                name_prefix: str = "sd") -> list[Path]:
    """Decode base64 images from a txt2img/img2img response; return paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    seed = _seed_of(result)
    seed = "0" if seed is None else str(seed)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    saved = []
    for i, b64 in enumerate(result.get("images") or []):
        path = out_dir / f"{name_prefix}_{timestamp}_{seed}_{i}.png"
        path.write_bytes(base64.b64decode(b64))
        saved.append(path)
    return saved


# ------------------------------------------------------------------- client

class SDClient:
    """A1111-compatible client for a Forge Neo WebUI."""

    def __init__(self, base_url: str = DEFAULT_URL, timeout: int = 600):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._caps = _Caps()
        self._opt_ts = 0.0
        self._opt_lock = threading.Lock()
        self._opts: dict = {}
        self._models_ts = 0.0
        self._models: list[dict] = []
        self.known_arches: dict[str, str] = {}   # user-pinned title -> arch

    # ------------------------------------------------------------- plumbing

    def _post(self, endpoint: str, payload: dict) -> dict:
        resp = requests.post(
            f"{self.base_url}{endpoint}",
            json=payload,
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise SDWebUIError(
                f"{endpoint} failed ({resp.status_code}): {resp.text[:500]}")
        try:
            return resp.json()
        except ValueError:
            return {}

    def _get(self, endpoint: str, timeout: int | None = None):
        resp = requests.get(f"{self.base_url}{endpoint}",
                            timeout=timeout or 30)
        if resp.status_code != 200:
            raise SDWebUIError(
                f"{endpoint} failed ({resp.status_code}): {resp.text[:500]}")
        return resp.json()

    def _get_soft(self, endpoint: str, timeout: int = 20, default=None):
        """GET that never raises — capability probing must not break a turn."""
        try:
            return self._get(endpoint, timeout=timeout)
        except Exception:                                 # noqa: BLE001
            return default

    # ----------------------------------------------------------------- info

    def capabilities(self, force: bool = False) -> _Caps:
        """What this server supports: payload schema, samplers, schedules,
        companion files, architecture list. Cached; never raises."""
        caps = self._caps
        with caps.lock:
            if not force and caps.ok and time.time() - caps.ts < _CAP_TTL:
                return caps
        # one pass, all soft — a partially reachable server still yields a
        # usable (smaller) capability set
        spec = self._get_soft("/openapi.json", timeout=60, default={}) or {}
        if spec:
            comps = (spec.get("components") or {}).get("schemas") or {}
            t2i = set((comps.get("StableDiffusionProcessingTxt2Img") or {})
                      .get("properties") or {})
            i2i = set((comps.get("StableDiffusionProcessingImg2Img") or {})
                      .get("properties") or {})
            if t2i or i2i:
                caps.payload_keys = t2i | set(PAYLOAD_KEYS)
                caps.img2img_keys = i2i | set(IMG2IMG_KEYS)
            caps.paths = set(spec.get("paths") or {})
        samplers = self._get_soft("/sdapi/v1/samplers", default=[]) or []
        if samplers:
            caps.samplers = [s.get("name") or s.get("label") or ""
                             for s in samplers if isinstance(s, dict)]
            caps.samplers = [s for s in caps.samplers if s]
        scheds = self._get_soft("/sdapi/v1/schedulers", default=[]) or []
        if scheds:
            caps.schedulers = [s.get("name") or s.get("label") or ""
                               for s in scheds if isinstance(s, dict)]
            caps.schedulers = [s for s in caps.schedulers if s]
        mods = self._get_soft("/sdapi/v1/sd-modules", default=[]) or []
        te, vae, other = [], [], []
        for m in mods if isinstance(mods, list) else []:
            name = str(m.get("model_name") or "").strip()
            path = str(m.get("filename") or "")
            if not name:
                continue
            parts = path.replace("\\", "/").split("/")
            folder = parts[-2].lower() if len(parts) > 1 else ""
            if "text_encoder" in folder:
                te.append(name)
            elif "vae" in folder:
                vae.append(name)
            else:
                other.append(name)
        caps.text_encoders, caps.vaes, caps.other_modules = te, vae, other
        opts = self._get_soft("/sdapi/v1/options", default={}) or {}
        if isinstance(opts, dict) and opts:
            caps.option_keys = set(opts)
            arches = {k[len("forge_checkpoint_"):]
                      for k in opts if k.startswith("forge_checkpoint_")}
            if arches:
                caps.arches = [a for a in NEO_ARCHES if a in arches] \
                    or sorted(arches)
            caps.version = str(opts.get("VERSION_UID") or "")
        scripts = self._get_soft("/sdapi/v1/scripts", default={}) or {}
        txt = scripts.get("txt2img") if isinstance(scripts, dict) else None
        if isinstance(txt, dict):
            caps.scripts = set(txt)
        elif isinstance(txt, list):
            caps.scripts = set(txt)
        with caps.lock:
            caps.ts = time.time()
            caps.ok = True
        return caps

    def version(self) -> str:
        """WebUI version string from the last generation's infotext, if known."""
        return self._get_soft("/sdapi/v1/options",
                              default={}).get("VERSION_UID", "") or ""

    def options(self, force: bool = False) -> dict:
        """Current WebUI options (762 keys on Neo) — the source of truth for
        which per-architecture settings exist. Cached briefly."""
        with self._opt_lock:
            if not force and self._opts and time.time() - self._opt_ts < 3.0:
                return self._opts
        opts = self._get_soft("/sdapi/v1/options", default=None)
        if not isinstance(opts, dict) or not opts:
            return self._opts
        with self._opt_lock:
            self._opts = opts
            self._opt_ts = time.time()
        return opts

    def _option_keys(self) -> set[str]:
        keys = set(self.options().keys())
        return keys or set(self.capabilities().option_keys)

    def set_options(self, updates: dict, validate: bool = True) -> dict:
        """POST /sdapi/v1/options with a batch of settings.

        One request for the whole batch: each change fires the server's own
        on-change handler, so several small posts mean several model reloads.
        Unknown keys are refused — the WebUI raises on them.
        """
        clean = {k: v for k, v in (updates or {}).items()
                 if v is not None and k}
        if not clean:
            return {}
        if validate:
            known = self._option_keys()
            unknown = [k for k in clean if known and k not in known]
            if unknown:
                raise SDWebUIError(
                    "this Forge Neo server has no option(s): "
                    + ", ".join(sorted(unknown)))
        self._post("/sdapi/v1/options", clean)
        with self._opt_lock:                     # our cache is stale now
            self._opts = {}
            self._opt_ts = 0.0
        self._models_ts = 0.0
        return clean

    def list_models(self, force: bool = False) -> list[dict]:
        """Installed checkpoints, annotated with their architecture.

        Architecture comes from the file's own bytes when the WebUI runs on
        the same machine (neo_detect reads the safetensors header and applies
        Neo's own classifier — the name is not trustworthy: Nova's "AM"
        fine-tunes are Anima models), else from name patterns. Anything the
        user pinned in `known_arches` wins over both.
        """
        if not force and self._models and time.time() - self._models_ts < 60:
            return self._models
        raw = self._get_soft("/sdapi/v1/sd-models", timeout=25, default=[]) or []
        opts = self.options()
        by_key: dict[str, dict] = {}
        out: list[dict] = []
        for m in raw if isinstance(raw, list) else []:
            title = str(m.get("title") or "").strip()
            if not title:
                continue
            path = str(m.get("filename") or "")
            arch = self._detect_arch(title, path)
            row = {"title": title, "arch": arch,
                   "hash": m.get("hash") or m.get("sha256") or "",
                   "path": path}
            by_key[_model_key(title)] = row
            out.append(row)
        # the per-architecture dropdowns are where modular models live; merge
        # rather than append, so one model is one row even when the dropdown
        # spells it without the extension
        for arch in self.capabilities().arches:
            title = str(opts.get(f"forge_checkpoint_{arch}") or "").strip()
            if not title or title.lower() in ("none", "automatic"):
                continue
            hit = by_key.get(_model_key(title))
            if hit is not None:
                if hit["arch"] == "sd" and arch != "sd":
                    hit["arch"] = arch        # it lives in a modular slot
                continue
            row = {"title": title, "arch": self._detect_arch(title, ""),
                   "hash": "",
                   "from": f"forge_checkpoint_{arch}"}
            by_key[_model_key(title)] = row
            out.append(row)
        with self._opt_lock:
            self._models = out
            self._models_ts = time.time()
        return out

    def _detect_arch(self, title: str, path: str) -> str:
        """User pin > file bytes (Neo's own classifier) > name patterns."""
        pinned = (self.known_arches or {}).get(_model_key(title))
        if pinned:
            return str(pinned).lower()
        if path:
            from neo_detect import guess_file
            hit = guess_file(path)
            if hit:
                return hit
        return guess_arch(title)

    def set_known_arches(self, mapping: dict | None):
        """Pin title -> architecture overrides (user-confirmed mappings)."""
        self.known_arches = {
            _model_key(k): str(v).lower()
            for k, v in (mapping or {}).items() if k and v
        }
        self._models_ts = 0.0

    def list_samplers(self) -> list[str]:
        return list(self.capabilities().samplers)

    def list_schedulers(self) -> list[str]:
        return list(self.capabilities().schedulers)

    def list_low_bits(self) -> list[str]:
        return list(self.capabilities().low_bits or LOW_BITS_CHOICES)

    def list_vaes(self) -> list[str]:
        """VAE files the server can see ('Automatic' = the checkpoint's own)."""
        caps = self.capabilities()
        vaes = ["Automatic"] + [v for v in caps.vaes + caps.other_modules
                                if _is_legacy_vae(v)]
        vaes += [v for v in caps.vaes if not _is_legacy_vae(v)]
        seen, out = set(), []
        for v in vaes:
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out

    def list_text_encoders(self) -> list[str]:
        return list(self.capabilities().text_encoders)

    def companion_files(self) -> dict:
        caps = self.capabilities()
        return {"text_encoder": list(caps.text_encoders),
                "vae": list(caps.vaes),
                "modules": list(caps.other_modules)}

    def current_model(self) -> str:
        return str(self.options().get("sd_model_checkpoint") or "")

    def current_arch(self) -> str:
        """Architecture of the loaded checkpoint.

        The file's own bytes win (Neo's classifier via neo_detect); the
        per-architecture slot that holds the title is the next best hint, and
        the name patterns come last.
        """
        title = self.current_model() or ""
        row = next((m for m in self.list_models()
                    if _model_key(m.get("title") or "") == _model_key(title)),
                   None)
        arch = self._detect_arch(title, (row or {}).get("path", ""))
        if arch != "sd":
            return arch
        opts = self.options()
        for a in self.capabilities().arches:
            if a == "sd":
                continue
            slot = str(opts.get(f"forge_checkpoint_{a}") or "").strip()
            if slot and _model_key(slot) == _model_key(title):
                return a
        preset = str(opts.get("forge_preset") or "").lower()
        if preset in self.capabilities().arches and preset != "sd":
            return preset
        return arch

    def progress(self) -> dict:
        return self._get("/sdapi/v1/progress?skip_current_image=false")

    def interrupt(self):
        """Stop the running job on the WebUI side (used by "stop")."""
        try:
            self._post("/sdapi/v1/interrupt", {})
        except Exception:                                 # noqa: BLE001
            pass                            # best effort — never raises

    def wait_until_ready(self, poll_seconds: float = 2.0, max_wait: int = 300):
        """Block until the server isn't busy."""
        waited = 0.0
        while waited < max_wait:
            p = self.progress()
            if p.get("progress", 0) == 0 and not p.get("state", {}).get("job_count"):
                return
            time.sleep(poll_seconds)
            waited += poll_seconds

    # ------------------------------------------------------------- profiles

    def arch_profile(self, arch: str | None = None) -> dict:
        """Settings profile for an architecture.

        Prefers the server's own `<arch>_t2i_*` defaults (so it follows a
        Neo upgrade), falls back to ARCH_PROFILES. Adds the computed CFG/step
        band, the `band` name and a human label.
        """
        arch = (arch or "sd").lower()
        prof = dict(ARCH_PROFILES.get(arch) or ARCH_PROFILES["sd"])
        prof["arch"] = arch
        prof["label"] = ARCH_LABELS.get(arch, arch)
        opts = self.options()
        got = False
        live_keys = (("sampler", "sampler"), ("scheduler", "scheduler"),
                     ("cfg", "cfg"), ("steps", "steps"))
        if arch in DISTILLED_ARCHES:
            # only a real distilled family has a distilled CFG slider; the
            # other families' `<arch>_t2i_dcfg` option is the Shift slider
            # under a confusing key name — never send it
            live_keys += (("dcfg", "dcfg"),)
        for key, field in live_keys:
            val = opts.get(f"{arch}_t2i_{key}")
            if val is not None and val != "":
                if field in ("cfg", "dcfg"):
                    try:
                        prof[field] = float(val)
                    except (TypeError, ValueError):
                        continue
                elif field == "steps":
                    try:
                        prof[field] = int(float(val))
                    except (TypeError, ValueError):
                        continue
                else:
                    prof[field] = str(val)
                got = True
        prof["from_server"] = got
        lo, hi = CFG_BANDS["distilled" if prof["distilled"] else "standard"]
        prof["cfg_range"] = [lo, hi]
        slo, shi = STEP_BANDS["distilled" if prof["distilled"] else "standard"]
        prof["step_range"] = [slo, shi]
        prof["enforce"] = arch in ENFORCE_BANDS
        return prof

    def arch_for(self, model: str = "", arch: str | None = None) -> str:
        """Architecture of `model` (or of the loaded one when empty).

        A caller that is about to generate with a specific checkpoint must get
        *that* file's architecture, not whatever happens to be loaded — which
        is the normal case, because a model switch and the generation that
        follows it are one step in the same tool call. Resolution order:
        explicit argument > user pin > the file's own bytes > name patterns.
        """
        if arch:
            return str(arch).lower()
        if not model:
            return self.current_arch()
        title = str(model)
        row = next((m for m in self.list_models()
                    if _model_key(m.get("title") or "") == _model_key(title)),
                   None)
        return self._detect_arch(title, (row or {}).get("path", ""))

    def architecture_summary(self) -> list[dict]:
        """One row per architecture the server knows — for the UI."""
        rows = []
        for arch in self.capabilities().arches:
            prof = self.arch_profile(arch)
            rows.append({"arch": arch, "label": prof["label"],
                         "cfg": prof["cfg"], "steps": prof["steps"],
                         "sampler": prof["sampler"],
                         "scheduler": prof["scheduler"],
                         "distilled": prof["distilled"],
                         "video": prof["video"],
                         "checkpoint": str(self.options().get(
                             f"forge_checkpoint_{arch}") or ""),
                         "modules": list(self.options().get(
                             f"forge_additional_modules_{arch}") or []),
                         "low_bits": str(self.options().get(
                             f"forge_unet_storage_dtype_{arch}") or "")})
        return rows

    # ------------------------------------------------- model + configuration

    def set_model(self, model_title: str, arch: str | None = None,
                  text_encoder: str | None = None,
                  sd_vae: str | None = None, low_bits: str | None = None,
                  wait: bool = True) -> dict:
        """Switch checkpoint, attaching the companion files it needs.

        The architecture is detected from the checkpoint's own bytes (Neo's
        classifier, applied to the safetensors header) — not from its name,
        which lies (Nova's "AM" fine-tunes are Anima models).

        Companion modules go out as ONE options write together with the
        checkpoint: Neo reads the *generic* `forge_additional_modules` when it
        builds the loading parameters, and mirrors it into the per-arch slot,
        so both are written — writing only the per-arch key (an earlier
        version's mistake) leaves the active slot empty and the model loads
        without its text encoder / VAE.

        Auto-attach: for a modular family whose slot is empty, the required
        companions (FAMILY_MODULES) are matched against the installed files
        and attached automatically, so "generate with the Anima model" just
        works. `text_encoder`/`sd_vae`/`low_bits` override that.
        """
        title = str(model_title or "").strip()
        if not title:
            raise SDWebUIError("no model given")
        # resolve to the row list_models knows (gives us the file to detect)
        row = next((m for m in self.list_models()
                    if _model_key(m["title"]) == _model_key(title)), None)
        arch = (arch or "").lower() \
            or self._detect_arch(title, (row or {}).get("path", ""))
        caps = self.capabilities()
        if arch not in caps.arches:
            raise SDWebUIError(
                f"this server has no {arch!r} architecture "
                f"(known: {', '.join(caps.arches)})")
        opts = self.options()

        updates: dict = {"forge_preset": arch}
        # slot keys hold the short name (what the Neo UI writes there);
        # sd_model_checkpoint takes the full tile, which the loader matches
        short = _model_key(title)
        if f"forge_checkpoint_{arch}" in opts:
            updates[f"forge_checkpoint_{arch}"] = short
        updates["sd_model_checkpoint"] = title

        modules, notes = self._plan_modules(
            opts, arch, row=row, text_encoder=text_encoder, sd_vae=sd_vae)
        if notes:
            updates["forge_additional_modules_" + arch] = modules
            updates["forge_additional_modules"] = modules
        else:
            updates["forge_additional_modules"] = \
                list(opts.get("forge_additional_modules_" + arch) or [])
        if low_bits is not None:
            value = norm_low_bits(low_bits)
        else:
            value = str(opts.get(f"forge_unet_storage_dtype_{arch}")
                        or "Automatic")
        if f"forge_unet_storage_dtype_{arch}" in opts:
            updates[f"forge_unet_storage_dtype_{arch}"] = value
        updates["forge_unet_storage_dtype"] = value
        if sd_vae is not None and _is_legacy_vae(sd_vae):
            updates["sd_vae"] = sd_vae

        # modules first, checkpoint last: the loading parameters snapshot must
        # already contain the companions when the checkpoint change lands
        ordered = {k: updates[k] for k in sorted(updates, key=lambda k: (
            0 if k.startswith("forge_additional_modules")
            or k.startswith("forge_unet_storage_dtype")
            or k == "forge_preset" else
            1 if k.startswith("forge_checkpoint_") else 2))}
        self.set_options(ordered)
        self._models_ts = 0.0
        if wait:
            self.wait_for_model(title)
        return {"model": self.current_model() or title, "arch": arch,
                "modules": modules, "low_bits": value,
                "notes": notes}

    def _plan_modules(self, opts: dict, arch: str, *, row: dict | None = None,
                      text_encoder=None, sd_vae=None) -> tuple[list, list]:
        """Work out the companion list for an architecture.

        Returns (modules, notes). Priority: explicit request > the arch's
        saved slot > auto-attach from the family spec > keep whatever is
        already there. Auto-attach is all-or-nothing: a partial set would load
        and then fail deep inside the pipeline.
        """
        current = [str(m) for m in (opts.get(
            f"forge_additional_modules_{arch}") or [])]
        caps = self.capabilities()
        installed: dict[str, str] = {}
        for n in caps.text_encoders:
            installed[n] = "text_encoder"
        for n in caps.vaes + caps.other_modules:
            installed.setdefault(n, "vae")

        if text_encoder is not None or (sd_vae is not None
                                        and not _is_legacy_vae(sd_vae)):
            mods = list(current)
            notes = []
            if text_encoder is not None:
                wanted = str(text_encoder).strip()
                mods = [m for m in mods if installed.get(m) != "text_encoder"]
                if wanted and wanted.lower() not in ("none", "automatic"):
                    if wanted not in installed:
                        tes = sorted(n for n, k in installed.items()
                                     if k == "text_encoder")
                        raise SDWebUIError(
                            f"text encoder {wanted!r} is not installed on "
                            f"this server (found: "
                            f"{', '.join(tes) if tes else 'nothing'})")
                    mods.insert(0, wanted)
                notes.append(f"text encoder: {wanted or 'cleared'}")
            if sd_vae is not None and not _is_legacy_vae(sd_vae):
                wanted = str(sd_vae).strip()
                mods = [m for m in mods if installed.get(m) != "vae"]
                if wanted and wanted.lower() not in ("none", "automatic"):
                    if wanted not in installed:
                        vaes = sorted(n for n, k in installed.items()
                                      if k == "vae")
                        raise SDWebUIError(
                            f"VAE {wanted!r} is not installed on this server "
                            f"(found: {', '.join(vaes) if vaes else 'nothing'})")
                    mods.insert(0, wanted)
                notes.append(f"VAE: {wanted or 'cleared'}")
            out, seen = [], set()
            for m in mods:
                if m and m not in seen:
                    seen.add(m)
                    out.append(m)
            return out, notes

        # no explicit request: the arch's own slot if it has one, else the
        # family spec (auto-attach), else nothing
        if current:
            return current, []
        from neo_detect import FAMILY_MODULES
        spec = FAMILY_MODULES.get(arch) or {}
        if not spec:
            return [], []
        modules, missing = [], []
        for kind, patterns in (("text_encoder", spec.get("text_encoder", [])),
                               ("vae", spec.get("vae", []))):
            got = sorted(n for n, k in installed.items() if k == kind
                         and any(p in n.lower() for p in patterns))
            if not got:
                missing.append(f"{kind} matching "
                               f"{' or '.join(patterns)}*")
            else:
                modules.append(got[0])
        if missing:
            raise SDWebUIError(
                f"the {arch} architecture needs companion files this server "
                f"does not have: {', '.join(missing)}. Install them into the "
                f"WebUI's models/text_encoder and models/VAE folders, or "
                f"pick them in Settings -> Image.")
        return modules, [f"auto-attached {', '.join(modules)}"]

    def configure(self, arch: str | None = None, *, text_encoder=None,
                  sd_vae=None, low_bits=None, wait: bool = True) -> dict:
        """Assign companion files / precision for an architecture without
        switching the checkpoint (this is the Settings > Image hook).

        Like set_model, the result is written to BOTH the generic
        forge_additional_modules / forge_unet_storage_dtype (what the loader
        reads) and the per-arch keys (what the UI remembers).
        """
        arch = (arch or self.current_arch()).lower()
        caps = self.capabilities()
        if arch not in caps.arches:
            raise SDWebUIError(
                f"this server has no {arch!r} architecture "
                f"(known: {', '.join(caps.arches)})")
        opts = self.options()
        updates: dict = {}
        explicit = text_encoder is not None or (
            sd_vae is not None and not _is_legacy_vae(sd_vae))
        modules, notes = self._plan_modules(opts, arch,
                                            text_encoder=text_encoder,
                                            sd_vae=sd_vae)
        if explicit or notes:
            updates[f"forge_additional_modules_{arch}"] = modules
            updates["forge_additional_modules"] = modules
        if sd_vae is not None and _is_legacy_vae(sd_vae):
            updates["sd_vae"] = sd_vae
        if low_bits is not None:
            value = norm_low_bits(low_bits)
            if f"forge_unet_storage_dtype_{arch}" in opts:
                updates[f"forge_unet_storage_dtype_{arch}"] = value
            updates["forge_unet_storage_dtype"] = value
        self.set_options(updates)
        if wait and updates:
            self.wait_until_ready(max_wait=180)
        return {"arch": arch, "modules": modules, "changed": sorted(updates),
                "notes": notes,
                "low_bits": str(opts.get(f"forge_unet_storage_dtype_{arch}")
                                or "")}

    def wait_for_model(self, title: str, max_wait: int = 120,
                       poll: float = 2.0) -> str:
        """Poll until the loaded checkpoint matches (loads take 10-30 s)."""
        deadline = time.time() + max_wait
        cur = ""
        while time.time() < deadline:
            try:
                cur = self.current_model()
            except Exception:                             # noqa: BLE001
                pass
            if cur and (title in cur or cur in title):
                return cur
            time.sleep(poll)
        return cur or title

    def refresh_models(self):
        """Ask the WebUI to rescan its model folders."""
        self._models_ts = 0.0
        self._post("/sdapi/v1/refresh-checkpoints", {})
        try:
            self._post("/sdapi/v1/refresh-vae", {})
        except SDWebUIError:
            pass
        self.capabilities(force=True)
        return self.list_models(force=True)

    # --------------------------------------------------------------- imaging

    def prepare_args(self, args: dict | None = None, *,
                     model: str | None = None,
                     arch: str | None = None,
                     kind: str = "txt2img") -> dict:
        """Turn loose generation arguments into a Neo-ready payload.

        `kind` is "txt2img" or "img2img" and decides which schema the payload
        is filtered against — img2img-only keys such as `denoising_strength`
        are dropped from a txt2img request and vice versa.

        Returns a dict with the payload plus what was decided, so callers can
        snapshot the real settings:

            {"payload": {...}, "arch": "flux", "distilled": True,
             "sampler": "Euler", "scheduler": "beta", "cfg_scale": 1.0,
             "steps": 20, "clip_skip": 2, "override_settings": {...},
             "notes": ["dropped dead key: clip_skip"]}

        The notes are for the UI, never for the LLM: silently dropping a key
        the server ignores anyway is the whole point, but a dropped *override*
        would change the image, so those are reported as warnings.
        """
        caps = self.capabilities()
        args = dict(args or {})
        args.pop("model", None)
        model = model or self.current_model() or ""
        arch = self.arch_for(model, arch)
        prof = self.arch_profile(arch)
        distilled = bool(prof["distilled"]) or is_distilled(model)
        kind = "distilled" if distilled else "standard"
        notes: list[str] = []

        # --- configuration arguments are not generation arguments ---------
        cfg_only = {k: args.pop(k) for k in
                    ("text_encoder", "sd_vae", "low_bits", "override",
                     "video", "arch") if k in args}
        if cfg_only.get("arch"):
            arch = str(cfg_only["arch"]).lower()
            prof = self.arch_profile(arch)
            distilled = bool(prof["distilled"]) or is_distilled(model)
            kind = "distilled" if distilled else "standard"
        enforce = prof["enforce"] or distilled

        # --- sampler + schedule type ---------------------------------------
        raw_sampler = str(args.pop("sampler_name", "") or "").strip()
        sampler, sched_from_sampler = split_sampler(raw_sampler)
        known = self.norm_sampler(sampler or raw_sampler)
        if raw_sampler and not known:
            notes.append(f"sampler {raw_sampler!r} is not offered by this "
                         f"server — using {prof['sampler']!r}")
            known = ""
        if sched_from_sampler and known:
            notes.append(f"{raw_sampler!r} is sampler {known!r} + schedule "
                         f"type {sched_from_sampler!r} in Forge Neo")
        sampler = known or prof["sampler"]
        scheduler = self.norm_scheduler(
            args.pop("scheduler", None) or sched_from_sampler
            or prof["scheduler"])
        # a schedule type this build doesn't have must not reach the payload:
        # the sampler stands on its own (Automatic is the safe fallback)
        avail_sched = ([s.lower() for s in caps.schedulers] if caps.schedulers
                       else list(SCHEDULER_CHOICES))
        if scheduler and scheduler not in avail_sched:
            notes.append(f"schedule type {scheduler!r} is not offered by this "
                         f"server — dropped")
            scheduler = None

        # --- steps / CFG, clamped to the architecture's band ---------------
        # The server's own `<arch>_t2i_*` default is trusted as-is — it knows
        # things this table does not (Krea Raw wants CFG 1 at 8 steps even
        # though it is a "standard" DiT). The bands only police values a caller
        # asked for, and only where a wrong number ruins the image: a distilled
        # model at CFG 7 is grey soup, a SDXL checkpoint at CFG 4.5 is fine.
        lo_s, hi_s = STEP_BANDS[kind]
        lo_c, hi_c = CFG_BANDS[kind]
        steps_given = "steps" in args
        cfg_given = "cfg_scale" in args
        steps = args.pop("steps", None)
        if steps in (None, ""):
            steps = prof["steps"]
            if is_distilled(model) and not (lo_s <= steps <= hi_s):
                # a turbo/schnell variant of a family whose default is tuned
                # for the full model — start low instead of the arch default
                steps = lo_s + (hi_s - lo_s) // 3
        else:
            try:
                steps = int(float(steps))
            except (TypeError, ValueError):
                steps = prof["steps"]
        steps = max(1, min(150, steps))
        if steps_given and not (lo_s <= steps <= hi_s) and enforce:
            clamped = max(lo_s, min(hi_s, steps))
            notes.append(f"steps {steps} -> {clamped} "
                         f"({kind} models use {lo_s}-{hi_s})")
            steps = clamped

        cfg = args.pop("cfg_scale", None)
        if cfg in (None, ""):
            cfg = prof["cfg"]
        else:
            try:
                cfg = float(cfg)
            except (TypeError, ValueError):
                cfg = float(prof["cfg"])
        cfg = max(0.0, min(30.0, cfg))
        if cfg_given and not (lo_c <= cfg <= hi_c) and enforce:
            clamped = max(lo_c, min(hi_c, cfg))
            notes.append(f"CFG {cfg} -> {clamped} "
                         f"({kind} models use {lo_c}-{hi_c})")
            cfg = clamped

        # distilled guidance: the real CFG of a distilled model. Only sent for
        # the DiT families — sd/xl advertise a value too, but an ordinary
        # sampler never reads it and sending it only muddies the infotext.
        dcfg = args.pop("distilled_cfg_scale", None)
        if dcfg in (None, "") and prof["dcfg"] is not None \
                and arch not in LEGACY_ARCHES:
            dcfg = prof["dcfg"]
        if dcfg not in (None, ""):
            try:
                dcfg = round(float(dcfg), 4)
            except (TypeError, ValueError):
                dcfg = prof["dcfg"]

        # --- clip skip lives in an option now -----------------------------
        override: dict = {}
        clip = args.pop("clip_skip", None)
        if clip not in (None, ""):
            try:
                override[LEGACY_KEY_MAP["clip_skip"]] = float(int(float(clip)))
            except (TypeError, ValueError):
                pass

        payload: dict = dict(args)
        payload.update({"sampler_name": sampler, "steps": int(steps),
                        "cfg_scale": round(float(cfg), 3)})
        if scheduler and scheduler != "automatic":
            payload["scheduler"] = scheduler
        if dcfg is not None:
            payload["distilled_cfg_scale"] = dcfg

        # extra option overrides the caller asked for, validated against the
        # server: an unknown key here is a hard 500, not a warning
        extra = cfg_only.get("override")
        if isinstance(extra, dict):
            known = set(self.options().keys())
            for k, v in extra.items():
                if not k or v is None:
                    continue
                if known and k not in known:
                    notes.append(f"option {k!r} does not exist on this server "
                                 f"— dropped")
                    continue
                override[k] = v
        if override:
            payload["override_settings"] = self._valid_overrides(override, notes)
            if not payload["override_settings"]:
                payload.pop("override_settings")
            else:
                # Verified on neo-2.29.2: the request really does use the
                # override (info.clip_skip == the overridden value) and True
                # puts the option back afterwards. False leaves it changed for
                # every later request, so it is never what we want.
                payload["override_settings_restore_afterwards"] = True

        # --- companion components as per-request overrides -----------------
        # Not the default path: on Neo these force a model reload inside the
        # request and a failed reload surfaces as HTTP 500 "Failed to load
        # model". set_model()/configure() (the options route) is the safe way;
        # this exists for one-off requests that must not persist.
        for key, opt in (("text_encoder", None), ("sd_vae", "sd_vae"),
                         ("low_bits", None)):
            val = cfg_only.get(key)
            if not val:
                continue
            if key == "low_bits":
                try:
                    val = norm_low_bits(val)
                except SDWebUIError as e:
                    notes.append(str(e))
                    continue
                opt = f"forge_unet_storage_dtype_{arch}"
            elif key == "sd_vae" and _is_legacy_vae(val):
                opt = "sd_vae"
            elif key == "text_encoder":
                opt = f"forge_additional_modules_{arch}"
                val = [str(val)]
            if opt not in self._option_keys():
                notes.append(f"{key}: {opt!r} not available on this server")
                continue
            payload.setdefault("override_settings", {})
            payload["override_settings"][opt] = val
            payload["override_settings_restore_afterwards"] = True
            notes.append(f"{key} sent as a per-request override — it reloads "
                         f"the model; prefer Settings > Image")

        if kind not in ("txt2img", "img2img"):
            kind = "img2img" if "init_images" in payload else "txt2img"
        payload = self._clean_payload(payload, kind=kind, notes=notes)
        return {"payload": payload, "arch": arch, "model": model,
                "distilled": distilled, "band": kind, "sampler": sampler,
                "scheduler": scheduler, "steps": int(steps),
                "cfg_scale": round(float(cfg), 3),
                "distilled_cfg_scale": dcfg,
                "clip_skip": override.get("CLIP_stop_at_last_layers"),
                "override_settings": payload.get("override_settings") or {},
                "notes": notes, "profile": prof}

    def _valid_overrides(self, override: dict, notes: list) -> dict:
        """Keep only option keys this server actually has."""
        known = set(self.options().keys())
        out = {}
        for k, v in override.items():
            if not k or v is None:
                continue
            if known and k not in known:
                notes.append(f"option {k!r} does not exist on this server "
                             f"— dropped")
                continue
            out[k] = v
        return out

    def _clean_payload(self, payload: dict, kind: str = "txt2img",
                       notes: list | None = None) -> dict:
        """Drop keys the Neo schema does not know, and sanitise scripts.

        The request model is Pydantic-validated: unknown keys are ignored
        without a word, so a legacy argument (the classic `clip_skip`, a
        removed alwayson script) would just do nothing while looking like it
        worked. Everything not in the schema is reported instead.
        """
        notes = notes if notes is not None else []
        caps = self.capabilities()
        allowed = (caps.img2img_keys if kind == "img2img"
                   else caps.payload_keys) | set(PAYLOAD_KEYS)
        if kind == "img2img":
            allowed |= set(IMG2IMG_KEYS)
        out = {}
        for k, v in payload.items():
            if v is None:
                continue
            if k in LEGACY_KEY_MAP:
                notes.append(f"'{k}' is not a payload field any more — it was "
                             f"mapped to the {LEGACY_KEY_MAP[k]} option")
                continue
            if k not in allowed:
                notes.append(f"dropped argument '{k}' (not in the Forge Neo "
                             f"{kind} schema)")
                continue
            out[k] = v
        if "alwayson_scripts" in out:
            out["alwayson_scripts"] = self._clean_scripts(
                out["alwayson_scripts"], notes)
            if not out["alwayson_scripts"]:
                out.pop("alwayson_scripts")
        return out

    def _clean_scripts(self, scripts, notes: list) -> dict:
        """Normalise alwayson_scripts to Neo's {"title": [args]} shape.

        Legacy A1111 clients sometimes send a bare list of positional args or
        a list of [name, args] pairs; Neo wants a title->args mapping, and a
        title it does not know is silently skipped by the server, so a stale
        script name would quietly change the result. Unknown titles are
        dropped and reported.
        """
        caps = self.capabilities()
        known = caps.scripts

        def norm_title(title: str) -> str:
            t = str(title).strip()
            for cand in known:
                if cand.lower() == t.lower():
                    return cand
            return t

        out: dict = {}
        if isinstance(scripts, dict):
            for title, args in scripts.items():
                name = norm_title(title)
                if known and name not in known:
                    notes.append(f"script {title!r} is not installed on this "
                                 f"server — dropped")
                    continue
                out[name] = list(args) if isinstance(args, (list, tuple)) \
                    else ([] if args is None else [args])
        elif isinstance(scripts, (list, tuple)):
            for item in scripts:
                title, args = (item if isinstance(item, (list, tuple))
                               and len(item) == 2 else (item, []))
                name = norm_title(title)
                if known and name not in known:
                    notes.append(f"script {title!r} is not installed on this "
                                 f"server — dropped")
                    continue
                out[name] = list(args) if isinstance(args, (list, tuple)) else []
        return out

    def norm_sampler(self, name: str) -> str:
        """Validate a sampler against the server's list.

        Neo dropped Karras from the sampler names (it is a schedule type now),
        so 'DPM++ 2M Karras' has to arrive as sampler 'DPM++ 2M' — a client
        that guessed wrong would silently generate with a different schedule.
        """
        raw = str(name or "").strip()
        if not raw:
            return ""
        caps = self.capabilities()
        names = caps.samplers or list(SAMPLER_CHOICES)
        table = _slug_map(names)
        hit = table.get(_slug(raw))
        if hit:
            return hit
        # tolerate a schedule suffix the caller forgot to split
        sampler, _ = split_sampler(raw)
        if sampler and sampler != raw:
            hit = table.get(_slug(sampler))
            if hit:
                return hit
        return ""

    def norm_scheduler(self, name) -> str | None:
        """Map a schedule type onto the spelling /sdapi/v1/schedulers uses."""
        if name is None:
            return None
        raw = str(name).strip()
        if not raw:
            return None
        caps = self.capabilities()
        names = caps.schedulers or list(SCHEDULER_CHOICES)
        table = _slug_map(names)
        slug = _slug(raw)
        if slug in table:
            return table[slug]
        alias = SCHEDULER_ALIASES.get(slug)
        if alias and alias in names:
            return alias
        if alias:
            return alias
        return slug

    def submit(self, payload: dict, *, img2img: bool = False,
               notes: list | None = None) -> dict:
        """POST an already-built payload.

        For callers that ran prepare_args() themselves (so they can read the
        resolved settings back) or that need to add a key prepare_args does not
        own, such as `init_images`. The payload is filtered once more on the
        way out, so nothing bypasses the schema check.
        """
        return self._post(
            "/sdapi/v1/img2img" if img2img else "/sdapi/v1/txt2img",
            self._clean_payload(payload, kind="img2img" if img2img else "txt2img",
                                notes=notes if notes is not None else []))

    def txt2img(
        self,
        prompt: str = "",
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        steps: int | None = None,
        cfg_scale: float | None = None,
        sampler_name: str = "",
        seed: int = -1,
        batch_size: int = 1,
        **extra,
    ) -> dict:
        """Text -> image(s). 'images' holds base64 PNGs, 'info' the params.

        Steps/CFG/sampler/schedule are filled in from the loaded model's
        architecture when not given, and clamped to what that architecture can
        actually use (see prepare_args).
        """
        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "steps": steps,
            "cfg_scale": cfg_scale,
            "sampler_name": sampler_name,
            "seed": seed,          # -1 = random
            "batch_size": batch_size,
            **extra,
        }
        prepared = self.prepare_args(payload, model=extra.get("model"),
                                     kind="txt2img")
        return self.submit(prepared["payload"])

    def img2img(
        self,
        init_image_path: str | Path | None = None,
        prompt: str = "",
        negative_prompt: str = "",
        denoising_strength: float = 0.6,
        width: int = 1024,
        height: int = 1024,
        steps: int | None = None,
        cfg_scale: float | None = None,
        sampler_name: str = "",
        seed: int = -1,
        **extra,
    ) -> dict:
        """Image + prompt -> new image. Lower denoising = closer to original.

        `init_image` (a path/bytes/str) is an alias for `init_image_path` so
        callers can pass a source straight through.
        """
        src = init_image_path if init_image_path is not None \
            else extra.pop("init_image", None)
        data = base64.b64encode(Path(src).read_bytes()).decode()
        payload = {
            "init_images": [data],
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "denoising_strength": denoising_strength,
            "width": width,
            "height": height,
            "steps": steps,
            "cfg_scale": cfg_scale,
            "sampler_name": sampler_name,
            "seed": seed,
            **extra,
        }
        prepared = self.prepare_args(payload, model=extra.get("model"),
                                     kind="img2img")
        return self.submit(prepared["payload"], img2img=True)

    # ---------------------------------------------------------------- video

    def video_endpoint(self) -> str | None:
        """The video route this build exposes, if any.

        Forge Neo ships a Wan video tab, but in 2.29.x it is UI-only: no
        /sdapi route and no Gradio api_name, so the wrapper below reports the
        feature as unavailable instead of guessing an endpoint. When a build
        does publish one, it is picked up here with no code change.
        """
        paths = self.capabilities().paths
        for candidate in ("/sdapi/v1/video", "/sdapi/v1/vid2vid",
                          "/sdapi/v1/text2video", "/sdapi/v1/txt2vid"):
            if candidate in paths:
                return candidate
        return None

    def video_supported(self) -> bool:
        return self.video_endpoint() is not None

    def video_txt2img(self, prompt: str = "", negative_prompt: str = "",
                      width: int = 832, height: int = 480, frames: int = 33,
                      fps: int = 16, steps: int | None = None,
                      cfg_scale: float | None = None, sampler_name: str = "",
                      seed: int = -1, **extra) -> dict:
        """Video diffusion (Wan 2.2). Routed only when the server publishes a
        video endpoint — the classic txt2img payload has no frame/fps field,
        and inventing one would silently drop the settings."""
        endpoint = self.video_endpoint()
        if not endpoint:
            raise SDWebUIError(
                "this Forge Neo build does not expose video generation over "
                "the API (the Wan tab is UI-only). Generate videos from the "
                "WebUI itself, or upgrade to a build that publishes a video "
                "route.")
        prof = self.arch_profile("wan")
        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "steps": steps if steps is not None else prof["steps"],
            "cfg_scale": cfg_scale if cfg_scale is not None else prof["cfg"],
            "sampler_name": sampler_name or prof["sampler"],
            "scheduler": prof["scheduler"],
            "seed": seed,
            "frames": int(frames),
            "fps": int(fps),
            **extra,
        }
        payload = self._clean_payload(payload, notes=[])
        return self._post(endpoint, payload)


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    c = SDClient()
    print(f"Server   : {c.base_url}")
    caps = c.capabilities(force=True)
    print(f"Reachable: {caps.ok}")
    print(f"Model    : {c.current_model()}  [{c.current_arch()}]")
    print(f"Models   : {len(c.list_models())} available")
    print(f"Samplers : {len(caps.samplers)}  Schedules: {len(caps.schedulers)}")
    print(f"TextEnc  : {caps.text_encoders}")
    print(f"VAEs     : {caps.vaes}")
    print(f"Video API: {c.video_endpoint() or 'not exposed'}")
    for row in c.architecture_summary():
        print(f"  {row['arch']:<7} cfg {row['cfg']:<5} steps {row['steps']:<4} "
              f"{row['sampler']}/{row['scheduler']}"
              + ("  [distilled]" if row["distilled"] else ""))
