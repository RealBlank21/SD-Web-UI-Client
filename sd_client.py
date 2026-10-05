"""
Stable Diffusion WebUI API client (vendored, standalone).

Server must run with --api --listen. Requires: requests.
"""

import base64
import json
import re
from datetime import datetime
from pathlib import Path

import requests

DEFAULT_URL = "http://100.93.220.68:7860"


class SDWebUIError(Exception):
    """Raised when the WebUI API returns an error."""


class SDClient:
    def __init__(self, base_url: str = DEFAULT_URL, timeout: int = 600):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, endpoint: str, payload: dict) -> dict:
        resp = requests.post(
            f"{self.base_url}{endpoint}",
            json=payload,
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise SDWebUIError(
                f"{endpoint} failed ({resp.status_code}): {resp.text[:500]}")
        return resp.json()

    def _get(self, endpoint: str):
        resp = requests.get(f"{self.base_url}{endpoint}", timeout=30)
        if resp.status_code != 200:
            raise SDWebUIError(
                f"{endpoint} failed ({resp.status_code}): {resp.text[:500]}")
        return resp.json()

    # ------------------------------------------------------------------ info

    def list_models(self) -> list[dict]:
        """All checkpoints available in models/Stable-diffusion."""
        return self._get("/sdapi/v1/sd-models")

    def list_samplers(self) -> list[dict]:
        return self._get("/sdapi/v1/samplers")

    def current_model(self) -> str:
        return self._get("/sdapi/v1/options").get("sd_model_checkpoint", "")

    def set_model(self, model_title: str):
        """Switch checkpoint. Use the exact 'title' from list_models()."""
        self._post("/sdapi/v1/options", {"sd_model_checkpoint": model_title})

    def progress(self) -> dict:
        return self._get("/sdapi/v1/progress?skip_current_image=false")

    def interrupt(self):
        """Stop the running job on the WebUI side (used by "stop")."""
        try:
            self._post("/sdapi/v1/interrupt", {})
        except Exception:
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

    # --------------------------------------------------------------- imaging

    def txt2img(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        steps: int = 25,
        cfg_scale: float = 7.0,
        sampler_name: str = "DPM++ 2M Karras",
        seed: int = -1,
        batch_size: int = 1,
        **extra,
    ) -> dict:
        """Text -> image(s). 'images' holds base64 PNGs, 'info' the params."""
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
        return self._post("/sdapi/v1/txt2img", payload)

    def img2img(
        self,
        init_image_path: str | Path,
        prompt: str,
        negative_prompt: str = "",
        denoising_strength: float = 0.6,
        width: int = 1024,
        height: int = 1024,
        steps: int = 25,
        cfg_scale: float = 7.0,
        sampler_name: str = "DPM++ 2M Karras",
        seed: int = -1,
        **extra,
    ) -> dict:
        """Image + prompt -> new image. Lower denoising = closer to original."""
        data = base64.b64encode(Path(init_image_path).read_bytes()).decode()
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
        return self._post("/sdapi/v1/img2img", payload)


def save_images(result: dict, out_dir: str | Path = ".",
                name_prefix: str = "sd") -> list[Path]:
    """Decode base64 images from a txt2img/img2img response; return paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    seed_match = re.search(r'"seed": (\d+)', result.get("info", "{}"))
    seed = seed_match.group(1) if seed_match else "0"

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    saved = []
    for i, b64 in enumerate(result["images"]):
        path = out_dir / f"{name_prefix}_{timestamp}_{seed}_{i}.png"
        path.write_bytes(base64.b64decode(b64))
        saved.append(path)
    return saved


if __name__ == "__main__":
    c = SDClient()
    print(f"Server   : {c.base_url}")
    print(f"Model    : {c.current_model()}")
    print(f"Models   : {len(c.list_models())} available")
