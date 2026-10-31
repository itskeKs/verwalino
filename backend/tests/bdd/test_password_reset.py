"""Bind the password reset specification in either deployment mode."""
from pathlib import Path
from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / 'features/password_reset.feature'))
