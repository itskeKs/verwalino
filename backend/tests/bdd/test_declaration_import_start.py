"""Bind the declaration_import_start journey to the shared step library."""
from pathlib import Path

from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / "features/declaration_import_start.feature"))
