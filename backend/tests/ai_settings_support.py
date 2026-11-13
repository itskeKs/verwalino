"""Synthetic organization configuration for tests that exercise AI readers."""
from contextlib import closing
import psycopg2
from app.services.ai_settings_service import _cipher
from tests.conftest import MIGRATOR_SYNC_URL


def configure_ai(organization_id, **updates):
    api_key = updates.pop("api_key", "synthetic-test-key")
    confirm = updates.pop("confirm", True)
    with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as db:
        with db, db.cursor() as cursor:
            cursor.execute('SELECT provider,model,import_enabled,qa_enabled,monthly_limit_usd FROM organization_ai_settings WHERE organization_id=%s', (str(organization_id),))
            row = cursor.fetchone()
            values = dict(zip(('provider','model','import_enabled','qa_enabled','monthly_limit_usd'), row)) if row else {
                'provider': 'openai', 'model': 'gpt-5.6-luna', 'import_enabled': True, 'qa_enabled': True, 'monthly_limit_usd': None,
            }
            values.update(updates)
            encrypted = _cipher(organization_id).encrypt(api_key.encode()).decode()
            cursor.execute('''INSERT INTO organization_ai_settings
              (organization_id,provider,model,import_enabled,qa_enabled,monthly_limit_usd,encrypted_api_key)
              VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(organization_id) DO UPDATE SET
              provider=excluded.provider,model=excluded.model,import_enabled=excluded.import_enabled,
              qa_enabled=excluded.qa_enabled,monthly_limit_usd=excluded.monthly_limit_usd,encrypted_api_key=excluded.encrypted_api_key''',
              (str(organization_id),values['provider'],values['model'],values['import_enabled'],values['qa_enabled'],values['monthly_limit_usd'],encrypted))
            if confirm:
                cursor.execute('SELECT provider, base_url FROM organization_ai_settings WHERE organization_id=%s', (str(organization_id),))
                provider, base_url = cursor.fetchone()
                cursor.execute('''SELECT c.id FROM organization_ai_confirmation c WHERE c.organization_id=%s
                  AND c.provider=%s AND c.base_url IS NOT DISTINCT FROM %s LIMIT 1''', (str(organization_id), provider, base_url))
                if cursor.fetchone() is None:
                    cursor.execute('SELECT id FROM "user" WHERE organization_id=%s ORDER BY created_at LIMIT 1', (str(organization_id),))
                    user_row = cursor.fetchone()
                    if user_row is None:
                        raise RuntimeError('configure_ai requires an organization user to confirm')
                    cursor.execute('''INSERT INTO organization_ai_confirmation
                      (organization_id,confirmed_by_user_id,provider,base_url,text_version)
                      VALUES (%s,%s,%s,%s,'1')''',
                      (str(organization_id), str(user_row[0]), provider, base_url))
