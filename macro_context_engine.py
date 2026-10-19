from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATA_DIR = Path("data")
OFFICIAL_FILE = DATA_DIR / "macro" / "official_releases.json"
RATES_FILE = DATA_DIR / "macro" / "rates_context.json"
COT_FILE = DATA_DIR / "macro" / "cot_context.json"
EXPECTATIONS_FILE = DATA_DIR / "macro" / "fed_expectations.json"
INTERMARKET_FILE = DATA_DIR / "macro" / "intermarket_context.json"
OUTPUT_FILE = DATA_DIR / "macro" / "GOLD_pro.json"


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def build_context(official: dict[str, Any], rates: dict[str, Any], cot: dict[str, Any], expectations: dict[str, Any], intermarket: dict[str, Any] | None = None) -> dict[str, Any]:
    source_status = official.get("sources") if isinstance(official.get("sources"), dict) else {}
    monitored = [name for name, item in source_status.items() if isinstance(item, dict) and item.get("available")]
    recent = official.get("recent_releases") if isinstance(official.get("recent_releases"), list) else []
    return {
        "available": bool(monitored or rates or cot),
        "official_sources_monitored": monitored,
        "official_source_status": source_status,
        "recent_verified_releases": recent[:5],
        "rates": rates.get("context") if isinstance(rates.get("context"), dict) else {"available": False, "reason": "NO_RATES_DATA"},
        "positioning": cot.get("context") if isinstance(cot.get("context"), dict) else {"available": False, "reason": "NO_COT_DATA"},
        "fed_expectations": expectations.get("context") if isinstance(expectations.get("context"), dict) else {"available": False, "reason": "NO_EXPECTATIONS_DATA"},
        "intermarket": (intermarket or {}).get("context") if isinstance((intermarket or {}).get("context"), dict) else {"available": False, "reason": "NO_INTERMARKET_DATA"},
        "scoring_applied": False,
    }


def main() -> int:
    context = build_context(load_json(OFFICIAL_FILE), load_json(RATES_FILE), load_json(COT_FILE), load_json(EXPECTATIONS_FILE), load_json(INTERMARKET_FILE))
    payload = {"schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(), "context": context}
    atomic_write_json(OUTPUT_FILE, payload)
    print(
        f"MACRO_CONTEXT: sources={','.join(context['official_sources_monitored']) or 'none'} | "
        f"rates={context['rates'].get('direction', 'no_data')} | "
        f"cot={context['positioning'].get('direction', 'no_data')} | score_impact=0"
    )
    print("MACRO_CONTEXT_ENGINE: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
