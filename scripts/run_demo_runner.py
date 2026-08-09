from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    uvicorn.run("incidentpilot.demo.runtime:app", host="0.0.0.0", port=8300)  # noqa: S104
