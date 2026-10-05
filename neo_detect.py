"""
Checkpoint architecture detection from the safetensors header (torch-free).

This is a faithful port of the classifier inside Forge Neo
(modules_forge/packages/huggingface_guess/detection.py). Neo itself decides
which pipeline a checkpoint needs by inspecting the state dict — NOT by file
name — which is why "novaAnimeAM_v5029B" is an Anima model although the name
never says so. The detector reads the safetensors header (key names + tensor
shapes, no tensor data), so a multi-GB checkpoint costs a few hundred KB.

Only the modular DiT families are ported: legacy SD1.5/SDXL checkpoints are
unambiguous enough from their names, and if a file matches nothing here the
caller falls back to name heuristics.

Also carries the family -> required companion spec (which text encoder and
VAE patterns a modular model needs), used to auto-attach them on a model
switch.
"""

import json
import re
import struct
from pathlib import Path

# --------------------------------------------------------------------------
# image_model tag (Neo's own detection output) -> app architecture
# --------------------------------------------------------------------------
IMAGE_MODEL_ARCH = {
    "lumina2": "lumina",           # Z-Image is a Lumina2 with dim 3840
    "wan2.1": "wan",
    "flux": "flux",
    "flux2": "klein",              # presets.py: klein = Flux.2
    "chroma": "flux",              # Chroma = distilled Flux (FluxSchnell sub)
    "anima": "anima",
    "pid": "pid",
    "qwen_image": "qwen",
    "krea2": "krea",
    "ernie": "ernie",
}

#: architectures whose DISTILLED guidance is real (Neo's use_distill()) —
#: the <arch>_t2i_dcfg options of the other families are the Shift slider
#: under a confusing key name, not a distilled CFG, and must not be sent.
DISTILLED_ARCHES = {"flux"}

#: text encoder + VAE name patterns per family, matched against the files
#: /sdapi/v1/sd-modules reports (models/text_encoder + models/VAE). Patterns
#: are case-insensitive "startswith/contains" stems. A family with an empty
#: spec means "no confident guess — let the user pick in Settings".
FAMILY_MODULES = {
    "anima":  {"text_encoder": ["qwen"], "vae": ["qwen"]},
    "qwen":   {"text_encoder": ["qwen"], "vae": ["qwen"]},
    "flux":   {"text_encoder": ["clip_l", "t5xxl"], "vae": ["ae"]},
    "wan":    {"text_encoder": ["umt5"], "vae": ["wan"]},
}


def read_safetensors_header(path: str | Path) -> dict | None:
    """{tensor name: shape tuple} from a safetensors file, or None.

    Reads only the header: an 8-byte little-endian length then the JSON
    header. Anything unreadable (GGUF, diffusers folders, partial files)
    returns None and the caller falls back to name heuristics.
    """
    p = Path(path)
    try:
        with p.open("rb") as f:
            head = f.read(8)
            if len(head) < 8:
                return None
            (n,) = struct.unpack("<Q", head)
            if not 0 < n < 100 * 1024 * 1024:
                return None
            meta = json.loads(f.read(n).decode("utf-8", "replace"))
    except (OSError, ValueError):
        return None
    shapes = {}
    for key, val in meta.items():
        if key == "__metadata__":
            continue
        shape = (val or {}).get("shape")
        if isinstance(shape, list):
            shapes[key] = tuple(shape)
    return shapes or None


class _ShapeOnly:
    """Stand-in for a tensor: the detector only ever reads `.shape`."""

    __slots__ = ("shape",)

    def __init__(self, shape: tuple):
        self.shape = shape


# --------------------------------------------------------------------------
# ported detection (see Neo's huggingface_guess/detection.py)
# --------------------------------------------------------------------------

def _count_blocks(keys: list[str], prefix: str) -> int:
    count = 0
    keys_set = set(keys)
    while prefix.format(count) in keys_set or \
            any(k.startswith(prefix.format(count)) for k in keys):
        count += 1
    return count


def _unet_prefix(keys: list[str]) -> str:
    for prefix in ("model.diffusion_model.", "net."):
        if sum(1 for k in keys if k.startswith(prefix)) > 5:
            return prefix
    return "model."


def detect_image_model(shapes: dict) -> str | None:
    """Neo's detect_unet_config, reduced to the family-deciding branches.

    Returns the app architecture ("anima", "qwen", "flux", ...) or None when
    the file is not a recognised modular DiT (legacy SD UNets fall through —
    their names carry them).
    """
    tag = _detect_tag(shapes)
    if tag is None:
        return None
    return IMAGE_MODEL_ARCH.get(tag, tag)


def _detect_tag(shapes: dict) -> str | None:
    """The raw `image_model` tag from Neo's branches (qwen_image, flux2, ...)."""
    keys = list(shapes)
    sd = {k: _ShapeOnly(v) for k, v in shapes.items()}
    kp = _unet_prefix(keys)

    def has(key: str) -> bool:
        return (kp + key) in shapes

    def w(key: str) -> _ShapeOnly:
        return sd[kp + key]

    # --- Lumina 2 / Z-Image (Z-Image is Lumina2 with dim 3840) -----------
    if has("cap_embedder.1.weight") and (
            has("noise_refiner.0.attention.k_norm.weight")
            or has("layers.0.attention.to_out.0.qweight")):
        try:
            dim = int(w("cap_embedder.1.weight").shape[0])
        except (IndexError, KeyError, TypeError):
            return None
        return "zit" if dim == 3840 else "lumina2"

    # --- Wan 2.1 / 2.2 ----------------------------------------------------
    if has("head.modulation"):
        return "wan2.1"

    # --- SVD-quantised Flux (nunchaku) ------------------------------------
    if has("single_transformer_blocks.0.mlp_fc1.qweight"):
        return "flux"

    # --- Flux.1 / Flux.2 / Chroma ------------------------------------------
    if (has("double_blocks.0.img_attn.norm.key_norm.scale")
            or has("double_blocks.0.img_attn.norm.key_norm.weight")) \
            and (has("img_in.weight")
                 or has("distilled_guidance_layer.norms.0.scale")):
        if has("double_stream_modulation_img.lin.weight"):
            return "flux2"
        if has("distilled_guidance_layer.0.norms.0.scale") \
                or has("distilled_guidance_layer.norms.0.scale"):
            return "chroma"
        return "flux"

    # --- Anima (Qwen-Image-based anime DiT; needs the llm adapter) --------
    if has("blocks.0.mlp.layer1.weight"):
        return "anima"

    # --- PiD ---------------------------------------------------------------
    if has("lq_proj.latent_proj.0.weight"):
        return "pid"

    # --- Qwen Image ---------------------------------------------------------
    if has("txt_norm.weight"):
        return "qwen_image"

    # --- Krea 2 ---------------------------------------------------------------
    if has("txtfusion.projector.weight"):
        return "krea2"

    # --- ERNIE Image ------------------------------------------------------------
    if has("layers.0.mlp.linear_fc2.weight"):
        return "ernie"

    return None                     # not a modular DiT we know


_HEADER_CACHE: dict = {}


def guess_file(path: str | Path) -> str | None:
    """Architecture of a checkpoint file from its bytes, or None.

    Cached on (path, size, mtime) — checkpoint files are static, so each file
    is read once per process.
    """
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    token = (str(p), st.st_size, st.st_mtime)
    if token in _HEADER_CACHE:
        return _HEADER_CACHE[token]
    arch = None
    shapes = read_safetensors_header(p)
    if shapes:
        tag = detect_image_model(shapes)
        if tag:
            arch = IMAGE_MODEL_ARCH.get(tag, tag)
    _HEADER_CACHE[token] = arch
    return arch


# --------------------------------------------------------------------------
# name heuristics (fallback when the file bytes are not readable — e.g. the
# WebUI runs on another host)
# --------------------------------------------------------------------------

_NAME_PATTERNS = (
    ("qwen", ("qwenimage", "qwen")),
    ("klein", ("klein", "flux2")),
    ("flux", ("flux", "chroma")),
    ("anima", ("anima", "animeam", "animam")),
    ("krea", ("krea",)),
    ("zit", ("zimage", "zit")),
    ("wan", ("wan22", "wan2", "wan14b", "wan")),
    ("lumina", ("lumina",)),
    ("ernie", ("ernie",)),
    ("pid", ("pidnext", "pimage", "pid")),
    ("xl", ("xl", "sdxl", "illustrious", "pony", "noob", "realvis", "hyper",
            "juggernaut", "animediff", "anythingv5")),
)


def guess_from_name(title: str) -> str:
    t = re.sub(r"[\s_\-]+", "", str(title or "")).lower()
    if not t:
        return "sd"
    for arch, pats in _NAME_PATTERNS:
        if any(p in t for p in pats):
            return arch
    return "sd"


_DISTILLED_HINTS = ("turbo", "schnell", "lightning", "lcm", "ddistill",
                    "distilled", "1step", "fewstep", "chroma")


def name_is_distilled(title: str) -> bool:
    t = re.sub(r"[\s_\-]+", "", str(title or "")).lower()
    return any(h in t for h in _DISTILLED_HINTS)
