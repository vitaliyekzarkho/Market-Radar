from __future__ import annotations

import unittest

from telegram_notifier import (
    ECONOMIC_CALENDAR_URL,
    TRADINGVIEW_CHART_URL,
    build_inline_keyboard,
    delivery_decision,
    message_digest,
    validate_notification,
)


def notification(should_send: bool = True, message: str = "GOLD | TEST") -> dict:
    return {"should_send": should_send, "message": message}


class TelegramNotifierTests(unittest.TestCase):
    def test_no_alert_is_skipped(self) -> None:
        result = delivery_decision(notification(False), {}, "token", "chat")
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "NO_ALERT")

    def test_empty_alert_is_skipped(self) -> None:
        result = delivery_decision(notification(True, ""), {}, "token", "chat")
        self.assertEqual(result["reason"], "EMPTY_MESSAGE")

    def test_long_message_is_rejected(self) -> None:
        valid, reason = validate_notification(notification(True, "x" * 4097))
        self.assertFalse(valid)
        self.assertEqual(reason, "MESSAGE_TOO_LONG")

    def test_missing_credentials_retains_pending_alert(self) -> None:
        result = delivery_decision(notification(), {}, None, None)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["reason"], "TELEGRAM_NOT_CONFIGURED")

    def test_configured_alert_is_ready(self) -> None:
        result = delivery_decision(notification(), {}, "token", "chat")
        self.assertEqual(result["status"], "ready")

    def test_duplicate_is_skipped(self) -> None:
        message = "GOLD | TEST"
        result = delivery_decision(
            notification(message=message),
            {"last_sent_digest": message_digest(message)},
            "token",
            "chat",
        )
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "DUPLICATE_MESSAGE")

    def test_digest_is_deterministic(self) -> None:
        self.assertEqual(message_digest("same"), message_digest("same"))
        self.assertNotEqual(message_digest("same"), message_digest("different"))

    def test_default_keyboard_has_chart_and_calendar(self) -> None:
        keyboard = build_inline_keyboard({"symbol": "GOLD.pro"})
        buttons = [button for row in keyboard["inline_keyboard"] for button in row]
        self.assertEqual(buttons[0], {"text": "📈 Open chart", "url": TRADINGVIEW_CHART_URL})
        self.assertIn({"text": "📅 Calendar", "url": ECONOMIC_CALENDAR_URL}, buttons)
        self.assertNotIn("📰 Drivers", [button["text"] for button in buttons])

    def test_driver_button_appears_for_verified_link(self) -> None:
        keyboard = build_inline_keyboard({
            "links": {"driver_url": "https://example.com/verified-driver"},
        })
        buttons = [button for row in keyboard["inline_keyboard"] for button in row]
        self.assertIn(
            {"text": "📰 Drivers", "url": "https://example.com/verified-driver"},
            buttons,
        )

    def test_unsafe_driver_link_is_ignored(self) -> None:
        keyboard = build_inline_keyboard({"links": {"driver_url": "javascript:alert(1)"}})
        buttons = [button for row in keyboard["inline_keyboard"] for button in row]
        self.assertNotIn("📰 Drivers", [button["text"] for button in buttons])

    def test_custom_safe_urls_are_supported(self) -> None:
        keyboard = build_inline_keyboard({
            "links": {
                "chart_url": "https://example.com/chart",
                "calendar_url": "https://example.com/calendar",
            },
        })
        self.assertEqual(keyboard["inline_keyboard"][0][0]["url"], "https://example.com/chart")
        self.assertEqual(keyboard["inline_keyboard"][1][0]["url"], "https://example.com/calendar")


if __name__ == "__main__":
    unittest.main(verbosity=2)
