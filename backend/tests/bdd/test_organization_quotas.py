"""Bind the hosted quota boundary and isolation journeys."""
from pathlib import Path
from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / "features/organization_quotas.feature"))
