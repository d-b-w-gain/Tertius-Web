from uuid import uuid4

import pytest
from fastapi import HTTPException

from core.auth_types import AuthContext
from core.workbench_access import (
    SITE_WORKBENCH_ROLE,
    STRUCTURAL_WORKBENCH_ROLE,
    enabled_workbenches,
    require_site_workbench,
    require_structural_workbench,
)


def _context(*roles: str) -> AuthContext:
    return AuthContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        keycloak_subject="keycloak-user",
        email="user@example.com",
        roles=frozenset(roles),
    )


def test_enabled_workbenches_maps_only_known_keycloak_roles():
    assert enabled_workbenches(
        {SITE_WORKBENCH_ROLE, STRUCTURAL_WORKBENCH_ROLE, "unrelated-role"}
    ) == ("site", "structural")
    assert enabled_workbenches({"unrelated-role"}) == ()


def test_workbench_dependencies_enforce_independent_roles():
    site_context = _context(SITE_WORKBENCH_ROLE)
    structural_context = _context(STRUCTURAL_WORKBENCH_ROLE)

    assert require_site_workbench(site_context) is site_context
    assert require_structural_workbench(structural_context) is structural_context

    with pytest.raises(HTTPException) as site_denied:
        require_site_workbench(structural_context)
    with pytest.raises(HTTPException) as structural_denied:
        require_structural_workbench(site_context)

    assert site_denied.value.status_code == 403
    assert structural_denied.value.status_code == 403
