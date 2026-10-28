from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SYMBOL = "GOLD.pro"
SYMBOL_FILE = SYMBOL.replace(".", "_")
DATA_DIR = Path("data")

NOTIFICATION_FILE = DATA_DIR / "notifications" / f"{SYMBOL_FILE}.json"
STATE_FILE = DATA_DIR / "runtime" / f"{SYMBOL_FILE}_telegram_state.json"
DELIVERY_FILE = DATA_DIR / "telegram" / f"{SYMBOL_FILE}.json"

TOKEN_ENV = "MARKETRADAR_TELEGRAM_BOT_TOKEN"
CHAT_ID_ENV = "MARKETRADAR_TELEGRAM_CHAT_ID"
TELEGRAM_TEXT_LIMIT = 4096
HTTP_TIMEOUT_SECONDS = 15
TRADINGVIEW_CHART_URL = "https://www.tradingview.com/chart/?symbol=OANDA%3AXAUUSD"
ECONOMIC_CALENDAR_URL = "https://www.tradingview.com/economic-calendar/"


def load_json(path: Path, required: bool = True) -> dict[str, Any]:
    if not path.is_file():
        if required:
            raise FileNotFoundError(f"Missing required file: {path}")
        return {}
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else {}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    temporary.replace(path)


def message_digest(message: str) -> str:
    return hashlib.sha256(message.encode("utf-8")).hexdigest()[:24]


def validate_notification(payload: dict[str, Any]) -> tuple[bool, str]:
    if not payload.get("should_send"):
        return False, "NO_ALERT"
    message = str(payload.get("message") or "").strip()
    if not message:
        return False, "EMPTY_MESSAGE"
    if len(message) > TELEGRAM_TEXT_LIMIT:
        return False, "MESSAGE_TOO_LONG"
    return True, "READY"


def safe_http_url(value: Any) -> str | None:
    candidate = str(value or "").strip()
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return candidate


def build_inline_keyboard(notification: dict[str, Any]) -> dict[str, Any]:
    links = notification.get("links")
    links = links if isinstance(links, dict) else {}

    chart_url = safe_http_url(links.get("chart_url")) or TRADINGVIEW_CHART_URL
    calendar_url = safe_http_url(links.get("calendar_url")) or ECONOMIC_CALENDAR_URL
    driver_url = safe_http_url(links.get("driver_url") or notification.get("driver_url"))

    rows: list[list[dict[str, str]]] = [
        [{"text": "📈 Open chart", "url": chart_url}],
    ]
    context_row: list[dict[str, str]] = []
    if driver_url:
        context_row.append({"text": "📰 Drivers", "url": driver_url})
    context_row.append({"text": "📅 Calendar", "url": calendar_url})
    rows.append(context_row)
    return {"inline_keyboard": rows}


def send_telegram(
    token: str,
    chat_id: str,
    message: str,
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    form: dict[str, str] = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": "true",
    }
    if reply_markup:
        form["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    encoded = urllib.parse.urlencode(form).encode("utf-8")
    request = urllib.request.Request(url, data=encoded, method="POST")
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict) or not body.get("ok"):
        raise RuntimeError(f"Telegram rejected the message: {body}")
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    return {
        "message_id": result.get("message_id"),
        "chat_id": (result.get("chat") or {}).get("id") if isinstance(result.get("chat"), dict) else None,
    }


def delivery_decision(
    notification: dict[str, Any],
    state: dict[str, Any],
    token: str | None,
    chat_id: str | None,
) -> dict[str, Any]:
    valid, reason = validate_notification(notification)
    message = str(notification.get("message") or "").strip()
    digest = message_digest(message) if message else None
    if not valid:
        status = "skipped"
    elif digest == state.get("last_sent_digest"):
        status, reason = "skipped", "DUPLICATE_MESSAGE"
    elif state.get('pending_digest'):
        status, reason = 'skipped', 'PREVIOUS_DELIVERY_UNKNOWN'
    elif not token or not chat_id:
        status, reason = "pending", "TELEGRAM_NOT_CONFIGURED"
    else:
        status = "ready"
    return {
        "status": status,
        "reason": reason,
        "digest": digest,
        "message": message,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deliver a filtered MarketRadar alert to Telegram.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the pending message without contacting Telegram.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    notification = load_json(NOTIFICATION_FILE)
    state = load_json(STATE_FILE, required=False)
    token = os.environ.get(TOKEN_ENV)
    chat_id = os.environ.get(CHAT_ID_ENV)
    decision = delivery_decision(notification, state, token, chat_id)
    now = datetime.now(timezone.utc).isoformat()

    output: dict[str, Any] = {
        "schema_version": 1,
        "generated_at_utc": now,
        "symbol": notification.get("symbol", SYMBOL),
        "status": decision["status"],
        "reason": decision["reason"],
        "notification_type": notification.get("notification_type", "NONE"),
        "priority": notification.get("priority", "none"),
        "message_digest": decision["digest"],
        "telegram_message_id": None,
    }

    if args.dry_run and decision["status"] in {"ready", "pending"}:
        output["status"] = "dry_run"
        output["reason"] = "DRY_RUN"
        print("TELEGRAM_NOTIFIER: DRY_RUN")
        print("\n" + decision["message"])
    elif decision["status"] == "ready":
        atomic_write_json(STATE_FILE, dict(state, pending_digest=decision['digest'], attempt_started_at_utc=now))
        try:
            result = send_telegram(
                str(token),
                str(chat_id),
                decision["message"],
                reply_markup=build_inline_keyboard(notification),
            )
        except (OSError, TimeoutError, urllib.error.URLError, ValueError, RuntimeError) as exc:
            output["status"] = "failed"
            output["reason"] = "DELIVERY_FAILED"
            output["error"] = type(exc).__name__
            atomic_write_json(DELIVERY_FILE, output)
            print(f"TELEGRAM_NOTIFIER: UNKNOWN | {type(exc).__name__}; no automatic retry")
            return 1
        output["status"] = "sent"
        output["reason"] = "DELIVERED"
        output["telegram_message_id"] = result.get("message_id")
        atomic_write_json(STATE_FILE, {
            "updated_at_utc": now,
            "last_sent_digest": decision["digest"],
            "last_message_id": result.get("message_id"),
        })
        print(f"TELEGRAM_NOTIFIER: SENT | message_id={result.get('message_id')}")
    elif decision["status"] == "pending":
        print("TELEGRAM_NOTIFIER: NOT_CONFIGURED — alert retained in notification file")
    else:
        print(f"TELEGRAM_NOTIFIER: SKIPPED | reason={decision['reason']}")

    atomic_write_json(DELIVERY_FILE, output)
    print(f"TELEGRAM_DELIVERY_SAVED: {DELIVERY_FILE}")
    print("TELEGRAM_NOTIFIER_ENGINE: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
