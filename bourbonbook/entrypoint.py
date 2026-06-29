from __future__ import annotations

import logging
import os

from bourbonbook.config import Settings
from bourbonbook.migrations import bootstrap_database


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = Settings.from_env()
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
