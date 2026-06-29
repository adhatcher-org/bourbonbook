from __future__ import annotations

import os

from bourbonbook.config import Settings
from bourbonbook.logging_config import configure_logging
from bourbonbook.migrations import bootstrap_database


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings)
    settings.validate_identity()
    bootstrap_database(settings)
    proxy_args = (
        ["--proxy-headers", "--forwarded-allow-ips", settings.forwarded_allow_ips]
        if settings.proxy_headers
        else ["--no-proxy-headers"]
    )
    os.execvp(
        "uvicorn",
        [
            "uvicorn",
            "bourbonbook.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--no-access-log",
            *proxy_args,
        ],
    )


if __name__ == "__main__":
    main()
