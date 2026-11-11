"""Bind the property_data_domains journey to the shared step library."""
from pathlib import Path

import pytest
from pytest_bdd import scenarios

from tests.bdd.world import target_url

pytestmark = pytest.mark.skipif(bool(target_url()), reason='The clerk of the local seed data does not exist on an external installation, which has only its administrator.')

scenarios(str(Path(__file__).resolve().parents[3] / "features/property_data_domains.feature"))
