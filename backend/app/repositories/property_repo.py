"""Property repository -- tenant-scoped."""

from app.models.property import Property
from app.repositories.base import TenantRepository


class PropertyRepository(TenantRepository[Property]):
    model = Property
