"""Bind the browser import journey to the shared step library."""
from pathlib import Path

from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / "features" / "bank_statement_browser.feature"))
