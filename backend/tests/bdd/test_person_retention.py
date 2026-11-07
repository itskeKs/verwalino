"""Bind the repayment-recipient retention journey."""
from pathlib import Path
from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / 'features/person_retention.feature'))
