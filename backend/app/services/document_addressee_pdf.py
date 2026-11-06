"""Combine individual addressed copies without changing legacy render output."""
from copy import copy
from datetime import date
from html import escape


def addressed_copies(document, lines, names, render, *, plan=False):
    recipients = (document.snapshot_payload or {}).get('addressees')
    if not recipients:
        return None
    legacy = copy(document)
    legacy.snapshot_payload = {k: v for k, v in document.snapshot_payload.items() if k != 'addressees'}
    bodies, head = [], ''
    for recipient in recipients:
        selected = [line for line in lines if not plan or line.line_type != 'OWNER_ADVANCE'
            or (str(line.unit_id) == recipient['unit_id'] and str(line.owner_profile_id) == recipient['owner_profile_id'])]
        page = render(legacy, selected, names)
        head, body = page.split('<body>', 1)
        body = body.split('</body>', 1)[0]
        day = date.fromisoformat(recipient['key_date']).strftime('%d.%m.%Y')
        address = (f"<div class='meta'><strong>{escape(recipient['owner_name'])}</strong><br>"
            f"Einheit {escape(recipient['unit_number'])}<br>Eigentümer am {day}</div>")
        bodies.append("<section style='break-before: page'>" + address + body + '</section>')
    return head + '<body>' + ''.join(bodies) + '</body></html>'
