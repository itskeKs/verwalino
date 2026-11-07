from pathlib import Path

from pytest_bdd import scenarios

features = Path(__file__).resolve().parents[3] / 'features'
scenarios(str(features / 'advance_split.feature'), str(features / 'journeys' / 'rent_change_settlement.feature'))
