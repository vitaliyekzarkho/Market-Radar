from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OUTPUT_FILE = Path("data") / "macro" / "fed_expectations.json"
CME_URL = "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html"


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    # CME publishes the tool publicly but not a stable free machine-readable
    # probability feed. Do not scrape dynamic HTML or invent probabilities.
    context: dict[str, Any] = {
        "available": False,
        "reason": "NO_VERIFIED_MACHINE_READABLE_FEED",
        "source": "CME FedWatch",
        "source_url": CME_URL,
        "manual_reference_available": True,
        "scoring_applied": False,
    }
    atomic_write_json(OUTPUT_FILE, {"schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(), "context": context})
    print("FED_EXPECTATIONS: NO_DATA | reason=NO_VERIFIED_MACHINE_READABLE_FEED")
    print("FED_EXPECTATIONS_ADAPTER: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
