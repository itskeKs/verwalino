"""The write transaction must finish before an HTTP success can escape."""
import inspect

import pytest
from fastapi.dependencies.utils import get_dependant
from fastapi.routing import APIRoute, iter_route_contexts

from app.core.tenant import get_db
from app.core.live_principal import (
    get_live_writer_db, get_live_publication_db, get_live_admin_db, get_live_member_db,
)
from app.main import app

WRITE_GUARDS = (get_live_writer_db, get_live_publication_db, get_live_admin_db)
MUTATIONS = {'POST', 'PUT', 'PATCH', 'DELETE'}
EXEMPT: dict[str, str] = {
    'POST /api/reports/datev/preview': 'Read-only preview; get_live_member_db keeps request scope (#262).',
    'POST /api/reports/datev.csv': 'Read-only export; get_live_member_db keeps request scope (#262).',
    'POST /api/payments/sepa/preview': 'Read-only preview; get_live_member_db keeps request scope (#262).',
    'POST /api/payments/sepa.xml': 'Read-only export; get_live_member_db keeps request scope (#262).',
}


def database_dependencies(dependant):
    for dependency in dependant.dependencies:
        if dependency.call is get_db:
            yield dependency
        yield from database_dependencies(dependency)


def yielding(dependant, chain=()):
    for child in dependant.dependencies:
        path = (*chain, getattr(child.call, '__name__', repr(child.call)))
        if inspect.isasyncgenfunction(child.call) or inspect.isgeneratorfunction(child.call):
            yield path, child
        yield from yielding(child, path)


def late_transactions():
    late = {}
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        if isinstance(route, APIRoute):
            chains = ['->'.join(p) for p, d in yielding(route.dependant) if d.scope != 'function']
            for method in context.methods & MUTATIONS:
                if chains:
                    late[f'{method} {context.path}'] = chains
    return late


@pytest.mark.parametrize('guard', WRITE_GUARDS)
def test_write_guard_finishes_inner_transaction_before_response(guard):
    dependencies = list(database_dependencies(get_dependant(path='/', call=guard)))
    assert len(dependencies) == 1
    assert dependencies[0].scope == 'function'


def test_member_guard_keeps_request_scoped_transaction():
    assert inspect.signature(get_live_member_db).parameters['db'].default.scope is None


def test_no_route_opens_two_tenant_sessions():
    checked = []
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        if isinstance(route, APIRoute):
            dependencies = list(database_dependencies(route.dependant))
            if not dependencies:
                continue
            assert len({d.scope for d in dependencies}) == 1, context.path
            assert all(d.use_cache for d in dependencies), context.path
            checked.append(context.path)
    assert checked


def test_every_mutation_route_closes_its_transaction_before_the_response():
    failures = {route: chains for route, chains in late_transactions().items() if route not in EXEMPT}
    assert not failures, '\n'.join(f'{route}: {", ".join(chains)}' for route, chains in sorted(failures.items()))


def test_exemptions_name_current_late_routes():
    late = late_transactions()
    for route, reason in EXEMPT.items():
        assert route in late, f'Stale transaction-scope exemption: {route}'
        assert reason.strip(), f'Transaction-scope exemption needs a reason: {route}'
