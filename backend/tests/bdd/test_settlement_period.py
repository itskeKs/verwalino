from pathlib import Path
from pytest_bdd import scenarios

FEATURES = Path(__file__).resolve().parents[3] / "features"

scenarios(str(FEATURES / "settlement_period.feature"), str(FEATURES / "journeys" / "operating_cost_draft.feature"))
