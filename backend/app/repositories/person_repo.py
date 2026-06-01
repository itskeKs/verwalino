"""Person repository -- tenant-scoped."""

from app.models.person import Person
from app.repositories.base import TenantRepository


class PersonRepository(TenantRepository[Person]):
    model = Person
