"""Versioned hosted legal documents: one source of truth for sign-up assent.

The version identifiers match the versioned documents on the public website;
the sign-up page links to exactly these versions and the request must carry
them, so an outdated page is refused instead of recording a stale assent.
"""

TERMS_VERSION = "2026-11-13"
AV_VERTRAG_VERSION = "2026-11-13"

# Public website of the hosted offering; the legal documents live there,
# not in the app, and are reviewed and released independently (issue #26).
PUBLIC_LEGAL_BASE = "https://verwalino.de"
TERMS_URL = f"{PUBLIC_LEGAL_BASE}/nutzungsbedingungen/"
AV_VERTRAG_URL = f"{PUBLIC_LEGAL_BASE}/av-vertrag/"

DOCUMENTS = {
    "terms": {"version": TERMS_VERSION, "url": TERMS_URL},
    "av_vertrag": {"version": AV_VERTRAG_VERSION, "url": AV_VERTRAG_URL},
}
