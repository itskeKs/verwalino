"""Bind the scoped_statements journey to the shared step library."""
from pathlib import Path

import pytest
from pytest_bdd import scenarios

from tests.bdd.world import target_url

pytestmark = pytest.mark.skipif(bool(target_url()), reason="Creates immutable scopes and booked payments that pile up in the shared organization of an external installation and break its org-wide lists (the scope selector page, the payments order); it runs against the local disposable database and in PR CI's browser job.")

scenarios(str(Path(__file__).resolve().parents[3] / "features/scoped_statements.feature"))
