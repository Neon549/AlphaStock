import hashlib
import hmac
import json
import unittest

from control_plane.contracts import TriggerType
from control_plane.triggers import webhook_event


class SourceWebhookTests(unittest.TestCase):
    def test_source_webhook_uses_business_revision_identity(self):
        payload = {
            "type": "financial_report.changed",
            "source_id": "cninfo:600519:annual",
            "source_type": "financial_report",
            "source_version": "2025-annual",
            "content_hash": "abc123",
            "affected_symbols": ["600519"],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(b"secret", raw, hashlib.sha256).hexdigest()
        event = webhook_event(payload, signature, "secret")
        self.assertEqual(event.trigger, TriggerType.SOURCE_CHANGE)
        self.assertEqual(event.metadata["event_type"], "source.changed")
        self.assertEqual(event.metadata["source_version"], "2025-annual")

    def test_source_webhook_requires_a_revision_and_symbol(self):
        payload = {"type": "news.changed", "source_id": "eastmoney:news"}
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(b"secret", raw, hashlib.sha256).hexdigest()
        with self.assertRaises(ValueError):
            webhook_event(payload, signature, "secret")


if __name__ == "__main__":
    unittest.main()
