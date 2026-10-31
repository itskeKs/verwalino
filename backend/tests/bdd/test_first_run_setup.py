"""Bind the first-run setup specification to the shared API steps."""
from pathlib import Path

from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / "features/first_run_setup.feature"))
