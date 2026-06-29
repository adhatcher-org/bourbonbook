from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from bourbonbook.config import Settings

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,80}$")
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

SENSITIVE_KEYS = (
    "authorization",
    "api_key",
    "apikey",
    "cookie",
    "csrf",
    "form",
    "password",
    "secret",
    "smtp_password",
    "token",
)
STANDARD_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


def current_request_id() -> str | None:
    return request_id_var.get()


def valid_request_id(value: str | None) -> bool:
    return bool(value and REQUEST_ID_PATTERN.fullmatch(value))


def _redact(key: str, value: Any) -> Any:
    if any(fragment in key.lower() for fragment in SENSITIVE_KEYS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(child_key): _redact(str(child_key), child_value)
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(key, item) for item in value]
    return value


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact("message", record.msg)
        if isinstance(record.args, dict):
            record.args = {key: _redact(str(key), value) for key, value in record.args.items()}
        elif record.args:
            record.args = tuple(_redact("arg", value) for value in record.args)
        for key, value in list(record.__dict__.items()):
            if key not in STANDARD_ATTRS:
                setattr(record, key, _redact(key, value))
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", "log"),
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None) or current_request_id()
        if request_id:
            event["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key not in STANDARD_ATTRS and key not in event:
                event[key] = _redact(key, value)
        if record.exc_info:
            event["exception"] = self.formatException(record.exc_info)
        return json.dumps(event, default=str, separators=(",", ":"))


def configure_logging(settings: Settings) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(settings.log_level.upper())
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactionFilter())
    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s [%(event)s] %(message)s",
                defaults={"event": "log"},
            )
        )
    root.addHandler(handler)
    logging.getLogger("uvicorn.access").disabled = True


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    message: str,
    **fields: Any,
) -> None:
    exc_info = fields.pop("exc_info", None)
    logger.log(level, message, exc_info=exc_info, extra={"event": event, **fields})
