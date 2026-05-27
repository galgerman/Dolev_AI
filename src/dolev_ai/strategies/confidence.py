"""Confidence scoring for momentum signals.

Combines FeatureSnapshot fields into a 0-100 confidence score with
per-feature contribution breakdown. Weights are config-driven and
recalibrated from observe-mode data via scripts/report_signals.py.

Direction handling: for "buy" signals positive features add to the
score; for "sell" the directional features flip sign (negative gradient
is good for shorts, below-VWAP is good for shorts, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from dolev_ai.strategies.features import FeatureSnapshot


# Default weights — initial guess, to be recalibrated.
# Positive = bullish-for-direction; negative = penalty regardless of side.
DEFAULT_WEIGHTS: dict[str, float] = {
    "gradient": 25.0,
    "acceleration": 15.0,
    "roc_3m": 10.0,
    "rel_strength_spy": 10.0,
    "rel_strength_sector": 10.0,
    "vwap_above": 10.0,
    "broke_pmh": 10.0,
    "rel_volume": 5.0,
    "extension_penalty": -5.0,
    "spread_penalty": -5.0,
}


# Per-feature normalisation caps (the value at which the feature contributes
# 100% of its weight). All in the natural unit of the feature.
NORM_CAPS: dict[str, float] = {
    "gradient": 1.0,          # %/min — at 1.0 %/min contribution = full weight
    "acceleration": 0.5,      # change in %/min between bars
    "roc_3m": 3.0,            # %
    "rel_strength_spy": 0.8,  # %/min difference vs SPY
    "rel_strength_sector": 0.8,
    "extension_pct": 3.0,     # %, beyond which the long-extension penalty kicks in
    "spread_pct": 0.3,        # %, anything above this is "wide"
    "rel_volume": 4.0,        # multiple of baseline
}


@dataclass
class ConfidenceResult:
    score: float                                 # 0..100
    components: dict[str, float] = field(default_factory=dict)
    explanation: str = ""


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _norm(value: float, cap: float) -> float:
    """Linearly normalise to [-1, 1] saturating at ±cap."""
    if cap <= 0:
        return 0.0
    return _clip(value / cap, -1.0, 1.0)


class ConfidenceScorer:
    """Weighted-sum scorer over normalised FeatureSnapshot fields.

    score = clip(sum(weight_i * normalised_i) + 50, 0, 100)

    The +50 baseline means a "neutral" signal lands at 50; clearly bullish
    features push above, clearly bearish/risk push below. The final clamp
    enforces the 0..100 range.
    """

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self._w = dict(DEFAULT_WEIGHTS)
        if weights:
            self._w.update(weights)

    def score(
        self,
        features: FeatureSnapshot,
        side: Literal["buy", "sell"],
    ) -> ConfidenceResult:
        sign = 1.0 if side == "buy" else -1.0
        comps: dict[str, float] = {}

        # Directional features — flip sign for shorts
        comps["gradient"] = self._w["gradient"] * _norm(features.gradient * sign, NORM_CAPS["gradient"])
        comps["acceleration"] = self._w["acceleration"] * _norm(features.acceleration * sign, NORM_CAPS["acceleration"])
        comps["roc_3m"] = self._w["roc_3m"] * _norm(features.roc_3m * sign, NORM_CAPS["roc_3m"])
        comps["rel_strength_spy"] = self._w["rel_strength_spy"] * _norm(
            features.rel_strength_spy * sign, NORM_CAPS["rel_strength_spy"]
        )
        comps["rel_strength_sector"] = self._w["rel_strength_sector"] * _norm(
            features.rel_strength_sector * sign, NORM_CAPS["rel_strength_sector"]
        )

        # VWAP state: for long, "above" is good; for short, "below" is good
        if features.vwap_state == "above":
            vwap_val = 1.0 * sign
        elif features.vwap_state == "below":
            vwap_val = -1.0 * sign
        else:
            vwap_val = 0.0
        comps["vwap_above"] = self._w["vwap_above"] * vwap_val

        # Premarket break: long uses PMH break, short uses PML break
        if side == "buy":
            comps["broke_pmh"] = self._w["broke_pmh"] * (1.0 if features.broke_pmh else 0.0)
        else:
            comps["broke_pmh"] = self._w["broke_pmh"] * (1.0 if features.broke_pml else 0.0)

        # Volume confirmation — direction-agnostic (more volume = better)
        rel_vol_norm = _clip(features.rel_volume / NORM_CAPS["rel_volume"], 0.0, 1.0)
        comps["rel_volume"] = self._w["rel_volume"] * rel_vol_norm

        # Extension penalty — once price moves >cap from VWAP we penalise
        # (regardless of side). The weight is negative so we add.
        overext = max(0.0, abs(features.extension_pct) - NORM_CAPS["extension_pct"])
        overext_norm = _clip(overext / NORM_CAPS["extension_pct"], 0.0, 1.0)
        comps["extension_penalty"] = self._w["extension_penalty"] * overext_norm

        # Spread penalty — wider spread = worse fills. Negative weight.
        spread_norm = _clip(features.spread_pct / NORM_CAPS["spread_pct"], 0.0, 1.0)
        comps["spread_penalty"] = self._w["spread_penalty"] * spread_norm

        # Final: sum components, anchored at 50
        raw = 50.0 + sum(comps.values())
        score = round(_clip(raw, 0.0, 100.0), 1)

        return ConfidenceResult(
            score=score,
            components={k: round(v, 2) for k, v in comps.items()},
            explanation=_explain(comps),
        )


def _explain(comps: dict[str, float]) -> str:
    """Human-readable summary of the top positive and negative contributors."""
    nonzero = [(k, v) for k, v in comps.items() if abs(v) > 0.1]
    if not nonzero:
        return "neutral — no strong features"
    nonzero.sort(key=lambda kv: abs(kv[1]), reverse=True)
    top = nonzero[:3]
    parts = [f"{k} {v:+.1f}" for k, v in top]
    return ", ".join(parts)
