"""Synthetic repayment recipients with no contractual or other binding."""


async def repayment_recipient(api, property_id, *, role='tenant', value_date='2026-02-01', book=True):
    async def create(path, body):
        response = await api.post(path, json=body)
        assert response.status_code == 201, response.text
        return response.json()

    person = await create('/api/persons', {'first_name': 'Rita', 'last_name': 'Rückzahlung'})
    profile = await create(f'/api/{role}-profiles', {'person_id': person['id']})
    bank = await create('/api/bank-accounts', {'property_id': str(property_id),
        'account_type': 'LAUFEND', 'label': 'Synthetic repayment account'})
    payment = await create('/api/payments', {'bank_account_id': bank['id'], 'direction': 'OUT',
        'amount': '125.00', 'value_date': value_date, f'{role}_profile_id': profile['id'],
        'payment_type': 'KALTMIETE' if role == 'tenant' else 'SONDERZAHLUNG',
        'period_year': int(value_date[:4]), 'period_month': 2})
    if book:
        response = await api.post(f"/api/payments/{payment['id']}/book")
        assert response.status_code == 200, response.text
        payment = response.json()
    return person, payment
