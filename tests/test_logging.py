from __future__ import annotations

import json
import logging
import sys

from bourbonbook.config import Settings
from bourbonbook.logging_config import (
    JsonFormatter,
    RedactionFilter,
    configure_logging,
    request_id_var,
)


def test_json_logs_include_request_id_and_redact_sensitive_fields() -> None:
    token = request_id_var.set("request-1234")
    try:
        record = logging.LogRecord(
            name="bourbonbook.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="event happened",
            args=(),
            exc_info=None,
        )
        record.event = "test_event"
        record.password = "secret-canary"
        RedactionFilter().filter(record)
        payload = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)

    assert payload["event"] == "test_event"
    assert payload["request_id"] == "request-1234"
    assert payload["password"] == "[REDACTED]"
    assert "secret-canary" not in json.dumps(payload)


def test_logging_filter_redacts_args_and_formatter_handles_exceptions(tmp_path) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        name="bourbonbook.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=10,
        msg="bad %(password)s",
        args=({"password": "secret-canary"},),
        exc_info=exc_info,
    )
    record.headers = {"authorization": "secret-canary"}
    RedactionFilter().filter(record)
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "bad [REDACTED]"
    assert payload["headers"]["authorization"] == "[REDACTED]"
    assert "exception" in payload
    assert "secret-canary" not in json.dumps(payload)

    configure_logging(
        Settings(
            data_dir=tmp_path,
            database_url=f"sqlite:///{tmp_path / 'test.db'}",
            session_secret="test-secret-that-is-long-enough",
            secure_cookies=False,
            ollama_url="http://ollama.invalid",
            ollama_model="test",
            max_users=10,
            max_upload_mb=2,
            log_format="json",
        )
    )
    root = logging.getLogger()
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
    assert isinstance(root.handlers[1].formatter, JsonFormatter)

    logging.getLogger("bourbonbook.test").info(
        "file event", extra={"event": "file_event", "password": "secret-canary"}
    )
    for handler in root.handlers:
        handler.flush()
    payload = json.loads((tmp_path / "logs" / "bourbonbook.log").read_text().strip())
    assert payload["event"] == "file_event"
    assert payload["password"] == "[REDACTED]"
    assert "secret-canary" not in json.dumps(payload)
