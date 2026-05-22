"""Factory: read config/active_model.yaml → return an LLMProvider."""
from __future__ import annotations

import logging
import pathlib

import yaml

from dolev_ai.llm import LLMProvider
from dolev_ai.llm.anthropic_provider import AnthropicProvider
from dolev_ai.llm.openai_compat import OpenAICompatProvider

logger = logging.getLogger(__name__)

ACTIVE_YAML = pathlib.Path(__file__).parent.parent.parent.parent / "config" / "active_model.yaml"


def load_active_config() -> dict:
    if not ACTIVE_YAML.exists():
        raise RuntimeError(
            f"{ACTIVE_YAML} not found. Run `py -3.12 scripts/probe_hardware.py` first."
        )
    with open(ACTIVE_YAML) as f:
        return yaml.safe_load(f) or {}


def build_provider(cfg: dict | None = None) -> LLMProvider:
    cfg = cfg or load_active_config()
    kind = (cfg.get("provider") or "openai_compat").strip()
    model = cfg["name"]
    timeout = float(cfg.get("timeout_s", 60))

    if kind == "openai_compat":
        endpoint = cfg["endpoint"]
        logger.info(f"LLM provider: openai_compat model={model} endpoint={endpoint}")
        return OpenAICompatProvider(base_url=endpoint, model=model, timeout_s=timeout)

    if kind == "anthropic":
        logger.info(f"LLM provider: anthropic model={model}")
        return AnthropicProvider(model=model)

    raise ValueError(f"Unknown provider: {kind!r}")
