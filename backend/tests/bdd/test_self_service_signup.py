"""Bind the hosted signup specification."""
from pathlib import Path
from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / 'features/self_service_signup.feature'))
