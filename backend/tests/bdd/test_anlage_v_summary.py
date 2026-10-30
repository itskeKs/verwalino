"""Bind the user-level Anlage V summary specification."""
from pathlib import Path

from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / 'features/anlage_v_summary.feature'))
