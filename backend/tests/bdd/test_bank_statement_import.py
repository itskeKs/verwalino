"""Bind the bank import and payment counterparty features."""

from pathlib import Path

from pytest_bdd import scenarios

FEATURE = Path(__file__).resolve().parents[3] / "features" / "bank_statement_import.feature"

scenarios(str(FEATURE), str(FEATURE.with_name("payment_counterparty.feature")))
