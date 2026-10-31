"""Bind features/dunning_interest.feature to the shared steps."""
from pathlib import Path

from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / "features" / "dunning_interest.feature"))
