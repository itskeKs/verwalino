"""Bind hosted setup to the shared API step library."""
from pathlib import Path

from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / "features/hosted_setup.feature"))
