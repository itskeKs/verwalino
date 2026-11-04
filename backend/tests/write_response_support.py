"""Observe committed rows and immediate detail reads before response cleanup."""
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text


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
