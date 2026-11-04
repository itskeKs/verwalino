"""Binds the recipient wording scenario; steps live in reports.py."""
from pathlib import Path

from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / "features" / "pdf_recipient_wording.feature"))
