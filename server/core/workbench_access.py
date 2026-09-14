from __future__ import annotations

from collections.abc import Iterable

from fastapi import Depends, HTTPException, status

from core.auth import get_auth_context
from core.auth_types import AuthContext


SITE_WORKBENCH = "site"
STRUCTURAL_WORKBENCH = "structural"

SITE_WORKBENCH_ROLE = "workbench-site"
STRUCTURAL_WORKBENCH_ROLE = "workbench-structural"

WORKBENCH_ROLES = {
    SITE_WORKBENCH: SITE_WORKBENCH_ROLE,
    STRUCTURAL_WORKBENCH: STRUCTURAL_WORKBENCH_ROLE,
}


def enabled_workbenches(roles: Iterable[str]) -> tuple[str, ...]:
    role_set = set(roles)
    return tuple(workbench for workbench, role in WORKBENCH_ROLES.items() if role in role_set)


def _require_workbench(ctx: AuthContext, workbench: str) -> AuthContext:
    required_role = WORKBENCH_ROLES[workbench]
    if required_role not in ctx.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{workbench.title()} workbench access required",
        )
    return ctx


def require_site_workbench(
    ctx: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    return _require_workbench(ctx, SITE_WORKBENCH)


def require_structural_workbench(
    ctx: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    return _require_workbench(ctx, STRUCTURAL_WORKBENCH)
