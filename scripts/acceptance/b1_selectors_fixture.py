"""Add bounded synthetic selector data only to the explicitly disposable DB."""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
import psycopg2
from app.core.config import settings
from app.core.declaration_test_database import require_disposable_database_settings

_, _, url = require_disposable_database_settings(settings)
with psycopg2.connect(url) as connection:
    with connection.cursor() as cursor:
        cursor.execute('SELECT u.organization_id FROM "user" u JOIN organization o ON o.id=u.organization_id WHERE u.email=%s AND o.name=%s', ('admin@alpha.de', 'Verwaltung Alpha'))
        result = cursor.fetchone()
        if result is None:
            raise SystemExit('Run the disposable pytest seed first; synthetic Alpha is missing.')
        organization = uuid.UUID(str(result[0]))
        for index in range(55):
            ident = uuid.uuid5(organization, f'b1-selector-acceptance:{index}')
            cursor.execute("INSERT INTO property(id,organization_id,name,street,zip_code,city,management_type) VALUES (%s,%s,%s,'','','','MIET') ON CONFLICT(id) DO NOTHING", (str(ident), str(organization), 'Paging-selector-acceptance'))
print('Synthetic selector fixture ready: 55 equal-name properties.')
