from pathlib import Path
from pytest_bdd import scenarios

scenarios(str(Path(__file__).resolve().parents[3] / 'features' / 'bank_account_scope_assignment.feature'))
