"""Bind the source access journey to the shared API step library."""
from pathlib import Path

import pytest
from pytest_bdd import scenarios

from tests.bdd.world import World, target_url
from tests.test_invoice_extraction_worker import worker_tenant  # noqa: F401

from tests.test_declaration_import_api import enable_declaration_imports, isolated_document_store  # noqa: F401

scenarios(str(Path(__file__).resolve().parents[3] / "features" / "source_access.feature"))


@pytest.fixture
def world(bdd_loop, worker_tenant, client_unauth):
    admin, _ = worker_tenant
    return World(bdd_loop, [admin], client_unauth, primary_organization_alias="Verwaltung Alpha")


pytestmark = pytest.mark.skipif(bool(target_url()), reason="Activation rehearsal requires an isolated disposable organization")
