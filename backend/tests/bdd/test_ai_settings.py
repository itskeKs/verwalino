"""Bind the organization AI settings journey."""
from pathlib import Path
from pytest_bdd import scenarios
import pytest
from tests.bdd.world import target_url

pytestmark = pytest.mark.skipif(bool(target_url()), reason='Fake model discovery requires the in-process local app; live settings acceptance is separate.')

scenarios(str(Path(__file__).resolve().parents[3] / "features" / "ai_settings.feature"))
