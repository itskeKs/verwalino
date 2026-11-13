"""Bind the second-factor journey to the shared HTTP steps."""
from pathlib import Path

from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / "features/second_factor.feature"))
