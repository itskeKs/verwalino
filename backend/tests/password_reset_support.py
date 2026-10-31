"""Reset mail capture without network transport."""
import re
from email import policy
from email.parser import BytesParser
from urllib.parse import parse_qs, urlsplit

from tests.signup_support import signup_mail  # noqa: F401

MESSAGE = {'message': 'Wenn ein Konto zu dieser Adresse besteht, erhalten Sie eine E-Mail zum Zurücksetzen Ihres Passworts.'}
REFUSED = {'detail': 'Zurücksetzen nicht möglich. Bitte fordern Sie einen neuen Link an.'}
NEW_PASSWORD = 'NewPassword456!'


def reset_token(mail):
    message = BytesParser(policy=policy.default).parsebytes(mail.messages[-1][0])
    assert message['Subject'] == 'Verwalino: Passwort zurücksetzen'
    body = message.get_content()
    assert '1 Stunde' in body
    link = re.search(r'https://signup.example.test/passwort-zuruecksetzen\?token=\S+', body)[0]
    return parse_qs(urlsplit(link).query)['token'][0]
