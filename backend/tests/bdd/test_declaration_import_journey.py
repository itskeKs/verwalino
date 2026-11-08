"""Bind the real-browser declaration journey with an offline provider."""
from pathlib import Path
import pytest
from pytest_bdd import scenarios
from tests.bdd.world import target_url

pytestmark = pytest.mark.skipif(bool(target_url()), reason='The deterministic extraction worker requires the local disposable database; live provider acceptance is separate.')
scenarios(str(Path(__file__).resolve().parents[3] / 'features/journeys/declaration_import.feature'))
