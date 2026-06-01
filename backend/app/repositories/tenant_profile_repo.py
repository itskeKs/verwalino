"""TenantProfile repository -- tenant-scoped."""

from app.models.tenant_profile import TenantProfile
from app.repositories.base import TenantRepository


class TenantProfileRepository(TenantRepository[TenantProfile]):
    model = TenantProfile
