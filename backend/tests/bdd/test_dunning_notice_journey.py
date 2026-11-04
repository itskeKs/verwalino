"""Bind the responsive dunning notice journey to the shared browser steps."""
from pathlib import Path

from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / "features" / "journeys" / "dunning_notice.feature"))
