import hashlib
import hmac
import json
import unittest
from datetime import datetime, timezone

from control_plane.contracts import TriggerType
from control_plane.triggers import cli_event, cron_event, webhook_event


class TriggerTests(unittest.TestCase):
    def test_cli_and_cron_normalise_to_events(self):
        self.assertEqual(cli_event("analyze 600519").trigger, TriggerType.CLI)
        event = cron_event("daily-scan", "scan", scheduled_at=datetime(2026, 8, 3, tzinfo=timezone.utc))
        self.assertEqual(event.trigger, TriggerType.CRON)
        self.assertEqual(event.metadata["job_name"], "daily-scan")

    def test_webhook_verifies_signature(self):
        payload = {"content": "analyze 600519", "model": "strong"}
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(b"secret", raw, hashlib.sha256).hexdigest()
        self.assertEqual(webhook_event(payload, signature, "secret").trigger, TriggerType.WEBHOOK)
        with self.assertRaises(PermissionError):
            webhook_event(payload, "bad", "secret")
