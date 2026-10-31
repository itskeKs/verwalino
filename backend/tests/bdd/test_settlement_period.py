from pathlib import Path
from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / "features" / "settlement_period.feature"))
