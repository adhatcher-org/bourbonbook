from __future__ import annotations

import logging
import os

from bourbonbook.config import Settings
from bourbonbook.migrations import bootstrap_database


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    bootstrap_database(Settings.from_env())
    os.execvp(
        "uvicorn",
        [
            "uvicorn",
            "bourbonbook.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--proxy-headers",
        ],
    )


if __name__ == "__main__":
    main()
