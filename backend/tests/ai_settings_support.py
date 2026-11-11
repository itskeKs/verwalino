"""Synthetic organization configuration for tests that exercise AI readers."""
from contextlib import closing
import psycopg2
from app.services.ai_settings_service import _cipher
from tests.conftest import MIGRATOR_SYNC_URL


def configure_ai(organization_id, **updates):
    api_key = updates.pop("api_key", "synthetic-test-key")
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
