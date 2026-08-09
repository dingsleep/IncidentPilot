from __future__ import annotations

from typing import Protocol


class RecoverableJobQueue(Protocol):
    async def recover_expired(self) -> int: ...


async def recover_expired_jobs(queue: RecoverableJobQueue) -> int:
    return await queue.recover_expired()
