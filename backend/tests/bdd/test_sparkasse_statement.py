"""Automated twin of the Sparkasse import checks in acceptance journey 2."""
from pathlib import Path

from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / "features" / "journeys" / "sparkasse_statement.feature"))
