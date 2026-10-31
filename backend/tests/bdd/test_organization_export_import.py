"""Bind the portable organization archive journey."""
from pathlib import Path
from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / "features/organization_export_import.feature"))
