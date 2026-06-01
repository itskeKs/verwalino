"""Role-based access control (RBAC) -- architecture.md section 4.

Roles are a fixed enum (SDD section 3 -- no fine-grained permission system):
  OWNER_ADMIN  -- full access including user/role management
  CLERK        -- operative access (create/edit data, no user management)
  READ_ONLY    -- read-only (e.g. Beirat, auditor)

The RBAC layer is orthogonal to tenant isolation:
  - RLS ensures *which* organization is visible.
  - RBAC ensures *what* a user may do within that organization.
"""

from __future__ import annotations

import enum
from typing import Sequence

from fastapi import HTTPException, status


class Role(str, enum.Enum):
    OWNER_ADMIN = "OWNER_ADMIN"
    CLERK = "CLERK"
    READ_ONLY = "READ_ONLY"


def require_role(current_role: str, allowed: Sequence[Role]) -> None:
    """Raise 403 if the current role is not in the allowed set."""
    if current_role not in {r.value for r in allowed}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )


def require_admin(current_role: str) -> None:
    """Shortcut: only OWNER_ADMIN."""
    require_role(current_role, [Role.OWNER_ADMIN])


def require_write(current_role: str) -> None:
    """Shortcut: OWNER_ADMIN or CLERK."""
    require_role(current_role, [Role.OWNER_ADMIN, Role.CLERK])
