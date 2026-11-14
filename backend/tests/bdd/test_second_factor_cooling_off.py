"""Bind the hosted second-factor enrolment during the cooling-off period."""
from pathlib import Path
from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / 'features/second_factor_cooling_off.feature'))
