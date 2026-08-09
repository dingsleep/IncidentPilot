from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request

from incidentpilot.api.auth import ActorContext
from incidentpilot.api.errors import ApiProblem
from incidentpilot.runtime.lifespan import ApiRuntime
from incidentpilot.runtime.unit_of_work import UnitOfWork


def get_runtime(request: Request) -> ApiRuntime:
    return request.app.state.runtime


def get_actor(request: Request) -> ActorContext:
    return get_runtime(request).auth.authenticate(request.headers)


def require_role(request: Request, minimum: str) -> ActorContext:
    actor = get_actor(request)
    levels = {"viewer": 1, "operator": 2, "admin": 3}
    if levels[actor.role] < levels[minimum]:
        raise ApiProblem(
            status=403,
            code="ROLE_FORBIDDEN",
            title="Forbidden",
            detail="The actor does not have permission for this operation.",
        )
    return actor


async def get_unit_of_work(
    runtime: Annotated[ApiRuntime, Depends(get_runtime)],
) -> AsyncIterator[UnitOfWork]:
    async with UnitOfWork(runtime.database) as unit_of_work:
        yield unit_of_work
