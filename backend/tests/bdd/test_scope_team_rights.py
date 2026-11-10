"""Bind the scope_team_rights journey to the shared step library."""
from pathlib import Path

from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / "features/scope_team_rights.feature"))
