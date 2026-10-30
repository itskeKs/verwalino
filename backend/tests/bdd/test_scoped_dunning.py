"""Bind features/scoped_dunning.feature to the shared steps."""
from pathlib import Path

from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / "features" / "scoped_dunning.feature"))
