"""Bind the real-browser mobile navigation specification."""
from pathlib import Path

from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / "features/mobile_navigation.feature"))
