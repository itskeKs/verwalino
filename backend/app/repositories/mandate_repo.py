"""ManagementMandate repository -- tenant-scoped."""

from app.models.management_mandate import ManagementMandate
from app.repositories.base import TenantRepository


class MandateRepository(TenantRepository[ManagementMandate]):
    model = ManagementMandate
