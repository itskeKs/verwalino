"""Bind the income regression from acceptance journey 5."""
from pathlib import Path

from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / 'features/journeys/annual_income.feature'))
