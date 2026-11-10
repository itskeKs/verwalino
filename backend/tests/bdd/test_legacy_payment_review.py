"""Bind the legacy_payment_review journey to the shared step library."""
from pathlib import Path

from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / "features/legacy_payment_review.feature"))
