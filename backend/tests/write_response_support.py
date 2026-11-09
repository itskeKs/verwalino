"""Observe committed rows and immediate detail reads before response cleanup."""
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text


async def request_without_open_transaction(api, observer, method, path, payload, headers=None):
    from app.main import app

    query = text("SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a USING (pid) "
                 "WHERE l.locktype='transactionid' AND a.usename IN ('app_rw','app_review') "
                 "AND l.pid <> pg_backend_pid()")
    observations = []

    async def observe_response(scope, receive, send):
        async def observe_send(message):
            if (message['type'] == 'http.response.start'
                    and 200 <= message['status'] < 300 and not observations):
                await observer.execute(text('SELECT pg_stat_clear_snapshot()'))
                observations.append(await observer.scalar(query))
            await send(message)
        await app(scope, receive, observe_send)

    async with AsyncClient(transport=ASGITransport(app=observe_response),
                           base_url='http://test', cookies=api.cookies) as client:
        response = await client.request(method, path, json=payload, headers=headers)
    message = f'{method} {path}: observed transaction lock counts {observations} at http.response.start'
    assert len(observations) == 1, f'{message}; response {response.status_code}: {response.text}'
    assert observations[0] == 0, message
    return response


async def create_visible_draft(api, observer, path, payload, table):
    from app.main import app

    # Table names are test constants, never HTTP input. The observer is a
    # separate app_rw connection, with the same tenant context and real RLS.
    parent = 'bank_account_id' if table == 'payment' else 'property_id'
    query = text(f"SELECT id FROM {table} WHERE {parent}=:parent")
    params = {'parent': payload[parent]}
    before = set((await observer.scalars(query, params)).all())
    visible, details = [], []

    async def observe_response(scope, receive, send):
        async def observe_send(message):
            if message['type'] == 'http.response.start' and message['status'] == 201:
                ids = set((await observer.scalars(query, params)).all()) - before
                visible.extend(str(row_id) for row_id in ids)
                for row_id in visible:
                    details.append(await api.get(f"{path}/{row_id}"))
            await send(message)
        await app(scope, receive, observe_send)

    async with AsyncClient(transport=ASGITransport(app=observe_response),
                           base_url='http://test', cookies=api.cookies) as client:
        response = await client.post(path, json=payload)
    assert response.status_code == 201, response.text
    assert visible == [response.json()['id']], f'{path}: row not committed at http.response.start'
    assert len(details) == 1 and details[0].status_code == 200, [r.text for r in details]
    assert details[0].json()['id'] == response.json()['id']
    return response
