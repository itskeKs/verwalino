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
ROUTERS = {'app.api.economic_plans', 'app.api.payments', 'app.api.weg_settlements'}
MUTATIONS = {'POST', 'PUT', 'PATCH', 'DELETE'}


def database_dependencies(dependant):
    for dependency in dependant.dependencies:
        if dependency.call is get_db:
            yield dependency
        yield from database_dependencies(dependency)


def mutation_routes():
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        if isinstance(route, APIRoute) and context.methods & MUTATIONS:
            yield context, route


def deferred_direct_writes():
    """Inspection reused for the PR inventory; do not expand this issue's scope."""
    return sorted(
        f'{method} {context.path}'
        for context, route in mutation_routes()
        if route.endpoint.__module__ not in ROUTERS
        and any(dependency.call is get_db and dependency.scope != 'function'
                for dependency in route.dependant.dependencies)
        for method in context.methods & MUTATIONS
    )


@pytest.mark.parametrize('guard', WRITE_GUARDS)
def test_write_guard_finishes_inner_transaction_before_response(guard):
    dependencies = list(database_dependencies(get_dependant(path='/', call=guard)))
    assert len(dependencies) == 1
    assert dependencies[0].scope == 'function'


def test_member_guard_keeps_request_scoped_transaction():
    assert inspect.signature(get_live_member_db).parameters['db'].default.scope is None


def test_existing_write_guard_callers_do_not_split_cached_database_sessions():
    def uses_guard(dependant):
        return dependant.call in WRITE_GUARDS or any(uses_guard(child) for child in dependant.dependencies)

    checked = []
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        if isinstance(route, APIRoute) and uses_guard(route.dependant):
            dependencies = list(database_dependencies(route.dependant))
            assert dependencies and all(d.scope == 'function' and d.use_cache for d in dependencies), context.path
            checked.append(context.path)
    assert checked


def test_every_target_mutation_finishes_its_transaction_before_response():
    checked, failures = [], []
    for context, route in mutation_routes():
        if route.endpoint.__module__ not in ROUTERS:
            continue
        key = f'{sorted(context.methods & MUTATIONS)} {context.path}'
        checked.append(key)
        dependencies = list(database_dependencies(route.dependant))
        if not dependencies or any(dependency.scope != 'function' for dependency in dependencies):
            failures.append(key)
    assert checked
    assert not failures, '\n'.join(failures)
