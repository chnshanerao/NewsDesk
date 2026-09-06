import json
import logging
import unittest

from newsdesk.ops import JsonFormatter


class StructuredLoggingTests(unittest.TestCase):
    def test_json_formatter_preserves_event_fields(self):
        record = logging.LogRecord("newsdesk", logging.INFO, __file__, 1,
                                   "ready", (), None)
        record.event = "service_ready"
        record.fields = {"port": 8899, "healthy": True}
        payload = json.loads(JsonFormatter().format(record))
        self.assertEqual(payload["event"], "service_ready")
        self.assertEqual(payload["port"], 8899)
        self.assertTrue(payload["healthy"])
