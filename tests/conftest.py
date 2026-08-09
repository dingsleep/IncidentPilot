from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Mapping
from typing import Any


def pytest_asyncio_loop_factories(
    config: Any,
    item: Any,
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    del config, item
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}
