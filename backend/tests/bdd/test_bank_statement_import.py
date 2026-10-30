"""Binds features/bank_statement_import.feature; the steps live in tests/bdd/steps/."""

from pathlib import Path

from pytest_bdd import scenarios

FEATURE = Path(__file__).resolve().parents[3] / "features" / "bank_statement_import.feature"

scenarios(str(FEATURE))
