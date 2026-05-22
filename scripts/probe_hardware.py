"""Detect hardware + reachable LLM endpoints, pick the best model from config/models.yaml.

Writes config/active_model.yaml. Re-run any time hardware or running models change.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

import yaml

REPO = pathlib.Path(__file__).parent.parent
MODELS_YAML = REPO / "config" / "models.yaml"
ACTIVE_YAML = REPO / "config" / "active_model.yaml"


def detect_vram_gb() -> float:
    """Return total VRAM in GB across all NVIDIA GPUs, or 0 if none."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode().strip()
        total_mb = sum(int(line.strip()) for line in out.splitlines() if line.strip())
        return round(total_mb / 1024, 1)
    except (FileNotFoundError, subprocess.SubprocessError, ValueError):
        pass

    # Fallback: ask pytorch if installed
    try:
        import torch
        if torch.cuda.is_available():
            total = sum(
                torch.cuda.get_device_properties(i).total_memory
                for i in range(torch.cuda.device_count())
            )
            return round(total / (1024 ** 3), 1)
    except Exception:
        pass

    return 0.0


def detect_ram_gb() -> float:
    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except ImportError:
        return 0.0


def endpoint_alive(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/models", timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, TimeoutError, ConnectionResetError, OSError):
        return False


def list_endpoint_models(url: str) -> list[str]:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/models", timeout=3) as r:
            body = json.loads(r.read().decode())
            return [m.get("id", "") for m in body.get("data", [])]
    except Exception:
        return []


def find_live_endpoint(default: str, alts: list[str]) -> str | None:
    candidates = [default, *alts] if default not in alts else alts
    for url in candidates:
        if endpoint_alive(url):
            return url
    return None


def main() -> int:
    with open(MODELS_YAML) as f:
        cfg = yaml.safe_load(f)

    vram = detect_vram_gb()
    ram = detect_ram_gb()
    print(f"Detected: VRAM={vram} GB, RAM={ram} GB")

    pin = (cfg.get("pin") or "").strip()
    alts = cfg.get("alt_endpoints", []) or []
    candidates = cfg["candidates"]

    if pin:
        candidates = [c for c in candidates if c["name"] == pin]
        if not candidates:
            print(f"ERROR: pinned model {pin!r} is not in candidates")
            return 1
        print(f"Pin in effect: {pin}")

    chosen = None
    for c in candidates:
        needs = c.get("min_vram_gb", 0)
        cpu_ok = c.get("cpu_ok", False)
        if vram >= needs or (cpu_ok and ram >= 8):
            endpoint = c.get("endpoint", "")
            live = find_live_endpoint(endpoint, alts)
            if not live:
                print(f"  skip {c['name']}: no live endpoint at {endpoint} or alternates")
                continue
            available = list_endpoint_models(live)
            if c["name"] not in available:
                print(f"  skip {c['name']}: not pulled on {live}")
                print(f"      -> pull it with: ollama pull {c['name']}")
                continue
            chosen = {**c, "endpoint": live}
            break

    if chosen is None:
        print("\nERROR: no usable model found.")
        print("Hint: install Ollama or LM Studio, then `ollama pull qwen2.5:7b`")
        return 1

    defaults = cfg.get("defaults", {})
    active = {
        "name": chosen["name"],
        "provider": chosen["provider"],
        "endpoint": chosen["endpoint"],
        "batch_size": defaults.get("batch_size", 8),
        "max_concurrent": defaults.get("max_concurrent", 2),
        "timeout_s": defaults.get("timeout_s", 60),
        "max_queue": defaults.get("max_queue", 1000),
    }
    with open(ACTIVE_YAML, "w") as f:
        yaml.safe_dump(active, f, sort_keys=False)
    print(f"\nChose: {chosen['name']} @ {chosen['endpoint']}")
    print(f"Wrote: {ACTIVE_YAML}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
