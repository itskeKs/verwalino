"""Bind the real-browser inbox journey with offline providers (issue #501)."""
from pathlib import Path

import pytest
from pytest_bdd import scenarios

from tests.bdd.world import target_url

pytestmark = pytest.mark.skipif(bool(target_url()), reason='The worker with its in-process fake providers needs the local disposable database; live provider acceptance is separate.')

scenarios(str(Path(__file__).resolve().parents[3] / "features" / "journeys" / "inbox_model_processing.feature"))
