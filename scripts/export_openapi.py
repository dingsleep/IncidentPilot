"""Export the canonical FastAPI schema consumed by the web client."""

from __future__ import annotations

import json
from pathlib import Path

from incidentpilot.api.main import create_app


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "web" / "openapi.json"
    target.write_text(
        json.dumps(create_app().openapi(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
