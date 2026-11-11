"""Bind the source access journey to the shared API step library."""
from pathlib import Path

import pytest
from pytest_bdd import scenarios

from tests.bdd.world import World, target_url
from tests.test_invoice_extraction_worker import worker_tenant as worker_tenant

from tests.test_declaration_import_api import enable_declaration_imports as enable_declaration_imports, isolated_document_store as isolated_document_store

scenarios(str(Path(__file__).resolve().parents[3] / "features" / "source_access.feature"))


@pytest.fixture
def world(bdd_loop, worker_tenant, client_unauth):
    admin, identity = worker_tenant
    from tests.ai_settings_support import configure_ai
    configure_ai(identity["org_a_id"])
    return World(bdd_loop, [admin], client_unauth, primary_organization_alias="Verwaltung Alpha")


pytestmark = pytest.mark.skipif(bool(target_url()), reason="Activation rehearsal requires an isolated disposable organization")
