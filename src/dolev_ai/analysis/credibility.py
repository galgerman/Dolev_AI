"""Per-account credibility scoring based on tier + optional override."""
from __future__ import annotations

import pathlib
from functools import lru_cache

import yaml

SEEDS_PATH = pathlib.Path(__file__).parent.parent.parent.parent / "config" / "seeds.yaml"

_TIER_WEIGHTS = {1: 1.0, 2: 0.7, 3: 0.4}


@lru_cache(maxsize=1)
def _load_seeds() -> dict[str, float]:
    """Return handle → credibility score mapping."""
    if not SEEDS_PATH.exists():
        return {}
    with open(SEEDS_PATH) as f:
        data = yaml.safe_load(f)
    result: dict[str, float] = {}
    tier_weights: dict[int, float] = data.get("tier_weights", _TIER_WEIGHTS)
    for account in data.get("accounts", []):
        handle = account["handle"].lstrip("@").lower()
        if "credibility_override" in account:
            score = float(account["credibility_override"])
        else:
            tier = int(account.get("tier", 3))
            score = tier_weights.get(tier, 0.4)
        result[handle] = score
    return result


def credibility(handle: str) -> float:
    """Return credibility score (0..1) for the given handle.

    Unknown accounts get a conservative default of 0.2.
    """
    return _load_seeds().get(handle.lstrip("@").lower(), 0.2)


def reload_seeds() -> None:
    _load_seeds.cache_clear()
