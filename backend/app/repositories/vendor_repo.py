"""Vendor repository -- tenant-scoped."""

from app.models.vendor import Vendor
from app.repositories.base import TenantRepository


class VendorRepository(TenantRepository[Vendor]):
    model = Vendor
