"""Small zero-dependency structured logging helpers for operations."""
import json
import logging
import os
import sys
import time


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": getattr(record, "event", "log"),
            "message": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if fields:
            payload.update(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def get_logger(name="newsdesk"):
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        if os.getenv("NEWSDESK_LOG_FORMAT", "json").lower() == "json":
            handler.setFormatter(JsonFormatter())
        else:
            handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(os.getenv("NEWSDESK_LOG_LEVEL", "INFO").upper())
        logger.propagate = False
    return logger


def event(logger, name, message="", level=logging.INFO, **fields):
    logger.log(level, message or name, extra={"event": name, "fields": fields})
