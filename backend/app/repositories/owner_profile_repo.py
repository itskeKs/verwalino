"""OwnerProfile repository -- tenant-scoped."""

from app.models.owner_profile import OwnerProfile
from app.repositories.base import TenantRepository


class OwnerProfileRepository(TenantRepository[OwnerProfile]):
    model = OwnerProfile
