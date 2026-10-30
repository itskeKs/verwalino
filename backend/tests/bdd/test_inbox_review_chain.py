"""Binds the inbox review chain; shared steps live in steps/."""
from pathlib import Path

from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / "features" / "inbox_review_chain.feature"))
