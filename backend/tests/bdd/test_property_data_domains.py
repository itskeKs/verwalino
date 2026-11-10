"""Bind the property_data_domains journey to the shared step library."""
from pathlib import Path

from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / "features/property_data_domains.feature"))
