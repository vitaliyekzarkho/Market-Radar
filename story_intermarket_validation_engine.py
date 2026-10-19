"""C3.3.3: compare story hypotheses with measured, closed-bar intermarket moves.

No causal attribution, trade permission, or score/direction changes are produced.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from reaction_window_engine import atomic_json

WINDOWS = ("post_15m", "post_60m", "post_4h", "post_24h")
ASSET_ALIASES = {
    "oil": ("oil", "wti", "brent", "usoil", "ukoil"),
    "yields": ("yields", "nominal_yields", "us10y", "us02y", "tnx"),
    "real_yields": ("real_yields", "tips", "us10y_real"),
    "usd": ("usd", "dxy", "usd_breadth"),
    "gold": ("gold", "xauusd", "gold.pro", "xau"),
    "inflation_expectations": ("inflation_expectations", "breakeven", "us10y_breakeven"),
    "equities": ("equities", "spx", "sp500", "us500"),
}
DIRECTIONS = {"bullish": 1, "rising": 1, "bearish": -1, "falling": -1,
              "flat": 0, "neutral": 0}


def _asset(report: dict[str, Any], name: str) -> dict[str, Any] | None:
    assets = report.get("assets") or {}
    if not isinstance(assets, dict):
        return None
    aliases = ASSET_ALIASES.get(name, (name,))
    matches = [(key, value) for key, value in assets.items()
               if isinstance(value, dict) and (str(key).lower() in aliases or
               str(value.get("symbol", "")).lower() in aliases)]
    # Multiple sources for one asset require explicit selection, never arbitrary precedence.
    return matches[0][1] if len(matches) == 1 else None


def _expected_signs(expected: str) -> set[int] | None:
    token = expected.lower().strip()
    if token in DIRECTIONS:
        return {DIRECTIONS[token]}
    if token in ("bearish_or_mixed", "bearish_or_neutral", "mixed_or_falling"):
        return {-1, 0}
    if token in ("bullish_or_mixed", "mixed_or_rising"):
        return {1, 0}
    if token == "not_rising":
        return {-1, 0}
    if token == "not_strongly_bullish":
        # Strength cannot be established from directional move alone.
        return None
    return None  # conditional/mixed/unknown must not be treated as confirmation


def evaluate_story(interpretation: dict[str, Any], report: dict[str, Any],
                   window: str = "post_60m", min_abs_move_percent: float = 0.0) -> dict[str, Any]:
    if window not in WINDOWS:
        raise ValueError(f"Unsupported window: {window}")
    if min_abs_move_percent < 0:
        raise ValueError("min_abs_move_percent must be nonnegative")
    story_id = str(interpretation.get("story_id") or "")
    event_id = str(report.get("event_id") or "")
    if not story_id or story_id != event_id:
        raise ValueError("Story/reaction event ID mismatch: explicit identity required")
    results = []
    for candidate in interpretation.get("mechanism_candidates") or []:
        observations = []
        for name, expected in (candidate.get("expected") or {}).items():
            asset = _asset(report, name)
            window_data = (asset.get("post_event") or {}).get(window) if asset else None
            base = {"asset": name, "expected": expected,
                    "source": asset.get("source") if asset else None,
                    "symbol": asset.get("symbol") if asset else None}
            if not isinstance(window_data, dict) or not window_data.get("complete") or not window_data.get("available"):
                observations.append({**base, "result": "insufficient_data", "reason": "missing_or_incomplete_window"})
                continue
            move = window_data.get("move_percent")
            if not isinstance(move, (int, float)):
                observations.append({**base, "result": "insufficient_data", "reason": "missing_move"})
                continue
            allowed = _expected_signs(str(expected))
            if allowed is None:
                observations.append({**base, "move_percent": move, "result": "insufficient_data",
                                     "reason": "non_directional_or_strength_condition"})
                continue
            observed_sign = 0 if abs(move) <= min_abs_move_percent else (1 if move > 0 else -1)
            observations.append({**base, "move_percent": move,
                                 "result": "supported" if observed_sign in allowed else "contradicted",
                                 "reason": "directional_consistency_only"})
        states = [item["result"] for item in observations]
        if not states or "insufficient_data" in states:
            status = "insufficient_data"
        elif all(state == "supported" for state in states):
            status = "supported"
        elif all(state == "contradicted" for state in states):
            status = "contradicted"
        else:
            status = "mixed"
        results.append({"mechanism_id": candidate.get("mechanism_id"), "status": status,
                        "observations": observations, "causality_confirmed": False})
    return {"schema_version": 1, "story_id": story_id, "event_id": event_id,
            "event_time_utc": report.get("event_time_utc"), "as_of_utc": report.get("as_of_utc"),
            "window": window, "min_abs_move_percent": min_abs_move_percent,
            "mechanisms": results,
            "interpretation": "directional_consistency_only_not_causal_attribution",
            "permissions": {"entry": False, "direction_switch": False, "score": False, "confirm_cause": False}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interpretations", type=Path, required=True)
    parser.add_argument("--reaction", type=Path, required=True)
    parser.add_argument("--story-id", required=True)
    parser.add_argument("--window", choices=WINDOWS, default="post_60m")
    parser.add_argument("--min-move-percent", type=float, default=0.0)
    parser.add_argument("--output", type=Path, default=Path("data/news/story_intermarket_validation.json"))
    args = parser.parse_args()
    interpretations = json.loads(args.interpretations.read_text(encoding="utf-8"))
    candidates = [item for item in interpretations.get("interpretations", []) if item.get("story_id") == args.story_id]
    if len(candidates) != 1:
        raise ValueError("Exactly one matching story interpretation is required")
    report = json.loads(args.reaction.read_text(encoding="utf-8"))
    result = evaluate_story(candidates[0], report, args.window, args.min_move_percent)
    atomic_json(args.output, result)
    print(f"STORY_INTERMARKET_VALIDATION: {args.story_id} | mechanisms={len(result['mechanisms'])} | saved={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
